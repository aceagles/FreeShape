"""Turning OCCT shapes into the plain output data of contract.py. The one module of the engine that touches
OCCT objects on the way out; nothing it returns holds one.

BREP is written the way FreeCAD writes it (Part::TopoShape::exportBrep, OCCT >= 7.6): without triangulation
or normals, in format version 1 -- meshing a part for display attaches triangles to its faces in place, so
a BREP written with them would change depending on whether the part had been meshed yet.

It is also written from a geometry copy (BRepBuilderAPI_Copy), not from the part's shape itself. Features
share sub-shapes between rebuilds (a sketch region's edges are reused by every extrude of it), and each
build adds its own pcurves to those shared edges. Written as is, the same part's BREP would carry stray
pcurves of earlier builds, so its text -- and hash -- would depend on the edit history, and even change
after a later build. The copy rebuilds each edge with pcurves only for faces of this shape, so the same
geometry writes the same text whichever way it was reached. The copy keeps every sub-shape's MapShapes
position (tests/test_engine.py checks this against the part's own shape).
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import zlib
from collections import OrderedDict

import numpy as np
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.BRepTools import BRepTools
from OCP.TopTools import TopTools_FormatVersion

from .. import kernel as K
from .contract import MateConnectorOut, MeshOut, PartOut, SketchOut, plain

_BREP_VERSION = TopTools_FormatVersion.TopTools_FormatVersion_VERSION_1
_KIND_OF_LETTER = {"F": "face", "E": "edge", "V": "vertex"}
_DIAG_STEPS = 4       # a model's diagonal is rounded to a step of 2**(1/4), so a small edit keeps every mesh cached
_CACHE_MIN = 512      # the least each Encoder cache keeps; `Encoder.fit` raises it to twice the model's parts


def brep_text(shape) -> bytes:
    """The canonical BREP text of a shape: a geometry copy (no stray pcurves), without triangulation
    (FreeCAD's exportBrep settings)."""
    clean = BRepBuilderAPI_Copy(shape, True, False).Shape()
    buf = io.BytesIO()
    BRepTools.Write_s(clean, buf, False, False, _BREP_VERSION)
    return buf.getvalue()


def empty_names() -> dict:
    return {"face": {}, "edge": {}, "vertex": {}}


def element_names(part) -> dict:
    """The part's element-name map, keyed by kind and 1-based MapShapes index. The engine attaches each
    part's naming.NamedShape as its `named` attribute before encoding; a part without one has no names. A map built for a different shape (a moved or re-made one) would index the wrong
    elements, so it is dropped rather than passed on."""
    named = getattr(part, "named", None)
    names = getattr(named, "names", None)
    shape = getattr(named, "shape", None)
    out = empty_names()
    if not names or shape is None or not shape.IsSame(part.shape):
        return out
    for (letter, index), name in names.items():
        kind = _KIND_OF_LETTER.get(letter)
        if kind is not None:
            out[kind][int(index)] = str(name)
    return out


def named_elements(named) -> dict:
    """The element-name map of a naming.NamedShape (a sketch's regions), by kind and 1-based index."""
    out = empty_names()
    for (letter, index), name in (getattr(named, "names", None) or {}).items():
        kind = _KIND_OF_LETTER.get(letter)
        if kind is not None:
            out[kind][int(index)] = str(name)
    return out


def mesh_out(shape, bases=None, model_diag=None) -> MeshOut:
    """A shape's display mesh. `model_diag`: the diagonal of the model it sits in, which coarsens a small part's
    faces (K.tessellate). STL/3MF export meshes on its own (meshexport.py) and does not use this."""
    m = K.tessellate(shape, bases=bases, model_diag=model_diag)
    return MeshOut(verts=np.asarray(m.verts, np.float32), normals=np.asarray(m.normals, np.float32),
                   tris=np.asarray(m.tris, np.uint32), tri_face=np.asarray(m.tri_face, np.int32) + 1,
                   edge_polys=[np.asarray(p, np.float32) for p in m.edge_polys],
                   vertices=np.asarray(m.vertices, np.float32),
                   face_ranges=[(int(a), int(b)) for a, b in m.face_ranges])


def _touch(cache, key):
    """The entry under `key`, now the most recently used, or None."""
    hit = cache.get(key)
    if hit is not None:
        cache.move_to_end(key)
    return hit


class Encoder:
    """Shape -> (BREP bytes, hash) and hash -> mesh, cached so an unchanged part is neither written nor
    meshed again on the next `outputs`. Entries hold the shape itself, so its id() cannot be reused.

    Each cache drops its least recently used entries past `limit`. `fit(n)` sets the limit from the model:
    twice its part count, never under _CACHE_MIN, so one `outputs` never evicts what it is about to read
    again. A fixed 512 used to clear the caches partway through a model with more parts than that, and every
    edit then encoded the whole model again (I1.2)."""

    def __init__(self, meshes=True, placed=False):
        self.meshes = meshes
        self.placed = placed     # `part(placed=True)` may send a placed part (PartOut.loc) -- the window reads it
        self.limit = _CACHE_MIN
        self.model_diag = None   # the model's diagonal, rounded (`set_model`): meshes are made relative to it
        self.release = False     # clean a shape's triangulation once its MeshOut exists (the engine worker sets it)
        self._brep = OrderedDict()       # id(shape) -> (shape, compressed brep, hash)
        self._mesh = OrderedDict()       # hash -> MeshOut
        self._bases = OrderedDict()      # hash(unplaced shape) -> (unplaced shape, compressed brep, hash)
        self._names = OrderedDict()      # name content -> the one shared dict (see `names`)
        self._mesh_bases = OrderedDict()  # unplaced geometry -> its mesh, so a placed copy is not meshed again
        #                                   (K.tessellate)

    def fit(self, parts):
        """Size the caches for a model of `parts` parts (StudioTab.outputs calls it before encoding)."""
        self.limit = max(_CACHE_MIN, 2 * int(parts))
        for cache in (self._brep, self._mesh, self._bases, self._mesh_bases, self._names):
            self._trim(cache)

    def set_model(self, shapes):
        """Say which shapes make up the model the next parts are meshed in (StudioTab.outputs calls it before
        encoding). Its diagonal, rounded to a step of 2**(1/4) so a small edit does not change every mesh's key,
        sets how coarsely small parts are meshed (K.MODEL_DEFL)."""
        lo = hi = None
        for sh in shapes:
            a, b = K.bbox(sh)
            if np.any(b > a):
                lo = a if lo is None else np.minimum(lo, a)
                hi = b if hi is None else np.maximum(hi, b)
        diag = 0.0 if lo is None else float(np.linalg.norm(hi - lo))
        self.model_diag = 2.0 ** (round(math.log2(diag) * _DIAG_STEPS) / _DIAG_STEPS) if diag > 0 else None

    def _trim(self, cache):
        while len(cache) > self.limit:
            cache.popitem(last=False)

    def _put(self, cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        self._trim(cache)

    def brep(self, shape):
        hit = _touch(self._brep, id(shape))
        if hit is None or hit[0] is not shape:
            hit = (shape, *self._write(shape))
            self._put(self._brep, id(shape), hit)
        return hit[1], hit[2]

    @staticmethod
    def _write(shape):
        """(compressed BREP, hash) of a shape, uncached."""
        text = brep_text(shape)
        return zlib.compress(text, 6), hashlib.sha256(text).hexdigest()

    def seed(self, shape, data, digest):
        """Record a shape rebuilt from (compressed BREP `data`, `digest`) -- a part another tab sent -- so it
        is passed on with the very bytes and hash it came with, never re-written."""
        self._put(self._brep, id(shape), (shape, data, digest))

    def mesh(self, shape, digest):
        if not self.meshes:
            return None
        key = (digest, self.model_diag)       # the same geometry in a bigger model is meshed coarser
        hit = _touch(self._mesh, key)
        if hit is None:
            hit = mesh_out(shape, self._mesh_bases, self.model_diag)
            self._put(self._mesh, key, hit)
            self._trim(self._mesh_bases)
            if self.release:
                # The MeshOut holds what the triangulation held, and nothing in the worker reads it again (a
                # later mesh makes it afresh). It is a cache on the TShape, so it can be dropped even where
                # shapes are shared; the worker is the only place this is on.
                BRepTools.Clean_s(shape)
        return hit

    def base(self, shape):
        """(BREP bytes, hash) of a shape with its location taken off: the same object for every placed copy of
        one geometry, so it is written, hashed and sent once. Kept here only: `shape` is a fresh unplaced copy
        on every call, so filing it under its id() in the BREP cache would never be hit again."""
        key = hash(shape)
        hit = _touch(self._bases, key)
        if hit is None or not hit[0].IsSame(shape):
            hit = (shape, *self._write(shape))
            self._put(self._bases, key, hit)
        return hit[1], hit[2]

    def _placement(self, shape):
        """(unplaced shape, 4x4 matrix) of a placed shape (a rotation and/or a move, not a mirror), else None."""
        loc = shape.Location()
        if loc.IsIdentity() or loc.Transformation().IsNegative():
            return None
        return shape.Located(K.TopLoc_Location()), K.location_matrix(loc)

    def names(self, part) -> dict:
        """`element_names(part)`, as one shared dict per distinct content: the placed copies of a geometry name
        their elements alike, so they hold the same object, which pickle and the wire send once. Callers must
        treat it as read-only."""
        out = element_names(part)
        key = tuple((k, tuple(sorted(d.items()))) for k, d in out.items())
        hit = _touch(self._names, key)
        if hit is None:
            hit = out
            self._put(self._names, key, hit)
        return hit

    def part(self, part, hidden=False, mesh=True, placed=False) -> PartOut:
        """A features.Part as output, in its own coordinates (`mesh=False`: without its display mesh).
        `placed=True`: a part whose shape is a placed copy goes as its unplaced geometry plus `loc` (see
        contract.PartOut), so an assembly's hundred identical screws are written, meshed and sent once."""
        place = self._placement(part.shape) if placed else None
        loc = base = None
        if place is None:
            data, digest = self.brep(part.shape)
            geo, geo_digest = part.shape, digest
        else:
            geo, M = place
            data, base = self.base(geo)
            geo_digest = base
            loc = [[float(x) for x in row] for row in M]
            digest = hashlib.sha256((base + json.dumps(loc)).encode()).hexdigest()
        fc = None
        if part.face_colors:      # kept 0-based by features.py; 1-based here, like every output index
            fc = {int(i) + 1: tuple(float(c) for c in rgb) for i, rgb in part.face_colors.items()}
        return PartOut(id=str(part.id), name=str(part.name), color=tuple(float(c) for c in part.color),
                       hidden=bool(hidden), brep=data, hash=digest, names=self.names(part),
                       mesh=self.mesh(geo, geo_digest) if mesh else None, face_colors=fc, loc=loc,
                       base=base or "")


def sketch_out(fid, name, result) -> SketchOut:
    """A features.SketchResult as output: its plane, the solved sketch and a signature per region."""
    pl = result.plane
    plane = {"origin": [float(v) for v in pl.origin], "xdir": [float(v) for v in pl.xdir],
             "normal": [float(v) for v in pl.normal]}
    sk = result.sketch.to_dict() if hasattr(result.sketch, "to_dict") else {}
    regions = plain(list(result.region_info or []))
    names = named_elements(result.named)
    region_names = [None if n is None else str(n) for n in (result.region_names or [])]
    body = {"plane": plane, "sketch": plain(sk), "regions": regions, "names": names, "region_names": region_names}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return SketchOut(id=str(fid), name=str(name), plane=plane, sketch=plain(sk), regions=regions,
                     names=names, region_names=region_names, hash=digest)


def connector_out(fid, entry) -> MateConnectorOut:
    """A studio's mate_connector (ModelState.connectors[fid]) as published output."""
    fr = entry["frame"]
    frame = {k: [float(v) for v in fr[k]] for k in ("o", "z", "x")}
    sketch = entry.get("sketch") or ""
    body = {"name": str(entry["name"]), "part": "" if entry["part"] is None else str(entry["part"]), "frame": frame}
    if sketch:          # left out when empty, so a part-owned connector keeps the hash it always had
        body["sketch"] = str(sketch)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return MateConnectorOut(id=str(fid), name=body["name"], part=body["part"], frame=frame, hash=digest,
                            sketch=str(sketch))
