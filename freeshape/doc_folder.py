"""FolderDocument: the folder-level Document -- a folder on disk holding `document.json` plus one file per
tab (a Part Studio, an Assembly, a Drawing or a Variable Studio). Headless -- no Qt.

Note the name clash this module works around: `freeshape.document.Document` is a *Part Studio*, not this
folder-level Document. That class keeps its name (and its own `document` file format / migration chain --
see freeshape/migrations.py); this module's class is called `FolderDocument` to stay unambiguous, and
"document.json" below always means the folder manifest, never a Part Studio file.

document.json is `{format, version, id, name, tabs: [{id, kind, file, name}, ...]}`. A tab's `id` is
generated once and never changes; it is also stamped into the tab's own file (as `_tab_id`) so it
survives a rename of that file on disk, even though this card doesn't yet reconcile a drifted `file`
entry back to its tab (that is I2.2's job, per freeshape/migrations.py).

A Document folder may have come from someone else, so document.json is untrusted input: every tab's
`file` is checked to resolve to a real path inside the folder (no absolute path, no `..` segment, and no
escape via a symlinked directory), and neither a tab file nor document.json itself may be a symlink. A
tab that fails this check is recorded in `errors` keyed by its id and its file is never opened -- the
check runs before any attempt to read it. (The FreeCAD sources mirrored into this repo at /opt/freecad-src
are pruned down to the element-map files ADR 0003 ported, so there's no Document/project-file handling
in them to port here; writes instead reuse this repo's own existing atomic-save convention, already used
by AssemblyDoc.save and DrawingDoc.save -- a `<path>.tmp` file written alongside the target and moved
into place with `os.replace`, which never follows a symlink at the destination.)
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
from . import gitstore as G
from . import kernel as K
from .assembly import AssemblyDoc
from .document import Document
from .drawing import DrawingDoc
from .links import LinkResolver, migrate_links_to_tab_ids, tab_sources
from .variable_studio import VariableStudioDoc

MANIFEST_NAME = "document.json"
FORMAT = "freeshape-document"
FORMAT_VERSION = 1

EXTENSIONS = {"studio": ".fshape", "assembly": ".fasm", "drawing": ".fdwg", "variables": ".fvars"}
FACTORIES = {"studio": Document, "assembly": AssemblyDoc, "drawing": DrawingDoc, "variables": VariableStudioDoc}
LOADERS = {"studio": Document.load, "assembly": AssemblyDoc.load, "drawing": DrawingDoc.load,
           "variables": VariableStudioDoc.load}


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _rmtree(path):
    shutil.rmtree(path, ignore_errors=True)


def _peek_id(folder):
    """The id in a folder's document.json, or None. Reads one regular file; runs no git and loads nothing."""
    p = os.path.join(folder, MANIFEST_NAME)
    try:
        if os.path.islink(p) or os.path.getsize(p) > (1 << 20):
            return None
        with open(p) as fh:
            doc_id = json.load(fh).get("id")
    except (OSError, ValueError, AttributeError):
        return None
    return doc_id if isinstance(doc_id, str) else None


def _attribute_lines():
    """`.gitattributes` for a new Document: no automatic merge of the manifest or any tab file."""
    return [f"{MANIFEST_NAME} -merge"] + [f"*{ext} -merge" for ext in EXTENSIONS.values()]


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


