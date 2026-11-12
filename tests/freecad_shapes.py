"""Shared pieces for the FreeCAD kernel-stress shapes in tests/data/freecad (see NOTICE.md there):
the case table, loaders and the mesh / fillet helpers. Used by test_golden_freecad.py and test_known_bugs.py."""
from __future__ import annotations

import functools
import gzip
import os
import tempfile
from dataclasses import dataclass

import numpy as np
from OCP.BRep import BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepGProp import BRepGProp
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_Line, GeomAbs_Plane
from OCP.TopAbs import TopAbs_IN, TopAbs_REVERSED
from OCP.TopoDS import TopoDS_Shape
from OCP.gp import gp_Pnt

from conftest import ANALYTIC, FREEFORM, ROOT, shape_fingerprint
from freeshape import kernel as K

DATA = os.path.join(ROOT, "tests", "data", "freecad")


@dataclass(frozen=True)
class Case:
    id: str
    path: str                  # relative to tests/data/freecad
    loader: str                # "brep" | "brep.gz" | "step"
    valid: bool                # expected BRepCheck validity of the file as shipped
    solids: int
    shells: int
    tol: float                 # golden tolerance class: ANALYTIC or FREEFORM
    tris: int                  # triangle count of K.tessellate at the default deflection (OCP 7.9 = 8.0)
    ops: tuple = ("cut", "fillet")
    watertight: bool = True    # expected to mesh watertight (closed solids only)
    empty_face_ok: bool = False


CASES = [
    Case("unorientable_solid", "brep/unorientableSolid.brep", "brep", False, 1, 1, ANALYTIC, 790,
         ops=("fuse_far",)),
    Case("countersink_cone", "brep/countersink_cone.brep", "brep", True, 0, 0, ANALYTIC, 0,
         ops=(), empty_face_ok=True),
    Case("helix1", "brep/helix1.brep", "brep", True, 1, 1, FREEFORM, 2226, ops=("cut",)),
    Case("part_cylinder1", "brep/part_cylinder1.brep", "brep", True, 1, 1, ANALYTIC, 164, ops=("cut",)),
    Case("roof_subtraction", "brep/roof_subtraction.brep", "brep", True, 1, 1, ANALYTIC, 20),
    Case("cylinder7_v2", "brep/cylinder7_V2.brep", "brep", True, 1, 1, ANALYTIC, 767, ops=("cut",),
         watertight=False),
    Case("cylinder_seam3", "brep/cylinderSeam3.brep", "brep", True, 1, 1, ANALYTIC, 236),
    Case("cylinder_seam10", "brep/cylinderSeam10.brep", "brep", True, 1, 1, FREEFORM, 1990, watertight=False),
    Case("spline", "brep/spline.brep", "brep", True, 1, 1, FREEFORM, 58),
    Case("shelf", "brep/shelf.brep", "brep", True, 1, 7, ANALYTIC, 156),
    Case("refine_breaks_cylinder", "brep/refine_breaks_cylinder.brep", "brep", True, 1, 1, ANALYTIC, 4214,
         ops=("cut",)),
    Case("blocks144", "brep/blocks144.brep.gz", "brep.gz", True, 1, 1, ANALYTIC, 2798, ops=("cut",)),
    Case("holes_in_face2", "brep/holesInFace2.brep", "brep", True, 1, 1, ANALYTIC, 120),
    Case("schenkel", "step/Schenkel.stp", "step", True, 1, 1, FREEFORM, 11672),
    Case("groove002", "brep/groove002.brep", "brep", True, 1, 1, ANALYTIC, 1391),
    Case("hole_puzzle", "brep/hole_puzzle_partshape4.brep.gz", "brep.gz", True, 1, 2, FREEFORM, 16041,
         ops=("cut",)),
    Case("assembly_shape015", "brep/assembly_shape015.brep", "brep", True, 1, 1, ANALYTIC, 2232),
]


def data_path(rel):
    return os.path.join(DATA, rel)


def _read_brep(path):
    shape = TopoDS_Shape()
    assert BRepTools.Read_s(shape, path, BRep_Builder()), f"BRepTools.Read_s failed on {path}"
    return shape


