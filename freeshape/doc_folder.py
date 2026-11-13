"""FolderDocument: the folder-level Document -- a folder on disk holding `document.json` plus one file per
tab (a Part Studio, an Assembly, a Drawing or a Variable Studio). Headless -- no Qt.

Note the name clash this module works around: `freeshape.document.PartStudioDoc` is a *Part Studio*, not this
folder-level Document. That class was once called `Document` (still an alias) and keeps its `document` file
format / migration chain -- see freeshape/migrations.py; this module's class is called `FolderDocument` to stay unambiguous, and
"document.json" below always means the folder manifest, never a Part Studio file.

document.json is `{format, version, id, name, tabs: [{id, kind, file, name}, ...]}`. A tab's `id` is
generated once and never changes; it is also stamped into the tab's own file (as `_tab_id`) so it
survives a rename of that file on disk. Open does not reconcile a drifted `file` entry back to its tab; it
only adds a warning when a tab file's `_tab_id` differs from its manifest id (see document_warnings).

A Document folder may have come from someone else, so document.json is untrusted input: every tab's
`file` is checked to resolve to a real path inside the folder (no absolute path, no `..` segment, and no
escape via a symlinked directory), and neither a tab file nor document.json itself may be a symlink. A
tab that fails this check is recorded in `errors` keyed by its id and its file is never opened -- the
check runs before any attempt to read it. (The FreeCAD sources mirrored into this repo at /opt/freecad-src
are pruned down to the element-map files ADR 0003 ported, so there's no Document/project-file handling
in them to port here; writes instead reuse this repo's own existing atomic-save convention, already used
by AssemblyDoc.save and DrawingDoc.save -- a `<path>.tmp` file written alongside the target and moved
into place with `os.replace`, which never follows a symlink at the destination.)

Every read and write of the manifest, a tab's data or a blob goes through the Document's `store` (ADR 0009,
freeshape/docstore.py); the checks and the atomic write above live in `docstore.FolderStore`. Git does not:
Versions, Workspaces and merge run in the store's folder (`docstore.git_folder`).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
import weakref

from . import doc_registry as R
from . import docstore as DS
from . import gitstore as G
from . import kernel as K
from . import tabkinds as TK
from .links import LinkResolver, migrate_links_to_tab_ids, tab_sources

MANIFEST_NAME = DS.MANIFEST_NAME
FORMAT = "freeshape-document"
FORMAT_VERSION = 1

_MANIFEST_KEYS = {"format", "version", "id", "name", "tabs"}
_TAB_KEYS = {"id", "kind", "file", "name"}

# live views over the tab-kind registry (freeshape/tabkinds.py), kept for the callers that import them
EXTENSIONS = TK.EXTENSIONS
FACTORIES = TK.FACTORIES
LOADERS = TK.LOADERS


def _rmtree(path):
    shutil.rmtree(path, ignore_errors=True)


def _peek_id(folder):
    """The id in a folder's document.json, or None. Reads one regular file; runs no git and loads nothing."""
    try:
        doc_id = json.loads(DS.FolderStore(folder).read_manifest(max_bytes=1 << 20).decode("utf-8")).get("id")
    except (OSError, ValueError, AttributeError, K.KernelError):
        return None
    return doc_id if isinstance(doc_id, str) else None


def _attribute_lines():
    """`.gitattributes` for a new Document: no automatic merge of the manifest or any tab file."""
    return [f"{MANIFEST_NAME} -merge"] + [f"*{ext} -merge" for ext in TK.extensions()]


class DirtyWorkspaceError(K.KernelError):
    """A Workspace switch was refused because tabs have unsaved changes; `tabs` names them."""

    def __init__(self, tabs):
        self.tabs = list(tabs)
        super().__init__("Unsaved changes in " + ", ".join(self.tabs) + ": save or discard them before switching")


class Tab:
    """One row of document.json. `doc` is the loaded Document / AssemblyDoc / DrawingDoc once opened, or
    `None` if its file was refused -- see `FolderDocument.errors`."""

    def __init__(self, id, kind, file, name):
        self.id = id
        self.kind = kind
        self.file = file
        self.name = name
        self.doc = None
        self.extra = {}      # unknown keys on this tab's manifest entry, written back unchanged