class FolderDocument:
    def __init__(self):
        self.id = uuid.uuid4().hex
        self.name = "Document 1"
        self.path = None
        self.tabs: list[Tab] = []
        self.errors: dict[str, str] = {}
        self.warnings: list[dict] = []   # problems found on open: see document_warnings
        self._counter = 0
        self.resolver = LinkResolver(folder=self)    # resolves tab-id links through this manifest
        self.trusted = False      # git may run in self.path: the app created it or imported it (see open_document)
        self.read_only = False    # an opened version
        self.version = None       # {name, commit, note, time} of an opened version
        self.registry = None
        self.git_error = None     # why a new Document got no repository (git missing or too old)
        self._scratch = None

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

    def document_warnings(self):
        """Check the Document as it stands without refusing it: the manifest against the folder (a tab file that
        is not in the manifest, a manifest entry whose file is missing or will not open) and every link inside it
        (a link to a tab id that is not in the manifest, judged by the resolver itself). Run on every open, since a
        merge done with plain git never passes the app's merge preview. Returns dicts {problem, tab, tab_name,
        target, message}; `problem` is "missing-file", "unreadable", "not-in-manifest" or "dangling-link"."""
        out = []

        def warn(problem, message, tab=None, target=None):
            out.append({"problem": problem, "tab": tab.id if tab else None, "tab_name": tab.name if tab else None,
                        "target": target, "message": message})

        listed = set()
        for tab in self.tabs:
            label = f"Tab “{tab.name}” ({tab.id})"
            try:
                full = self._resolve_tab_path(tab.file, label)
                listed.add(os.path.normpath(os.path.relpath(full, self.path)))
            except K.KernelError:
                full = None
            if tab.doc is not None:
                continue
            if full is not None and not os.path.lexists(full):
                warn("missing-file", f"{label}: its file “{tab.file}” is missing from the Document folder", tab)
            else:
                warn("unreadable", f"{label} could not be opened: {self.errors.get(tab.id, 'unknown error')}", tab)
        exts = tuple(EXTENSIONS.values())
        for root, dirs, files in os.walk(self.path or "."):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                rel = os.path.normpath(os.path.relpath(os.path.join(root, f), self.path))
                if f.endswith(exts) and rel not in listed:
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
        """Create a new, empty tab of the given kind ("studio" / "assembly" / "drawing") and return it."""
        self._check_writable()
        doc = FACTORIES[kind]()
        if name:
            doc.name = name
        tab = Tab(self.new_tab_id(), kind, self._unique_filename(doc.name, EXTENSIONS[kind]), doc.name)
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
        tab = Tab(tab_id or self.new_tab_id(), kind, file or self._unique_filename(name, EXTENSIONS[kind]), name)
        self._resolve_tab_path(tab.file, f"tab {tab.id!r}")     # the file name comes from an autosave: check it
        tab.doc = doc
        self.tabs.append(tab)
        self._attach(tab)
        return tab

    def add_files(self, paths):
        """Add loose .fshape / .fasm / .fdwg files to the Document as new tabs. Each is copied into the folder
        (the original is only read, never changed, since loose files outside may still link to it by uid and
        path), then every legacy uid + path link in the copies is rewritten to tab ids. All the files go in
        before the links are converted, so an assembly added with its Part Studios finds them. Returns the
        new tabs."""
        kinds = {ext: kind for kind, ext in EXTENSIONS.items() if kind != "variables"}
        added = []
        for src in paths:
            ext = os.path.splitext(src)[1].lower()
            kind = kinds.get(ext)
            if kind is None:
                raise K.KernelError(f"Can't add {os.path.basename(src)}: only .fshape, .fasm and .fdwg files")
            with open(src) as fh:
                data = json.load(fh)
            name = os.path.splitext(os.path.basename(src))[0]
            file = self._unique_filename(name, ext)
            while os.path.exists(os.path.join(self.path, file)):
                self._counter += 1
                file = self._unique_filename(f"{name} {self._counter}", ext)
            tab_id = self.new_tab_id()
            data["_tab_id"] = tab_id
            _atomic_write(os.path.join(self.path, file), json.dumps(data, indent=1))
            tab = Tab(tab_id, kind, file, name)
            tab.doc = LOADERS[kind](os.path.join(self.path, file))
            tab.doc.name = name
            self.tabs.append(tab)
            self._attach(tab)
            added.append(tab)
        migrate_links_to_tab_ids(self)
        self.refresh_links()
        self.save()
        return added

    # ------------------------------------------------------------------ untrusted-path checks
    def _resolve_tab_path(self, rel, label):
        """Join `rel` onto the folder after structural checks that need no filesystem access: no absolute
        path, no '..' segment."""
        if not rel or not isinstance(rel, str):
            raise K.KernelError(f"{label}: missing a file path")
        if os.path.isabs(rel) or (len(rel) >= 2 and rel[1] == ":"):
            raise K.KernelError(f"{label}: an absolute path isn't allowed ({rel!r})")
        if ".." in rel.replace("\\", "/").split("/"):
            raise K.KernelError(f"{label}: a '..' segment isn't allowed ({rel!r})")
        return os.path.normpath(os.path.join(self.path, rel))

    def _safe_tab_path(self, rel, label):
        """`_resolve_tab_path` plus the escape check that needs the filesystem: the real path must still
        land inside the folder. Used when opening untrusted manifest content, before anything is read. A
        save() target skips this extra check -- it's already known safe (self-generated by `add_tab`, or
        validated here when its entry was opened) -- because this check follows a symlinked *leaf* to
        find out where it points, which is the opposite of what a write wants: `save` must never follow
        one, and the tempfile-then-rename it uses for every write is what guarantees that regardless of
        what the target currently is."""
        full = self._resolve_tab_path(rel, label)
        root = os.path.realpath(self.path)
        if os.path.commonpath([root, os.path.realpath(full)]) != root:
            raise K.KernelError(f"{label}: resolves outside the Document folder ({rel!r})")
        return full

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
    def open(cls, path, registry=None):
        """Load the folder at `path` as it stands. `registry` lets cross-Document links resolve while it loads."""
        manifest_path = os.path.join(path, MANIFEST_NAME)
        if os.path.islink(manifest_path):
            raise K.KernelError(f"{MANIFEST_NAME}: refusing to open a symlink")
        with open(manifest_path) as fh:
            data = json.load(fh)
        if data.get("format") != FORMAT:
            raise K.KernelError("Not a FreeShape Document")
        version = data.get("version", 1)
        if version > FORMAT_VERSION:
            raise K.KernelError(f"{MANIFEST_NAME} was saved with file version {version}, but this build "
                                f"of FreeShape only supports up to version {FORMAT_VERSION}. Update "
                                f"FreeShape to open it.")
        doc = cls()
        doc.path = path
        doc.registry = registry
        doc.id = data.get("id") or doc.id
        doc.name = data.get("name", doc.name)
        doc.tabs = []
        doc.errors = {}
        for entry in data.get("tabs", []):
            tab_id, kind, file = entry.get("id"), entry.get("kind"), entry.get("file")
            label = f"tab {tab_id!r}"
            tab = Tab(tab_id, kind, file, entry.get("name") or kind or "")
            doc.tabs.append(tab)
            try:
                if kind not in LOADERS:
                    raise K.KernelError(f"{label}: unknown tab kind {kind!r}")
                full = doc._safe_tab_path(file, label)
                if not os.path.lexists(full):
                    raise K.KernelError(f"{label}: its file {file!r} is missing from the Document folder")
                if os.path.islink(full):
                    raise K.KernelError(f"{label}: refusing to open a symlink ({file!r})")
                tab.doc = LOADERS[kind](full)
            except K.KernelError as e:
                doc.errors[tab_id] = str(e)
        nums = [int("".join(c for c in (t.id or "") if c.isdigit()) or 0) for t in doc.tabs]
        doc._counter = max(nums + [0])
        for tab in doc.tabs:
            doc._attach(tab)
        # the uid + path -> tab id step of the link-format migration (see links.py), once every tab is loaded
        migrate_links_to_tab_ids(doc)
        doc.refresh_links()
        doc.warnings = doc.document_warnings()
        return doc

    def _check_writable(self):
        if self.read_only:
            raise K.KernelError("This is a read-only view of a version; it cannot be edited or saved")

    def save(self):
        self._check_writable()
        if self.path is None:
            raise K.KernelError("Document has no folder to save to")
        os.makedirs(self.path, exist_ok=True)
        for tab in self.tabs:
            if tab.doc is None:
                continue
            full = self._resolve_tab_path(tab.file, f"tab {tab.id!r}")
            if os.path.exists(full) and not tab.doc.dirty:
                continue
            tab.doc.path = full
            if hasattr(tab.doc, "write_blobs"):      # a Part Studio's Python-feature builds (blobs/, I4.7)
                tab.doc.write_blobs(full)
            data = json.loads(tab.doc.to_json())
            data["_tab_id"] = tab.id
            _atomic_write(full, json.dumps(data, indent=1))
            tab.doc.dirty = False
        self._write_manifest()

    def _write_manifest(self):
        data = {"format": FORMAT, "version": FORMAT_VERSION, "id": self.id, "name": self.name,
                "tabs": [{"id": t.id, "kind": t.kind, "file": t.file, "name": t.name} for t in self.tabs]}
        _atomic_write(os.path.join(self.path, MANIFEST_NAME), json.dumps(data, indent=1))

    # ------------------------------------------------------------------ versions (git; see gitstore.py)
    def _git_ready(self):
        if not self.trusted:
            raise K.KernelError("Versions need a Document the app created or imported: open it with open_document")
        if self.path is None or not G.is_repo(self.path):
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
        if self.path is None or not G.is_repo(self.path):
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
        fresh = FolderDocument.open(self.path)
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