@functools.lru_cache(maxsize=None)
def load(case: Case):
    """The shape of a case. Treat it as read-only: cached for the whole session."""
    path = data_path(case.path)
    if case.loader == "brep":
        return _read_brep(path)
    if case.loader == "brep.gz":
        with gzip.open(path, "rb") as fh, tempfile.NamedTemporaryFile(suffix=".brep") as tmp:
            tmp.write(fh.read())
            tmp.flush()
            return _read_brep(tmp.name)
    if case.loader == "step":
        items = K.import_step_with_colors(path)
        assert len(items) == 1, [n for _, n, _, _ in items]
        return items[0][0]
    raise ValueError(case.loader)


@functools.lru_cache(maxsize=None)
def fingerprint(case: Case):
    """shape_fingerprint of the loaded shape (the integration of freeform faces is the slow part)."""
    return shape_fingerprint(load(case))


@functools.lru_cache(maxsize=None)
def mesh(case: Case):
    return K.tessellate(load(case))


# --------------------------------------------------------------------------- mesh checks

def referenced_normals(m):
    """Lengths of the normals of the vertices some triangle uses (the renderer never reads the others)."""
    ref = np.unique(m.tris)
    return np.linalg.norm(m.normals[ref].astype(np.float64), axis=1)


def open_mesh_edges(m, shape):
    """Number of mesh edges not shared by exactly two triangles, after merging vertices closer than
    1e-6 x the bounding-box diagonal. 0 means the mesh of a closed solid is watertight."""
    lo, hi = K.bbox(shape)
    tol = 1e-6 * float(np.linalg.norm(hi - lo))
    q = np.round(m.verts.astype(np.float64) / tol).astype(np.int64)
    _, inv = np.unique(q, axis=0, return_inverse=True)
    t = inv.ravel()[m.tris.astype(np.int64)]
    t = t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])]     # collapsed by the merge
    e = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
    _, counts = np.unique(e, axis=0, return_counts=True)
    return int((counts != 2).sum())


# --------------------------------------------------------------------------- operation inputs

def half_box(shape):
    """A box covering the lower-left half of the shape's bounding box (x and y up to the centre, all of z)."""
    lo, hi = K.bbox(shape)
    c = (lo + hi) / 2
    return BRepPrimAPI_MakeBox(gp_Pnt(*(lo - 1)), gp_Pnt(c[0], c[1], hi[2] + 1)).Shape()


def _outward(face, at_mid=True):
    ad = BRepAdaptor_Surface(face)
    u = (ad.FirstUParameter() + ad.LastUParameter()) / 2
    v = (ad.FirstVParameter() + ad.LastVParameter()) / 2
    n = BRepLProp_SLProps(ad, u, v, 1, 1e-7).Normal()
    n = np.array([n.X(), n.Y(), n.Z()])
    return -n if face.Orientation() == TopAbs_REVERSED else n


def _area(face):
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    return g.Mass()


def fillet_choice(shape):
    """(edge, radius) for a deterministic fillet, or None when the shape has no suitable edge.
    The edge is the longest straight convex edge between two planar faces; the radius is 0.1 x the
    narrower of those faces, where a face's width is its area divided by the length of the shared edge."""
    seams = K.seam_edge_indices(shape)
    owners = {}
    for f in K.faces(shape):
        if BRepAdaptor_Surface(f).GetType() != GeomAbs_Plane:
            continue
        for e in K.edges(f):
            owners.setdefault(hash(e), []).append((e, f))
    best = None
    for i, e in enumerate(K.edges(shape)):
        if i in seams:
            continue
        pair = [f for e2, f in owners.get(hash(e), []) if e2.IsSame(e)]
        if len(pair) != 2 or pair[0].IsSame(pair[1]):
            continue
        info = K.edge_info(e)
        if info["type"] != int(GeomAbs_Line) or info["len"] < 1e-6:
            continue
        n1, n2 = _outward(pair[0]), _outward(pair[1])
        if abs(float(np.dot(n1, n2))) > 1 - 1e-9:
            continue                                         # coplanar or folded flat
        width = min(_area(f) / info["len"] for f in pair)
        mid = np.asarray(info["c"], float)
        probe = n1 + n2
        probe /= np.linalg.norm(probe)
        cl = BRepClass3d_SolidClassifier(shape, gp_Pnt(*(mid + probe * width * 1e-3)), 1e-7)
        if cl.State() == TopAbs_IN:
            continue                                         # concave
        key = (round(info["len"], 6), -i)                    # longest first, lowest index on ties
        if best is None or key > best[0]:
            best = (key, e, 0.1 * width)
    return None if best is None else (best[1], best[2])
