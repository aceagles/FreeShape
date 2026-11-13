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
import uuid

from . import kernel as K
from .assembly import AssemblyDoc
from .document import Document
from .drawing import DrawingDoc
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
        self._counter = 0

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
        doc = FACTORIES[kind]()
        if name:
            doc.name = name
        tab = Tab(self.new_tab_id(), kind, self._unique_filename(doc.name, EXTENSIONS[kind]), doc.name)
        tab.doc = doc
        self.tabs.append(tab)
        return tab

    def tab(self, tab_id):
        return next((t for t in self.tabs if t.id == tab_id), None)

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
    def create(cls, path, name=None):
        os.makedirs(path, exist_ok=True)
        doc = cls()
        doc.path = path
        if name:
            doc.name = name
        doc._write_manifest()
        return doc

    @classmethod
    def open(cls, path):
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
                if os.path.islink(full):
                    raise K.KernelError(f"{label}: refusing to open a symlink ({file!r})")
                tab.doc = LOADERS[kind](full)
            except K.KernelError as e:
                doc.errors[tab_id] = str(e)
        nums = [int("".join(c for c in (t.id or "") if c.isdigit()) or 0) for t in doc.tabs]
        doc._counter = max(nums + [0])
        return doc

    def save(self):
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
            data = json.loads(tab.doc.to_json())
            data["_tab_id"] = tab.id
            _atomic_write(full, json.dumps(data, indent=1))
            tab.doc.dirty = False
        self._write_manifest()

    def _write_manifest(self):
        data = {"format": FORMAT, "version": FORMAT_VERSION, "id": self.id, "name": self.name,
                "tabs": [{"id": t.id, "kind": t.kind, "file": t.file, "name": t.name} for t in self.tabs]}
        _atomic_write(os.path.join(self.path, MANIFEST_NAME), json.dumps(data, indent=1))
