"""Link resolution: finds the Part Studio or Assembly behind an assembly instance's source or a drawing's
source. Headless -- no Qt, no UI widget. Resolving never mutates a source dict -- when a link has drifted
(a renamed Part Studio, a moved file) `repair_links` writes the fix back, meant to be called just before
saving rather than on every resolve.

`LinkResolver` is headless: it has no idea what a "tab" is. The caller (normally MainWindow) injects
`find_open_studio` / `find_open_assembly` callbacks so documents already open in a tab are reused instead of
reloaded from disk, and a `flat_pattern_of` callback so a flat pattern already computed for the viewport is
reused too. Without those, everything falls back to loading straight from disk, cached by path and mtime --
which is exactly what the tests use.
"""
from __future__ import annotations

import os

import numpy as np

from . import kernel as K
from .document import Document


def _no_open_doc(uid, path):
    return None


def _default_flat_pattern_of(state, pid):
    from . import sheetmetal as SM
    return SM.flat_pattern(state.parts[pid].shape, state.sheet[pid])


class LinkResolver:
    """callable(src) -> features.Part for AssemblyDoc.resolver, or callable(src) -> TopoDS shape for
    DrawingDoc.resolver (use resolve_source / resolve_drawing_source directly for that)."""

    def __init__(self, find_open_studio=None, find_open_assembly=None, flat_pattern_of=None, load_document=None,
                 load_assembly=None):
        self.find_open_studio = find_open_studio or _no_open_doc
        self.find_open_assembly = find_open_assembly or _no_open_doc
        self.flat_pattern_of = flat_pattern_of or _default_flat_pattern_of
        self._load_document = load_document or Document.load
        self._load_assembly = load_assembly or self._default_load_assembly
        self._loaded = {}          # abs path -> (mtime, doc), for studios/assemblies loaded from disk
        self._shape_cache = {}     # compound cache for drawing sources, keyed by (kind, uid/id, ...)

    def _default_load_assembly(self, path):
        from .assembly import AssemblyDoc
        return AssemblyDoc.load(path, resolver=self.resolve_source)

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
        studio = self.studio_by_uid(src.get("uid"), src.get("path"), load=True)
        if studio is None:
            raise K.KernelError(f"Part Studio “{src.get('studio_name', '?')}” isn't open")
        p = studio.state.parts.get(src.get("part"))
        if p is None:
            raise K.KernelError(f"The part no longer exists in “{studio.name}”")
        return p

    # ------------------------------------------------------------------ drawing sources
    def resolve_drawing_source(self, src):
        """The shape a drawing shows (kept as the same object while the model is unchanged, so views aren't
        worked out again for nothing). Read-only, like resolve_source -- except for src["bends"] on a flat
        pattern, which the drawing re-reads from the source on every refresh and so has nowhere else to live."""
        if src.get("kind") == "assembly":
            return self._resolve_assembly_shape(src)
        return self._resolve_studio_shape(src)

    def _resolve_assembly_shape(self, src):
        asm = self.assembly_by_uid(src.get("uid"), src.get("path"), load=True)
        if asm is None:
            raise K.KernelError(f"Assembly “{src.get('name', '?')}” isn't open")
        items = [(id(asm.parts[i["id"]].shape), tuple(np.round(np.array(i["T"], float).ravel(), 9)))
                 for i in asm.visible_instances() if i["id"] in asm.parts]
        key = ("asm", src.get("uid"), tuple(items))
        if key not in self._shape_cache:
            self._shape_cache[key] = K.compound([K.located(asm.parts[i["id"]].shape, np.array(i["T"], float))
                                                 for i in asm.visible_instances() if i["id"] in asm.parts])
        return self._shape_cache[key]

    def _resolve_studio_shape(self, src):
        studio = self.studio_by_uid(src.get("uid"), src.get("path"), load=True)
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
        p = studio.state.parts.get(src["part"])
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


# ------------------------------------------------------------------ save-time link repair
def _repair_studio_link(resolver, src, part_name=False):
    studio = resolver.studio_by_uid(src.get("uid"), src.get("path"), load=True)
    if studio is None:
        return
    src["studio_name"] = studio.name
    if studio.path:
        src["path"] = os.path.abspath(studio.path)
    if studio.uid != src.get("uid"):
        src["uid"] = studio.uid
    if part_name:
        p = studio.state.parts.get(src.get("part"))
        if p is not None:
            src["part_name"] = p.name


def _repair_assembly_link(resolver, src):
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
    kind = src.get("kind")
    if kind == "studio":
        _repair_studio_link(resolver, src, part_name=part_name)
    elif kind == "assembly":
        _repair_assembly_link(resolver, src)


def repair_links(resolver, doc):
    """repair_source for every source a document holds -- every instance of an AssemblyDoc, or the single
    source of a DrawingDoc. Called from MainWindow's save path (and autosave), never from resolving."""
    if doc.kind == "assembly":
        for inst in doc.instances:
            repair_source(resolver, inst["source"])
    elif doc.kind == "drawing" and doc.source is not None:
        repair_source(resolver, doc.source, part_name=True)
