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
import zlib

import numpy as np
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.BRepTools import BRepTools
from OCP.TopTools import TopTools_FormatVersion

from .. import kernel as K
from .contract import MateConnectorOut, MeshOut, PartOut, SketchOut, plain

_BREP_VERSION = TopTools_FormatVersion.TopTools_FormatVersion_VERSION_1
_KIND_OF_LETTER = {"F": "face", "E": "edge", "V": "vertex"}
_CACHE_LIMIT = 512


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
    """The part's element-name map, keyed by kind and 1-based MapShapes index. Parts carry no map yet;
    once one is attached as a naming.NamedShape (`named` attribute) of this very shape, its names come
    through here. A map built for a different shape (a moved or re-made one) would index the wrong
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


def mesh_out(shape) -> MeshOut:
    m = K.tessellate(shape)
    return MeshOut(verts=np.asarray(m.verts, np.float32), normals=np.asarray(m.normals, np.float32),
                   tris=np.asarray(m.tris, np.uint32), tri_face=np.asarray(m.tri_face, np.int32) + 1,
                   edge_polys=[np.asarray(p, np.float32) for p in m.edge_polys],
                   vertices=np.asarray(m.vertices, np.float32),
                   face_ranges=[(int(a), int(b)) for a, b in m.face_ranges])


class Encoder:
    """Shape -> (BREP bytes, hash) and hash -> mesh, cached so an unchanged part is neither written nor
    meshed again on the next `outputs`. Entries hold the shape itself, so its id() cannot be reused."""

    def __init__(self, meshes=True):
        self.meshes = meshes
        self._brep = {}      # id(shape) -> (shape, compressed brep, hash)
        self._mesh = {}      # hash -> MeshOut

    def brep(self, shape):
        hit = self._brep.get(id(shape))
        if hit is None or hit[0] is not shape:
            text = brep_text(shape)
            hit = (shape, zlib.compress(text, 6), hashlib.sha256(text).hexdigest())
            if len(self._brep) >= _CACHE_LIMIT:
                self._brep.clear()
            self._brep[id(shape)] = hit
        return hit[1], hit[2]

    def seed(self, shape, data, digest):
        """Record a shape rebuilt from (compressed BREP `data`, `digest`) -- a part another tab sent -- so it
        is passed on with the very bytes and hash it came with, never re-written."""
        if len(self._brep) >= _CACHE_LIMIT:
            self._brep.clear()
        self._brep[id(shape)] = (shape, data, digest)

    def mesh(self, shape, digest):
        if not self.meshes:
            return None
        hit = self._mesh.get(digest)
        if hit is None:
            hit = mesh_out(shape)
            if len(self._mesh) >= _CACHE_LIMIT:
                self._mesh.clear()
            self._mesh[digest] = hit
        return hit

    def part(self, part, hidden=False, mesh=True) -> PartOut:
        """A features.Part as output, in its own coordinates (`mesh=False`: without its display mesh)."""
        data, digest = self.brep(part.shape)
        fc = None
        if part.face_colors:      # kept 0-based by features.py; 1-based here, like every output index
            fc = {int(i) + 1: tuple(float(c) for c in rgb) for i, rgb in part.face_colors.items()}
        return PartOut(id=str(part.id), name=str(part.name), color=tuple(float(c) for c in part.color),
                       hidden=bool(hidden), brep=data, hash=digest, names=element_names(part),
                       mesh=self.mesh(part.shape, digest) if mesh else None, face_colors=fc)


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