class FolderDocument:
    def __init__(self):
        self.id = uuid.uuid4().hex
        self.name = "Document 1"
        self.path = None
        self.tabs: list[Tab] = []
        self.errors: dict[str, str] = {}
        self.extra = {}      # unknown top-level manifest keys, written back unchanged
        self.warnings: list[dict] = []   # problems found on open: see document_warnings
        self._counter = 0
        self.resolver = LinkResolver(folder=self)    # resolves tab-id links through this manifest
        self.trusted = False      # git may run in self.path: the app created it or imported it (see open_document)
        self.read_only = False    # an opened version
        self.version = None       # {name, commit, note, time} of an opened version
        self.registry = None
        self.git_error = None     # why a new Document got no repository (git missing or too old)
        self._scratch = None
        self._store = None        # the DocumentStore it was opened on, if one was given (see `store`)
        self._folder_store = None

    @property
    def store(self):
        """The DocumentStore the manifest, the tabs' data and blobs go through: the one given to `open`, else a
        docstore.FolderStore on `path` (made again when `path` changes)."""
        if self._store is not None:
            return self._store
        if self._folder_store is None or self._folder_store.folder != self.path:
            self._folder_store = DS.FolderStore(self.path)
        return self._folder_store

    def _attach(self, tab):
        """Point a tab's document at this Document's resolver (studios, assemblies and drawings hold links)."""
        if tab.kind == "studio" and tab.doc is not None:
            tab.doc.link_inputs = self.resolver.resolve_studio
            tab.doc.var_inputs = self.resolver.resolve_variables
        elif tab.kind == "assembly" and tab.doc is not None:
            tab.doc.resolver = self.resolver.resolve_source
        elif tab.kind == "drawing" and tab.doc is not None:
            tab.doc.resolver = self.resolver.resolve_drawing_source

    def remove_tab(self, tab_id):
        """Drop a tab from the manifest (its file stays on disk). Links to it then report the missing tab."""
        self._check_writable()
        tab = self.tab(tab_id)
        if tab is None:
            raise K.KernelError(f"No tab {tab_id!r} in the Document")
        self.tabs.remove(tab)
        self.errors.pop(tab_id, None)

    def _context_assembly(self, tab_id, fid):
        """(studio, the live AssemblyDoc or None) for a Context feature: the open Assembly tab named by the
        feature's assembly id (a tab id, or the uid the capture stored), so unsaved Assembly edits count."""
        tab = self.tab(tab_id)
        if tab is None or tab.kind != "studio" or tab.doc is None:
            raise K.KernelError(f"No Part Studio tab {tab_id!r}")
        f = tab.doc.feature(fid)
        want = ((f or {}).get("params") or {}).get("assembly")
        asm = next((t.doc for t in self.tabs if t.kind == "assembly" and t.doc is not None
                    and want and want in (t.id, getattr(t.doc, "uid", None))), None)
        return tab.doc, asm

    def context_status(self, tab_id, fid):
        """"current", "out of date" or "assembly missing" for Context feature `fid` of studio tab `tab_id`.
        A query: no Link edge, nothing rebuilt."""
        from .document import context_status
        doc, asm = self._context_assembly(tab_id, fid)
        return context_status(doc, fid, asm)

    def update_context(self, tab_id, fid):
        """Recapture Context feature `fid` of studio tab `tab_id` from its Assembly tab (see
        document.update_context). Raises KernelError when the Assembly tab or instance is missing."""
        from .document import update_context
        self._check_writable()
        doc, asm = self._context_assembly(tab_id, fid)
        update_context(doc, fid, asm)

    def refresh_links(self):
        """Re-read every studio's, assembly's and drawing's links against the current tabs."""
        done = set()

        def rebuild_studio(tab):      # a source studio first, so it is read as it now stands
            if tab.id in done:
                return
            done.add(tab.id)
            for src in tab_sources("studio", tab.doc):
                up = self.tab(src.get("tab"))
                if up is not None and up.kind == "studio" and up.doc is not None:
                    rebuild_studio(up)
            tab.doc.rebuild()

        for tab in self.tabs:
            if tab.kind == "studio" and tab.doc is not None and tab_sources("studio", tab.doc):
                rebuild_studio(tab)
        for tab in self.tabs:
            if tab.kind == "assembly" and tab.doc is not None:
                tab.doc.refresh(solve=False, notify=False)
            elif tab.kind == "drawing" and tab.doc is not None:
                tab.doc.refresh(notify=False)

    def _stamped_tab_id(self, tab_id):
        """The `_tab_id` a tab's data carries, or None when it has none or cannot be read."""
        try:
            v = json.loads(self.store.read_tab(tab_id).decode("utf-8")).get("_tab_id")
        except (OSError, ValueError, AttributeError, K.KernelError):
            return None
        return v if isinstance(v, str) else None

    def document_warnings(self):
        """Check the Document as it stands without refusing it: the manifest against the folder (a tab file that
        is not in the manifest, a manifest entry whose file is missing or will not open) and every link inside it
        (a link to a tab id that is not in the manifest, judged by the resolver itself). Run on every open, since a
        merge done with plain git never passes the app's merge preview. Returns dicts {problem, tab, tab_name,
        target, message}; `problem` is "missing-file", "unreadable", "not-in-manifest", "tab-id-mismatch" (a tab file stamped with a
        different tab's id than its manifest entry) or "dangling-link"."""
        out = []

        def warn(problem, message, tab=None, target=None):
            out.append({"problem": problem, "tab": tab.id if tab else None, "tab_name": tab.name if tab else None,
                        "target": target, "message": message})

        store = self.store
        listed = []
        for tab in self.tabs:
            label = f"Tab “{tab.name}” ({tab.id})"
            try:
                store.place_tab(tab.id, tab.file)
                listed.append(tab.id)
                placed = True
            except K.KernelError:
                placed = False
            if tab.doc is not None:
                stamped = self._stamped_tab_id(tab.id) if placed else None
                if stamped is not None and stamped != tab.id:
                    warn("tab-id-mismatch", f"{label}: its file “{tab.file}” is stamped with another tab's id "
                                            f"({stamped}), not {tab.id}", tab, stamped)
                continue
            if placed and not store.list_tabs([tab.id]):
                warn("missing-file", f"{label}: its file “{tab.file}” is missing from the Document folder", tab)
            else:
                warn("unreadable", f"{label} could not be opened: {self.errors.get(tab.id, 'unknown error')}", tab)
        for rel in store.strays(listed):
            warn("not-in-manifest", f"“{rel}” is a tab file in the Document folder but is not listed in "
                                    f"{MANIFEST_NAME}", target=rel)
        for tab in self.tabs:
            if tab.doc is None:
                continue
            for src in tab_sources(tab.kind, tab.doc):
                if src.get("kind") not in ("studio", "assembly"):
                    continue
                try:
                    self.resolver._folder_doc(src, src["kind"])
                except K.KernelError as e:
                    warn("dangling-link", f"“{tab.name}” ({tab.id}) has a broken link: {e}", tab, src.get("tab"))
        return out

    # ------------------------------------------------------------------ ids / tabs
    def new_tab_id(self):
        self._counter += 1
        ids = {t.id for t in self.tabs}
        while f"T{self._counter}" in ids:
            self._counter += 1
        return f"T{self._counter}"

    def _unique_filename(self, stem, ext):
        used = {t.file for t in self.tabs}
        name = f"{stem}{ext}"
        n = 2
        while name in used:
            name = f"{stem} {n}{ext}"
            n += 1
        return name

    def add_tab(self, kind, name=None):
        """Create a new, empty tab of the given registered kind and return it."""
        self._check_writable()
        doc = TK.get(kind).make()
        if name:
            doc.name = name
        tab = Tab(self.new_tab_id(), kind, self._unique_filename(doc.name, TK.get(kind).ext), doc.name)
        tab.doc = doc
        self.tabs.append(tab)
        self._attach(tab)
        return tab

    def tab(self, tab_id):
        return next((t for t in self.tabs if t.id == tab_id), None)

    def adopt_tab(self, doc, kind, tab_id=None, name=None, file=None):
        """Register an already-built document as a tab (a tab made in the UI, or one recovered after a crash).
        A recovered tab passes the id it had so keyed recents and autosaves still point at it."""
        if tab_id is not None and self.tab(tab_id) is not None:
            raise K.KernelError(f"Tab {tab_id!r} already exists in the Document")
        name = name or doc.name
        tab = Tab(tab_id or self.new_tab_id(), kind, file or self._unique_filename(name, TK.get(kind).ext), name)
        self.store.place_tab(tab.id, tab.file)     # the file name comes from an autosave: check it
        tab.doc = doc
        self.tabs.append(tab)
        self._attach(tab)
        return tab

    def add_files(self, paths):
        """Add loose tab files (any registered kind) to the Document as new tabs. Each is copied into the folder
        (the original is only read, never changed, since loose files outside may still link to it by uid and
        path), then every legacy uid + path link in the copies is rewritten to tab ids. All the files go in
        before the links are converted, so an assembly added with its Part Studios finds them. Returns the
        new tabs."""
        added = []
        for src in paths:
            ext = os.path.splitext(src)[1].lower()
            kind = TK.kind_for_ext(ext)
            if kind is None:
                raise K.KernelError(f"Can't add {os.path.basename(src)}: only {TK.extension_list()} files")
            with open(src) as fh:
                data = json.load(fh)
            name = os.path.splitext(os.path.basename(src))[0]
            file = self._unique_filename(name, ext)
            while self.store.file_taken(file):
                self._counter += 1
                file = self._unique_filename(f"{name} {self._counter}", ext)
            tab_id = self.new_tab_id()
            data["_tab_id"] = tab_id
            self.store.place_tab(tab_id, file)
            self.store.write_tab(tab_id, json.dumps(data, indent=1).encode("utf-8"))
            tab = Tab(tab_id, kind, file, name)
            tab.doc = self._load_tab(tab)
            tab.doc.name = name
            self.tabs.append(tab)
            self._attach(tab)
            added.append(tab)
        migrate_links_to_tab_ids(self)
        self.refresh_links()
        self.save()
        return added

    # ------------------------------------------------------------------ tab data through the store
    def _load_tab(self, tab):
        """Load one tab's document from the store. The store checks the manifest's file name before anything is
        read (see docstore.FolderStore). A kind with no `from_data` loads from its file."""
        rec, store = TK.get(tab.kind), self.store
        store.place_tab(tab.id, tab.file)
        if rec.from_data is None:
            return rec.load(store.checked_tab_path(tab.id))
        return rec.from_data(store.read_tab(tab.id), path=store.tab_path(tab.id), blobs=store.blobs(tab.id))

    @staticmethod
    def _tab_bytes(tab):
        """A tab's data as saved in a Document: the document's own JSON, stamped with the tab id."""
        doc = tab.doc
        data = json.loads(doc.to_data() if hasattr(doc, "to_data") else doc.to_json())
        data["_tab_id"] = tab.id
        return json.dumps(data, indent=1).encode("utf-8")

    # ------------------------------------------------------------------ create / open / save
    @classmethod
    def create(cls, path, name=None, registry=None):
        """Make a new Document folder: manifest, a git repository with `.gitattributes`, and the trust marker in the
        registry. A folder that already has a `.git` is not ours to initialise: it is neither initialised nor
        trusted (open it with open_document, which imports it)."""
        os.makedirs(path, exist_ok=True)
        doc = cls()
        doc.path = path
        if name:
            doc.name = name
        doc._write_manifest()
        if not os.path.lexists(os.path.join(path, ".git")):
            try:
                G.init_repo(path)
                G.ensure_attributes(path, _attribute_lines())
                doc.trusted = True
            except G.GitUnavailable as e:
                doc.git_error = str(e)
                doc.trusted = True            # no repository to protect; versions then report the error
            doc.registry = registry or R.default_registry()
            try:
                doc.registry.register_created(doc.id, path)
            except OSError:
                pass
        return doc

    @classmethod
    def create_on(cls, store, name=None, registry=None):
        """Make a new Document on `store` (a DocumentStore) and write its manifest there. A store that keeps a
        folder gives the Document that `path`; a store that keeps none (docstore.MemoryStore) leaves `path` None.
        No repository is made and the Document is not trusted, so Versions, Workspaces and merge need a folder
        (see docstore.git_folder). `create` is the folder route: it also initialises git and registers the folder."""
        doc = cls()
        doc.path = store.folder
        doc._store = store
        doc.registry = registry
        if name:
            doc.name = name
        doc._write_manifest()
        return doc

    @classmethod
    def open_on(cls, store, registry=None):
        """Load the Document that `store` holds: `open` for any store."""
        return cls.open(store.folder, registry=registry, store=store)

    @classmethod
    def open_document(cls, path, registry=None, managed_root=None):
        """Open a Document the way the app should: a folder the app created (or already imported) opens in place;
        anything else is first imported with `git clone --no-local` into `managed_root`, and the app works only in
        the clone, never running git in the received folder. A received folder with no commits, or no repository,
        is copied without its `.git` and given a fresh one. Uncommitted edits in a received folder are not imported."""
        registry = registry or R.default_registry()
        real = os.path.realpath(path)
        doc_id = _peek_id(real)
        if doc_id and registry.is_trusted(doc_id, real):
            target = real
        else:
            target = cls._import(real, registry, managed_root or R.managed_root())
        doc = cls.open(target, registry=registry)
        doc.trusted = True
        return doc

    @classmethod
    def reimport_document(cls, source, registry=None, managed_root=None):
        """Import a received Document again to pick up what the sender has added since: a fresh `git clone --no-local`
        of `source` into the managed folder replaces the earlier clone in the registry (and on disk). This is the only
        time the app reads the sender's folder again; cross-Document pins see new Versions only through it."""
        registry = registry or R.default_registry()
        root = managed_root or R.managed_root()
        real = os.path.realpath(source)
        old = registry.import_for_source(real)
        old_id = _peek_id(old) if old else None
        target = cls._import(real, registry, root, fresh=True)
        if old and os.path.realpath(old) != os.path.realpath(target):
            registry.forget(old_id, old)
            if os.path.realpath(os.path.dirname(old)) == os.path.realpath(root):
                _rmtree(old)
        doc = cls.open(target, registry=registry)
        doc.trusted = True
        return doc

    @classmethod
    def _import(cls, source, registry, root, fresh=False):
        existing = None if fresh else registry.import_for_source(source)
        if existing:
            return existing
        os.makedirs(root, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9_-]", "_", _peek_id(source) or "")[:40] or "document"
        n, name = 1, stem
        while os.path.lexists(os.path.join(root, name)):
            n += 1
            name = f"{stem}-{n}"
        dest = os.path.join(os.path.realpath(root), name)
        try:
            if os.path.lexists(os.path.join(source, ".git")):
                G.clone(source, root, name)
                if G.head(dest) is None or _peek_id(dest) is None:
                    _rmtree(dest)
                    G.adopt_plain_folder(source, root, name)
            else:
                G.adopt_plain_folder(source, root, name)
            doc_id = _peek_id(dest)
            if doc_id is None:
                raise K.KernelError("Not a FreeShape Document")
        except Exception:
            _rmtree(dest)
            raise
        registry.register_import(doc_id, source, dest)
        return dest

    @classmethod
    def open(cls, path, registry=None, store=None):
        """Load the folder at `path` as it stands. `registry` lets cross-Document links resolve while it loads.
        `store` is the DocumentStore to read through (by default a docstore.FolderStore on `path`)."""
        data = cls._read_manifest((store if store is not None else DS.FolderStore(path)).read_manifest())
        version = data.get("version", 1)
        if version > FORMAT_VERSION:
            raise K.KernelError(f"{MANIFEST_NAME} was saved with file version {version}, but this build "
                                f"of FreeShape only supports up to version {FORMAT_VERSION}. Update "
                                f"FreeShape to open it.")
        doc = cls()
        doc.path = path
        doc._store = store
        doc.registry = registry
        doc.id = data.get("id") or doc.id
        doc.name = data.get("name", doc.name)
        doc.extra = {k: v for k, v in data.items() if k not in _MANIFEST_KEYS}
        doc.tabs = []
        doc.errors = {}
        for entry in data.get("tabs", []):
            tab_id, kind, file = entry.get("id"), entry.get("kind"), entry.get("file")
            label = f"tab {tab_id!r}"
            tab = Tab(tab_id, kind, file, entry.get("name") or kind or "")
            tab.extra = {k: v for k, v in entry.items() if k not in _TAB_KEYS}
            doc.tabs.append(tab)
            try:
                if kind not in TK.KINDS:
                    raise K.KernelError(f"{label}: unknown tab kind {kind!r}")
                tab.doc = doc._load_tab(tab)
            except K.KernelError as e:
                doc.errors[tab_id] = str(e)
            except Exception as e:      # a damaged tab file (bad JSON, a missing key...) must not stop the rest
                doc.errors[tab_id] = f"{label}: could not be read ({type(e).__name__}: {e})"
        nums = [int("".join(c for c in (t.id or "") if c.isdigit()) or 0) for t in doc.tabs]
        doc._counter = max(nums + [0])
        for tab in doc.tabs:
            doc._attach(tab)
        # the uid + path -> tab id step of the link-format migration (see links.py), once every tab is loaded
        migrate_links_to_tab_ids(doc)
        doc.refresh_links()
        doc.warnings = doc.document_warnings()
        return doc

    @staticmethod
    def _read_manifest(raw):
        """Parse document.json's bytes and check its shape; raise K.KernelError naming the first fault."""
        try:
            data = json.loads(bytes(raw).decode("utf-8"))
        except ValueError as e:
            raise K.KernelError(f"{MANIFEST_NAME} is not valid JSON ({e})") from e
        if not isinstance(data, dict):
            raise K.KernelError(f"{MANIFEST_NAME}: the top level must be an object, not {type(data).__name__}")
        if data.get("format") != FORMAT:
            raise K.KernelError("Not a FreeShape Document")
        version = data.get("version", 1)
        if not isinstance(version, int) or isinstance(version, bool):
            raise K.KernelError(f"{MANIFEST_NAME}: version must be an integer, not {version!r}")
        tabs = data.get("tabs", [])
        if not isinstance(tabs, list):
            raise K.KernelError(f"{MANIFEST_NAME}: tabs must be a list, not {type(tabs).__name__}")
        seen = set()
        for i, entry in enumerate(tabs):
            if not isinstance(entry, dict):
                raise K.KernelError(f"{MANIFEST_NAME}: tab entry {i} must be an object, "
                                    f"not {type(entry).__name__}")
            tab_id = entry.get("id")
            if not isinstance(tab_id, str) or not tab_id:
                raise K.KernelError(f"{MANIFEST_NAME}: tab entry {i} has an empty or non-string id ({tab_id!r})")
            if tab_id in seen:
                raise K.KernelError(f"{MANIFEST_NAME}: duplicate tab id {tab_id!r}")
            seen.add(tab_id)
        return data

    def _check_writable(self):
        if self.read_only:
            raise K.KernelError("This is a read-only view of a version; it cannot be edited or saved")

    def save(self):
        self._check_writable()
        if self.path is None and self._store is None:
            raise K.KernelError("Document has no folder to save to")
        store = self.store
        for tab in self.tabs:
            if tab.doc is None:
                continue
            store.place_tab(tab.id, tab.file)
            if store.has_tab(tab.id) and not tab.doc.dirty:
                continue
            tab.doc.path = store.tab_path(tab.id)
            if hasattr(tab.doc, "write_blobs"):      # a Part Studio's Python-feature builds (blobs/, I4.7)
                tab.doc.write_blobs(tab.doc.path, store=store.blobs(tab.id))
            store.write_tab(tab.id, self._tab_bytes(tab))
            tab.doc.dirty = False
        self._write_manifest()

    def _write_manifest(self):
        data = {**self.extra, "format": FORMAT, "version": FORMAT_VERSION, "id": self.id, "name": self.name,
                "tabs": [{**t.extra, "id": t.id, "kind": t.kind, "file": t.file, "name": t.name}
                         for t in self.tabs]}
        self.store.write_manifest(json.dumps(data, indent=1).encode("utf-8"))

    # ------------------------------------------------------------------ versions (git; see gitstore.py)
    def _git_ready(self):
        folder = DS.git_folder(self.store)      # a store with no folder refuses first: trust is not the problem there
        if not self.trusted:
            raise K.KernelError("Versions need a Document the app created or imported: open it with open_document")
        if folder is None or not G.is_repo(folder):
            raise G.GitError(self.git_error or "This Document folder has no repository")

    def create_version(self, name, note=""):
        """Save, then commit everything and tag the commit (annotated, carrying `note`). Saving alone never commits."""
        self._check_writable()
        self._git_ready()
        G.check_name(name)
        if any(v["name"] == name for v in G.list_tags(self.path)):
            raise G.GitError(f"A version named {name!r} already exists")
        self.save()
        G.commit_all(self.path, note or name)
        G.tag(self.path, name, note)
        return next(v for v in G.list_tags(self.path) if v["name"] == name)

    def versions(self):
        """Versions, oldest first: dicts {name, commit, note, time}."""
        self._git_ready()
        return G.list_tags(self.path)

    def open_version(self, name):
        """A read-only FolderDocument of version `name`: every tab loaded and every intra-Document link resolved from
        the files at that commit. The files are read raw from git (no filters) into a scratch folder, removed
        by close() or when the view is dropped."""
        self._git_ready()
        commit = G.resolve_version(self.path, name)
        scratch = tempfile.mkdtemp(prefix="freeshape-version-")
        try:
            folder = os.path.join(scratch, "Document")
            os.makedirs(folder)
            G.read_tree(self.path, commit, folder)
            view = FolderDocument.open(folder, registry=self.registry)     # its links resolve against the view's own tabs
        except (OSError, ValueError) as e:
            _rmtree(scratch)
            raise K.KernelError(f"Version {name!r} is not a readable Document: {e}") from e
        except Exception:
            _rmtree(scratch)
            raise
        view.read_only = True
        view.version = next((v for v in G.list_tags(self.path) if v["name"] == name),
                            {"name": name, "commit": commit, "note": "", "time": 0})
        view._scratch = scratch
        weakref.finalize(view, _rmtree, scratch)
        return view

    # ------------------------------------------------------------------ workspaces (git branches)
    def unsaved_tabs(self):
        """Names of the tabs with unsaved changes."""
        return [t.name or t.id for t in self.tabs if t.doc is not None and t.doc.dirty]

    def git_problem(self):
        """Why this Document has no versions or Workspaces (git missing, no repository), or None."""
        if self.git_error:
            return self.git_error
        try:
            folder = DS.git_folder(self.store)
        except K.KernelError as e:
            return str(e)
        if folder is None or not G.is_repo(folder):
            return "This Document folder has no repository, so it has no versions or Workspaces"
        return None

    def workspaces(self):
        """Workspace names (branches), the default one first."""
        self._git_ready()
        names = G.list_branches(self.path)
        return names or [G.current_branch(self.path) or G.DEFAULT_BRANCH]

    def current_workspace(self):
        self._git_ready()
        return G.current_branch(self.path) or G.DEFAULT_BRANCH

    def _check_dirty(self, dirty):
        if dirty not in ("raise", "save", "discard"):
            raise ValueError(f"dirty must be 'raise', 'save' or 'discard', not {dirty!r}")
        unsaved = self.unsaved_tabs()
        if unsaved and dirty == "raise":
            raise DirtyWorkspaceError(unsaved)
        return unsaved

    def switch_workspace(self, name, dirty="raise"):
        """Check out Workspace `name` and reload every tab from it. Unsaved tabs block the switch
        (`DirtyWorkspaceError`, nothing changed) unless `dirty` is "save" (save first) or "discard" (drop them).
        Saved but uncommitted edits stay with the Workspace they were made in: they are committed there first."""
        self._check_writable()
        self._git_ready()
        G.check_branch_name(name)
        if name not in G.list_branches(self.path):
            raise G.GitError(f"No Workspace named {name!r}")
        unsaved = self._check_dirty(dirty)
        if name == self.current_workspace():
            return
        if unsaved and dirty == "save":
            self.save()
        self._snapshot()
        G.switch_branch(self.path, name)
        self.reload()

    def create_workspace(self, name, version=None, switch=True, dirty="raise"):
        """Make Workspace `name` from version `version` (the current commit when None) and, by default, switch to
        it. A refused switch (unsaved tabs) leaves no new Workspace behind."""
        self._check_writable()
        self._git_ready()
        G.check_branch_name(name)
        if name in G.list_branches(self.path):
            raise G.GitError(f"A Workspace named {name!r} already exists")
        commit = G.resolve_version(self.path, version) if version is not None else None
        if switch:
            self._check_dirty(dirty)
        if commit is None:
            self._snapshot()
            commit = G.head(self.path)
        if commit is None:
            raise G.GitError("Create a version first: a Workspace starts from one")
        G.create_branch(self.path, name, commit)
        if switch:
            self.switch_workspace(name, dirty)
        return name

    def _snapshot(self):
        """Commit saved-but-uncommitted edits to the current Workspace so a branch change cannot carry them along."""
        if G.has_changes(self.path):
            G.commit_all(self.path, f"Snapshot of Workspace {self.current_workspace()}")

    def merge_preview(self, name, dirty="raise"):
        """What merging Workspace `name` into the current one would do: a doc_merge.MergePreview listing the tabs
        changed on both sides (pick ours or theirs per tab) and the problems in the merged tree. Saved but
        uncommitted edits are committed to the current Workspace first, as a switch does; nothing else changes."""
        from . import doc_merge
        self._check_writable()
        self._git_ready()
        G.check_branch_name(name)
        if name not in G.list_branches(self.path):
            raise G.GitError(f"No Workspace named {name!r}")
        ours = G.current_branch(self.path)
        if ours is None or ours == name:
            raise G.GitError("Switch to the Workspace to merge into, then merge another one into it")
        unsaved = self._check_dirty(dirty)
        if unsaved and dirty == "save":
            self.save()
        self._snapshot()
        return doc_merge.preview(self, ours, name)

    def merge_workspace(self, name, choices=None, dirty="raise"):
        """Merge Workspace `name` into the current one, taking ours or theirs per conflicting tab as `choices`
        ({key: "ours" | "theirs"}, keys from the preview's conflicts) says, and reload. Refused
        (doc_merge.MergeRefused, nothing committed) while a conflict has no choice or the merged tree has a problem
        such as a dangling link. Returns the merge commit, or None when there was nothing to merge."""
        from . import doc_merge
        p = self.merge_preview(name, dirty)
        p.choose_all(choices or {})
        commit = doc_merge.commit(self, p)
        if commit is not None:
            self.reload()
        return commit

    def reload(self):
        """Re-read the manifest and every tab from disk into this object (after a checkout)."""
        fresh = FolderDocument.open(self.path, store=self._store)
        self.name, self.tabs, self.errors, self._counter = fresh.name, fresh.tabs, fresh.errors, fresh._counter
        for tab in self.tabs:
            self._attach(tab)
        migrate_links_to_tab_ids(self)
        self.refresh_links()
        self.warnings = self.document_warnings()

    def close(self):
        """Release a version view's scratch folder."""
        if self._scratch:
            _rmtree(self._scratch)
            self._scratch = None

    def push(self, remote):
        """Push the history and version tags to a local bare repository (a folder path)."""
        self._check_writable()
        self._git_ready()
        G.push(self.path, remote)
