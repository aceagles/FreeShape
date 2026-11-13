"""Link resolution: finds the Part Studio or Assembly behind an assembly instance's source or a drawing's
source. Headless -- no Qt, no UI widget. Resolving never mutates a source dict -- when a link has drifted
(a renamed Part Studio, a moved file) `repair_links` writes the fix back, meant to be called just before
saving rather than on every resolve.

`LinkResolver` is headless: it has no idea what a "tab" is. The caller (normally MainWindow) injects
`find_open_studio` / `find_open_assembly` callbacks so documents already open in a tab are reused instead of
reloaded from disk, and a `flat_pattern_of` callback so a flat pattern already computed for the viewport is
reused too. Without those, everything falls back to loading straight from disk, cached by path and mtime --
which is exactly what the tests use.

Inside a folder Document (`folder=` a doc_folder.FolderDocument) a source stores `{"tab": <tab id>, "part": ...}`
instead of uid + path, and the tab id is looked up in the Document's manifest, so renaming a tab's file or linking
a tab that has never been saved cannot break the link. A source that also carries `"document"` (another
Document's id) and `"version"` (one of its Version names) is a cross-Document link: see `_cross_doc`. A legacy uid + path link inside a Document is converted to a tab id on open (see
`migrate_links_to_tab_ids`), and one whose path resolves outside the Document folder is never read -- the
instance is reported unresolved with an error naming the path, since a received Document could otherwise point a
link at any file on the machine. Loose files outside any Document keep uid + path.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from . import kernel as K
from .document import Document


def _no_open_doc(uid, path):
    return None


def _translate_part(studio, pid):
    """A part id as named by an assembly instance or drawing source, resolved against the studio's current
    ids through its alias table (populated when a pre-I1.4 file is loaded -- see
    document._migrate_part_ids); unchanged if it names no old id. Never mutates the caller's source dict.
    (`studio.state.parts` would also resolve an old id directly -- see features.PartMap -- but checking
    that here would always say "already current", since PartMap's own `in` already consults the alias.)"""
    if pid in (None, "*"):
        return pid
    return getattr(studio, "part_aliases", {}).get(pid, pid)


def studio_source(tab, part=None, document=None, version=None):
    """The source dict an assembly instance or drawing stores to link a Part Studio tab of a Document: the tab
    id and part id only -- no uid and no path, so it holds for a tab that has never been saved. For a tab of
    another Document pass that Document's id and the Version to pin: still no path."""
    src = {"kind": "studio", "tab": tab.id, "part": part, "studio_name": tab.name}
    if document is not None:
        src.update(document=document, version=version)
    return src


def assembly_source(tab, document=None, version=None):
    """The source dict a drawing stores to link an Assembly tab of a Document (of another one: see studio_source)."""
    src = {"kind": "assembly", "tab": tab.id, "name": tab.name}
    if document is not None:
        src.update(document=document, version=version)
    return src


def is_cross_link(src, own_id=None):
    """Whether a source names another Document (a `document` id that is not `own_id`)."""
    doc = src.get("document")
    return bool(doc) and doc != own_id


def _default_flat_pattern_of(state, pid):
    from . import sheetmetal as SM
    return SM.flat_pattern(state.parts[pid].shape, state.sheet[pid], state.named(pid))


@dataclass
class PublishedPart:
    """One part of a studio's published output, in the studio's own coordinates."""
    id: str
    name: str
    color: tuple
    hidden: bool
    shape: object            # the TopoDS shape (not a copy: read-only)
    names: dict              # {"face": {i: name}, "edge": {...}, "vertex": {...}}, 1-based i; empty if unnamed
    hash: str                # sha256 hex of the part's BREP text


@dataclass
class StudioPackage:
    """What a Part Studio publishes, as a consumer reads it: its parts, its sketches (engine.contract.SketchOut,
    with element names) and the digest of both. See LinkResolver.resolve_studio."""
    parts: dict              # part id -> PublishedPart
    sketches: dict           # sketch feature id -> SketchOut
    aliases: dict = field(default_factory=dict)
    digest: str = ""         # of everything published: parts, their visibility, sketches

    def digest_for(self, part=None):
        """The digest of what a link reading `part` (an id, or None / "*" for the whole studio) takes."""
        from .engine.linkgraph import package_digest
        return package_digest("studio", self._view(), [part])

    def _view(self):
        return {"parts": {pid: p.hash for pid, p in self.parts.items()},
                "hidden": sorted(pid for pid, p in self.parts.items() if p.hidden), "aliases": dict(self.aliases),
                "sketches": {fid: sk.hash for fid, sk in self.sketches.items()}}


class LinkResolver:
    """callable(src) -> features.Part for AssemblyDoc.resolver, or callable(src) -> TopoDS shape for
    DrawingDoc.resolver (use resolve_source / resolve_drawing_source directly for that)."""

    def __init__(self, find_open_studio=None, find_open_assembly=None, flat_pattern_of=None, load_document=None,
                 load_assembly=None, folder=None, registry=None):
        self.folder = folder       # the open FolderDocument whose manifest resolves tab ids, if any
        self._registry = registry  # finds another Document by id; default: the open folder's registry
        self._others = {}          # Document id -> (folder path, trusted FolderDocument opened in place)
        self._views = {}           # (Document id, folder path, version name) -> read-only version view
        self.find_open_studio = find_open_studio or _no_open_doc
        self.find_open_assembly = find_open_assembly or _no_open_doc
        self.flat_pattern_of = flat_pattern_of or _default_flat_pattern_of
        self._load_document = load_document or Document.load
        self._load_assembly = load_assembly or self._default_load_assembly
        self._loaded = {}          # abs path -> (mtime, doc), for studios/assemblies loaded from disk
        self._shape_cache = {}     # compound cache for drawing sources, keyed by (kind, uid/id, ...)
        self._encoder = None       # engine.encode.Encoder, made on the first resolve_studio

    def _default_load_assembly(self, path):
        from .assembly import AssemblyDoc
        return AssemblyDoc.load(path, resolver=self.resolve_source)

    # ------------------------------------------------------------------ the Link graph (see LinkGraph, end of file)
    @property
    def graph(self):
        """The edges recorded so far: which tab consumed which tab's output. Made on first use."""
        g = self.__dict__.get("_graph")
        if g is None:
            g = self.__dict__["_graph"] = LinkGraph()
        return g

    def record_edge(self, consumer, upstream, kind=None, keys=(), digest=None, declared=False):
        """`consumer` (a tab id) was handed `upstream`'s output. `keys` names what it reads of it (part ids),
        `digest` is a hash of exactly that. `declared`: an edge the engine did not read off a document link,
        which `record_inputs` leaves alone."""
        self.graph.add(consumer, upstream, Edge(kind, tuple(keys), digest, declared))

    def record_inputs(self, consumer, edges):
        """Replace the edges into `consumer` ({upstream tab id: Edge}) with what it consumed this time."""
        self.graph.set_inputs(consumer, edges)

    def forget_tab(self, tab):
        """A closed tab consumes nothing any more."""
        self.graph.forget(tab)

    # ------------------------------------------------------------------ inside a Document
    def _outside_folder(self, path):
        root = os.path.realpath(self.folder.path)
        full = os.path.realpath(os.path.join(root, path))     # an absolute path replaces the folder
        return os.path.commonpath([root, full]) != root

    # ------------------------------------------------------------------ another Document, pinned to a Version
    @property
    def registry(self):
        return self._registry or getattr(self.folder, "registry", None)

    def _other_document(self, doc_id):
        """The trusted folder Document registered under `doc_id`. Found through the registry only: the link holds
        no path, and a registry record the app did not make trusted (or whose folder is gone) is not opened.
        Raises KernelError naming the id."""
        reg = self.registry
        if not isinstance(doc_id, str) or reg is None:
            raise K.KernelError(f"Document “{doc_id}” is not in the Document registry")
        path = reg.trusted_path(doc_id)
        if path is None:
            raise K.KernelError(f"Document “{doc_id}” is not in the Document registry (or is not one the app "
                                f"created or imported); import it to link to it")
        hit = self._others.get(doc_id)
        if hit is None or hit[0] != path:
            from .doc_folder import FolderDocument
            other = FolderDocument.open_document(path, registry=reg)     # trusted: opens in place, no clone
            if other.id != doc_id:
                raise K.KernelError(f"The folder registered for Document “{doc_id}” holds a different Document")
            hit = self._others[doc_id] = (path, other)
        return hit

    def link_versions(self, src):
        """The Versions of the Document a cross-Document source names, oldest first (read live from git in the
        registry's trusted folder)."""
        return self._other_document(src.get("document"))[1].versions()

    def _cross_view(self, src):
        doc_id, version = src.get("document"), src.get("version")
        if not version:
            raise K.KernelError(f"The link to Document “{doc_id}” has no Version pinned")
        path, other = self._other_document(doc_id)
        key = (doc_id, path, version)
        view = self._views.get(key)
        if view is None:
            view = self._views[key] = other.open_version(version)
        return view

    def _cross_doc(self, src, kind):
        """The tab document a cross-Document source names, as it was at the pinned Version (read-only)."""
        view = self._cross_view(src)
        tid, label = src.get("tab"), src.get("studio_name") or src.get("name") or "?"
        tab = view.tab(tid) if tid is not None else None
        if tab is None:
            raise K.KernelError(f"Tab “{tid}” (“{label}”) is not in Version “{src.get('version')}” of the "
                                f"linked Document")
        if tab.kind != kind:
            raise K.KernelError(f"Tab “{tid}” (“{tab.name}”) is not a {kind}")
        if tab.doc is None:
            raise K.KernelError(f"Tab “{tid}” (“{tab.name}”) could not be opened: "
                                f"{view.errors.get(tid, 'unknown error')}")
        return tab.doc

    def link_status(self, src):
        """Where a cross-Document source stands: {document, pinned, latest, update_available, error}. `latest` is the
        newest Version of the other Document as the registry's folder has it now; a received Document changes only
        when it is imported again. Never raises."""
        st = {"document": src.get("document"), "pinned": src.get("version"), "latest": None,
              "update_available": False, "error": None}
        try:
            names = [v["name"] for v in self.link_versions(src)]
        except K.KernelError as e:
            st["error"] = str(e)
            return st
        st["latest"] = names[-1] if names else None
        if st["pinned"] not in names:
            st["error"] = f"Version “{st['pinned']}” of Document “{st['document']}” does not exist"
        else:
            st["update_available"] = names[-1] != st["pinned"]
        return st

    def update_link(self, src):
        """One-click update: re-pin a cross-Document source to the newest Version. Returns its name. Rebuilding is the
        caller's refresh (see update_cross_links)."""
        st = self.link_status(src)
        if st["error"] and st["latest"] is None:
            raise K.KernelError(st["error"])
        if st["latest"] is None:
            raise K.KernelError(f"Document “{src.get('document')}” has no Versions")
        src["version"] = st["latest"]
        return st["latest"]

    def _folder_doc(self, src, kind):
        """The tab document a source names inside the open Document, or None when the source is not a
        Document link at all (no tab id, and a legacy uid/path that isn't one of the Document's tabs and
        lies inside its folder -- the old uid/path flow then applies). Raises KernelError for a tab id the
        manifest doesn't have, a tab that failed to open, and a legacy path outside the Document folder,
        which is refused before any file is touched."""
        if is_cross_link(src, getattr(self.folder, "id", None)):
            return self._cross_doc(src, kind)
        folder = self.folder
        if folder is None:
            return None
        tid = src.get("tab")
        label = src.get("studio_name") or src.get("name") or "?"
        if tid is not None:
            tab = folder.tab(tid)
            if tab is None:
                raise K.KernelError(f"Tab “{tid}” (“{label}”) is no longer in the Document")
            if tab.kind != kind:
                raise K.KernelError(f"Tab “{tid}” (“{tab.name}”) is not a {kind}")
            if tab.doc is None:
                raise K.KernelError(f"Tab “{tid}” (“{tab.name}”) could not be opened: "
                                    f"{folder.errors.get(tid, 'unknown error')}")
            return tab.doc
        path = src.get("path")
        if path and self._outside_folder(path):
            raise K.KernelError(f"Link path “{path}” is outside the Document folder; not opened")
        tab = _tab_for_legacy(folder, src, kind)
        return tab.doc if tab is not None else None

    # ------------------------------------------------------------------ finding documents
    def studio_by_uid(self, uid, path=None, load=False):
        """The Part Studio an assembly instance or drawing source links to: an open tab (by id, then by
        file), else the file on disk."""
        doc = self.find_open_studio(uid, path)
        if doc is not None or not (load and path and os.path.exists(path)):
            return doc
        return self._load_cached(path, self._load_document)

    def assembly_by_uid(self, uid, path=None, load=False):
        """The Assembly a drawing source links to: an open tab (by id, then by file), else the file."""
        doc = self.find_open_assembly(uid, path)
        if doc is not None or not (load and path and os.path.exists(path)):
            return doc
        return self._load_cached(path, self._load_assembly)

    def _studio_for(self, src):
        return self._folder_doc(src, "studio") or self.studio_by_uid(src.get("uid"), src.get("path"), load=True)

    def _assembly_for(self, src):
        return self._folder_doc(src, "assembly") or self.assembly_by_uid(src.get("uid"), src.get("path"),
                                                                         load=True)

    def _load_cached(self, path, loader):
        ap = os.path.abspath(path)
        mtime = os.path.getmtime(ap)
        hit = self._loaded.get(ap)
        if hit is not None and hit[0] == mtime:
            return hit[1]
        try:
            doc = loader(ap)
        except Exception:
            return None
        self._loaded[ap] = (mtime, doc)
        return doc

    # ------------------------------------------------------------------ assembly instances
    def resolve_source(self, src):
        """The Part Studio part an assembly instance links to. Read-only: a stale uid/path resolved via its
        fallback (by path, then by loading the file) is not written back -- see repair_links."""
        studio = self._studio_for(src)
        if studio is None:
            raise K.KernelError(f"Part Studio “{src.get('studio_name', '?')}” isn't open")
        p = studio.state.parts.get(_translate_part(studio, src.get("part")))
        if p is None:
            raise K.KernelError(f"The part no longer exists in “{studio.name}”")
        return p

    def resolve_named(self, src):
        """The element map (naming.NamedShape) of the part `resolve_source` returns for `src`, or None when
        the studio is not found or the part carries no map. Lets a connector find its face by name."""
        try:
            studio = self._studio_for(src)
        except K.KernelError:
            return None
        return None if studio is None else studio.state.named(_translate_part(studio, src.get("part")))

    def resolve_studio(self, src):
        """The published output of the Part Studio `src` names (a Document.link_inputs callable): a
        StudioPackage of its parts and sketches, read from the studio's current state. Raises KernelError
        when the studio is not found."""
        from .engine.encode import Encoder, named_elements, sketch_out
        studio = self._studio_for(src)
        if studio is None:
            raise K.KernelError(f"Part Studio “{src.get('studio_name', '?')}” isn't open")
        if self._encoder is None:
            self._encoder = Encoder(meshes=False)     # caches each shape's BREP and hash across calls
        st = studio.state
        parts = {}
        for p in st.parts.values():
            named = st.named(p.id)
            parts[str(p.id)] = PublishedPart(
                id=str(p.id), name=str(p.name), color=tuple(float(c) for c in p.color),
                hidden=p.id in studio.hidden_parts, shape=p.shape, names=named_elements(named),
                hash=self._encoder.brep(p.shape)[1])
        sketches = {str(fid): sketch_out(fid, (studio.feature(fid) or {}).get("name", fid), res)
                    for fid, res in st.sketches.items()}
        pkg = StudioPackage(parts, sketches, dict(getattr(studio, "part_aliases", {})))
        pkg.digest = pkg.digest_for(None)
        return pkg

    def resolve_variables(self, src):
        """{name: value} of the Variable Studio tab `src` names inside the open Document (a Document.var_inputs
        callable), or None when it names no tab of the Document."""
        vs = self._folder_doc(src, "variables")
        return None if vs is None else dict(vs.values()[0])

    def resolve_connectors(self, src):
        """The mate connectors the Part Studio publishes for the part `src` links to, as [{"id", "name", "frame"}]
        (frames in the studio's coordinates, which are the part's), or None when the studio is not found."""
        try:
            studio = self._studio_for(src)
        except K.KernelError:
            return None
        if studio is None:
            return None
        pid = _translate_part(studio, src.get("part"))
        return [{"id": str(fid), "name": c["name"], "frame": {k: [float(v) for v in c["frame"][k]] for k in ("o", "z", "x")}}
                for fid, c in studio.state.connectors.items() if c["part"] == pid]

    # ------------------------------------------------------------------ drawing sources
    def resolve_drawing_source(self, src):
        """The shape a drawing shows (kept as the same object while the model is unchanged, so views aren't
        worked out again for nothing). Read-only, like resolve_source -- except for src["bends"] on a flat
        pattern, which the drawing re-reads from the source on every refresh and so has nowhere else to live."""
        if src.get("kind") == "assembly":
            return self._resolve_assembly_shape(src)
        return self._resolve_studio_shape(src)

    def resolve_drawing_named(self, src):
        """The element map of the shape `resolve_drawing_source` returns for `src`, or None: only a drawing of
        one whole part has one (a compound of parts, an assembly or a flat pattern is a different shape)."""
        if src.get("kind") == "assembly" or src.get("flat") or src.get("part") in (None, "*"):
            return None
        return self.resolve_named(src)

    def _resolve_assembly_shape(self, src):
        asm = self._assembly_for(src)
        if asm is None:
            raise K.KernelError(f"Assembly “{src.get('name', '?')}” isn't open")
        items = [(id(asm.parts[i["id"]].shape), tuple(np.round(np.array(i["T"], float).ravel(), 9)))
                 for i in asm.visible_instances() if i["id"] in asm.parts]
        key = ("asm", asm.uid, tuple(items))
        if key not in self._shape_cache:
            self._shape_cache[key] = K.compound([K.located(asm.parts[i["id"]].shape, np.array(i["T"], float))
                                                 for i in asm.visible_instances() if i["id"] in asm.parts])
        return self._shape_cache[key]

    def _resolve_studio_shape(self, src):
        studio = self._studio_for(src)
        if studio is None:
            raise K.KernelError(f"Part Studio “{src.get('studio_name', '?')}” isn't open")
        if src.get("part") in (None, "*"):
            parts = [p for p in studio.state.parts.values() if p.id not in studio.hidden_parts]
            if not parts:
                raise K.KernelError(f"“{studio.name}” has no parts")
            if len(parts) == 1:
                return parts[0].shape
            key = ("studio", studio.uid, tuple(id(p.shape) for p in parts))
            if key not in self._shape_cache:
                self._shape_cache[key] = K.compound([p.shape for p in parts])
            return self._shape_cache[key]
        p = studio.state.parts.get(_translate_part(studio, src["part"]))
        if p is None:
            raise K.KernelError(f"The part no longer exists in “{studio.name}”")
        if src.get("flat"):
            if p.id not in getattr(studio.state, "sheet", {}):
                raise K.KernelError(f"“{p.name}” isn't sheet metal any more")
            fp = self.flat_pattern_of(studio.state, p.id)
            # the drawing reads bend lines back out of the source on every refresh (see drawing.py), so this
            # is the one piece of derived data resolving does write back
            src["bends"] = [[list(map(float, a)), list(map(float, b)), lab] for a, b, lab in fp.bend_lines]
            key = ("flat", studio.uid, p.id, id(p.shape))
            if key not in self._shape_cache:
                self._shape_cache[key] = fp.solid()
            return self._shape_cache[key]
        return p.shape


# ------------------------------------------------------------------ what a tab consumes
SOURCE_FEATURES = {"variable_studio_ref": "source"}     # feature type -> the params key holding its source


def feature_source(feature):
    """The source dict a studio feature consumes, or None (a feature type outside SOURCE_FEATURES, no source)."""
    key = SOURCE_FEATURES.get(feature.get("type")) if isinstance(feature, dict) else None
    src = (feature.get("params") or {}).get(key) if key else None
    return src if isinstance(src, dict) else None


def source_edit(edit):
    """The source dict an engine edit hands a tab (add_feature, replace_feature, set_source, or a set_param
    that sets a whole source key), else None."""
    if not isinstance(edit, dict):
        return None
    op = edit.get("op")
    if op in ("add_feature", "replace_feature"):
        return feature_source(edit.get("feature"))
    if op == "set_source":
        return edit.get("source") if isinstance(edit.get("source"), dict) else None
    if op == "set_param":
        key = edit.get("key")
        key = key[0] if isinstance(key, (list, tuple)) and len(key) == 1 else key
        if key in SOURCE_FEATURES.values() and isinstance(edit.get("value"), dict):
            return edit["value"]
    return None


def tab_sources(kind, data):
    """Every source dict of a tab's JSON (or of a loaded document, which has the same attributes): an
    assembly's instances, a drawing's source, a studio's features listed in SOURCE_FEATURES."""
    def get(name, default):
        v = data.get(name, default) if isinstance(data, dict) else getattr(data, name, default)
        return default if v is None else v
    if kind == "assembly":
        srcs = [i.get("source") for i in get("instances", [])]
    elif kind == "drawing":
        srcs = [get("source", None)]
    elif kind == "studio":
        srcs = [feature_source(f) for f in get("features", [])]
    else:
        srcs = []
    return [s for s in srcs if isinstance(s, dict)]


# ------------------------------------------------------------------ cross-Document pins
def cross_sources(folder):
    """Every cross-Document source the Document's assemblies and drawings hold: (tab, source) pairs."""
    out = []
    for tab in folder.tabs:
        if tab.doc is None:
            continue
        out += [(tab, s) for s in tab_sources(tab.kind, tab.doc) if is_cross_link(s, folder.id)]
    return out


def cross_link_updates(folder):
    """The cross-Document pins that have a newer Version: a list of status dicts (see LinkResolver.link_status)
    each with the `tab` id that holds it. One entry per distinct (Document, pinned Version)."""
    seen, out = set(), []
    for tab, src in cross_sources(folder):
        st = folder.resolver.link_status(src)
        key = (st["document"], st["pinned"])
        if st["update_available"] and key not in seen:
            seen.add(key)
            out.append(dict(st, tab=tab.id))
    return out


def update_cross_links(folder, document=None):
    """One-click update: re-pin every cross-Document source (of `document`, or all) to its Document's newest Version,
    mark the holding tabs dirty and rebuild. Returns the number of sources re-pinned."""
    n = 0
    for tab, src in cross_sources(folder):
        if document is not None and src.get("document") != document:
            continue
        st = folder.resolver.link_status(src)
        if st["update_available"]:
            src["version"] = st["latest"]
            tab.doc.dirty = True
            n += 1
    if n:
        folder.refresh_links()
    return n


# ------------------------------------------------------------------ save-time link repair
def _tab_for_legacy(folder, src, kind):
    """The Document tab a legacy uid + path source names: a tab whose document has that uid, else the tab whose
    file is that path. Reads no file -- only compares against what the manifest and the open tabs hold."""
    uid = src.get("uid")
    for tab in folder.tabs:
        if tab.kind == kind and tab.doc is not None and uid and getattr(tab.doc, "uid", None) == uid:
            return tab
    path = src.get("path")
    if path:
        full = os.path.realpath(os.path.join(folder.path, path))
        for tab in folder.tabs:
            if tab.kind == kind and tab.file and \
                    os.path.realpath(os.path.join(folder.path, tab.file)) == full:
                return tab
    return None


def _to_tab_link(folder, src):
    """The uid + path -> tab id step of the link-format rewrite: turn a legacy source into `{tab, part}`,
    translating its part id through the studio's alias table (the same `_translate_part` step as a plain
    repair) so the file moves to tab ids and new-syntax part ids in a single save. True if it changed."""
    kind = src.get("kind")
    if kind not in ("studio", "assembly") or src.get("tab") is not None:
        return False
    tab = _tab_for_legacy(folder, src, kind)
    if tab is None:
        return False
    for k in ("uid", "path", "relpath"):
        src.pop(k, None)
    src["tab"] = tab.id
    if kind == "studio":
        src["studio_name"] = tab.name
        src["part"] = _translate_part(tab.doc, src.get("part")) if tab.doc is not None else src.get("part")
    else:
        src["name"] = tab.name
    return True


def migrate_links_to_tab_ids(folder):
    """Convert every legacy uid + path source held by the Document's assemblies and drawings to a tab id, and
    mark each changed tab dirty so the next save writes it. Run once as a Document opens; idempotent. Returns
    the number of sources converted."""
    n = 0
    for tab in folder.tabs:
        doc = tab.doc
        if doc is None:
            continue
        changed = sum(_to_tab_link(folder, s) for s in tab_sources(tab.kind, doc))
        if changed:
            doc.dirty = True
            n += changed
    return n


def _repair_studio_link(resolver, src, part_name=False):
    if resolver.folder is not None:
        _to_tab_link(resolver.folder, src)
        if src.get("tab") is not None:
            tab = resolver.folder.tab(src["tab"])
            if tab is None or tab.doc is None:
                return
            src["studio_name"] = tab.name
            src["part"] = _translate_part(tab.doc, src.get("part"))
            if part_name:
                p = tab.doc.state.parts.get(src.get("part"))
                if p is not None:
                    src["part_name"] = p.name
        return
    studio = resolver.studio_by_uid(src.get("uid"), src.get("path"), load=True)
    if studio is None:
        return
    src["studio_name"] = studio.name
    if studio.path:
        src["path"] = os.path.abspath(studio.path)
    if studio.uid != src.get("uid"):
        src["uid"] = studio.uid
    translated = _translate_part(studio, src.get("part"))
    if translated != src.get("part"):
        src["part"] = translated
    if part_name:
        p = studio.state.parts.get(src.get("part"))
        if p is not None:
            src["part_name"] = p.name


def _repair_assembly_link(resolver, src):
    if resolver.folder is not None:
        _to_tab_link(resolver.folder, src)
        tab = resolver.folder.tab(src["tab"]) if src.get("tab") is not None else None
        if tab is not None:
            src["name"] = tab.name
        return
    asm = resolver.assembly_by_uid(src.get("uid"), src.get("path"), load=True)
    if asm is None:
        return
    src["name"] = asm.name
    if asm.path:
        src["path"] = os.path.abspath(asm.path)
    if asm.uid != src.get("uid"):
        src["uid"] = asm.uid


def repair_source(resolver, src, part_name=False):
    """Write back one source's current uid/path/display name, repairing a stale link the way resolving used
    to (for either an assembly instance's source or a drawing's source). Call this only right before saving."""
    if is_cross_link(src, getattr(resolver.folder, "id", None)):
        return            # names a tab of another Document: the pin is the link, nothing in this folder to repair
    kind = src.get("kind")
    if kind == "studio":
        _repair_studio_link(resolver, src, part_name=part_name)
    elif kind == "assembly":
        _repair_assembly_link(resolver, src)


def repair_links(resolver, doc):
    """repair_source for every source a document holds -- every instance of an AssemblyDoc, or the single
    source of a DrawingDoc. Called from MainWindow's save path (and autosave), never from resolving."""
    for src in tab_sources(doc.kind, doc):
        repair_source(resolver, src, part_name=doc.kind == "drawing")


# ------------------------------------------------------------------ the Link graph
import graphlib  # noqa: E402
from typing import NamedTuple  # noqa: E402


class Edge(NamedTuple):
    """What one tab consumed of another: the kind of the upstream tab, the part ids it read (() or "*": all),
    and the hash of exactly that output as it was handed over."""
    kind: str | None
    keys: tuple
    digest: str | None
    declared: bool = False


class LinkCycle(Exception):
    """The Link graph has a cycle. `tabs` lists it, first tab repeated last (graphlib.CycleError.args[1])."""

    def __init__(self, tabs):
        super().__init__(" -> ".join(map(str, tabs)))
        self.tabs = list(tabs)


class LinkGraph:
    """Which tab consumed which tab's output, by tab id. Lives in the process that owns the tabs (the parent)."""

    def __init__(self):
        self._in = {}            # consumer -> {upstream: Edge}
        self.rebuilds = {}       # tab -> times the engine rebuilt it because an input changed
        self.last_order = []     # the tabs the last propagation rebuilt, in order

    def add(self, consumer, upstream, edge):
        self._in.setdefault(consumer, {})[upstream] = edge

    def set_inputs(self, consumer, edges):
        new = {u: e for u, e in self._in.get(consumer, {}).items() if e.declared}
        new.update(edges)
        if new:
            self._in[consumer] = new
        else:
            self._in.pop(consumer, None)

    def forget(self, tab):
        self._in.pop(tab, None)

    def inputs(self, consumer):
        return dict(self._in.get(consumer, {}))

    def edges(self):
        """[(consumer, upstream)], in the order they were recorded."""
        return [(c, u) for c, ins in self._in.items() for u in ins]

    def dependents(self, upstream):
        return [c for c, ins in self._in.items() if upstream in ins]

    def order(self, origin):
        """The tabs that consume `origin`, directly or not, in an order that puts every tab after the tabs it
        consumes, each once. A tab reached by two paths (a fan-in) is still one tab. Raises LinkCycle if
        some of them consume each other."""
        nodes, todo = {origin: None}, [origin]
        while todo:
            for c in self.dependents(todo.pop()):
                if c not in nodes:
                    nodes[c] = None
                    todo.append(c)
        if len(nodes) == 1 and origin not in self._in.get(origin, {}):
            return []
        preds = {n: tuple(u for u in self._in.get(n, {}) if u in nodes) for n in nodes}
        try:
            order = list(graphlib.TopologicalSorter(preds).static_order())
        except graphlib.CycleError as e:
            raise LinkCycle(e.args[1]) from e
        return [n for n in order if n != origin]
