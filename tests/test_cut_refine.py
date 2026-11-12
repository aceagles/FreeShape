"""The refine step after booleans (kernel.clean, ShapeUpgrade_UnifySameDomain) must not break a valid solid.

FreeCAD's ModelRefineTests shapes cylinder7_V2 and cylinderSeam3 (tests/data/freecad/brep, LGPL-2.1+) stack full
cylinders and bore a side hole whose boundary touches the cylinders' seam at one vertex, so one face's hole
wire shares a vertex object with its outer wire. BRepCheck accepts that, but UnifySameDomain rebuilds the
wires of every face it merges or re-edges from vertex connectivity and fuses the two loops into one
figure-of-eight wire: the face comes back BRepCheck_UnorientableShape (FreeCAD's 2014 log: "refine works, but
corrupts the model"). Same on OCCT 7.9.3 and 8.0.1; no UnifySameDomain option avoids it.
"""
import os
import sys

import pytest
from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_VERTEX
from OCP.TopoDS import TopoDS_Shape
from OCP.gp import gp_Pnt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import approx_rel  # noqa: E402
from freeshape import kernel as K  # noqa: E402

BREP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "freecad", "brep")


def _load(name):
    shape = TopoDS_Shape()
    assert BRepTools.Read_s(shape, os.path.join(BREP_DIR, name), BRep_Builder()), name
    assert K.is_valid(shape)
    return shape


def _half_box(shape, axis, frac):
    """A box from bbox-min minus 1 to `frac` of the way along `axis`, through the full extent of the other two."""
    lo, hi = K.bbox(shape)
    mx = list(hi + 1)
    mx[axis] = lo[axis] + frac * (hi[axis] - lo[axis])
    return BRepPrimAPI_MakeBox(gp_Pnt(*(lo - 1)), gp_Pnt(*mx)).Shape()


def _raw_cut(shape, tool):
    raw = BRepAlgoAPI_Cut(shape, tool).Shape()
    assert K.is_valid(raw) and len(K.solids(raw)) == 1
    return raw


# (file, cut axis, fraction): cases whose plain UnifySameDomain result is an invalid solid
BREAKING = [("cylinder7_V2.brep", 0, 0.5), ("cylinder7_V2.brep", 1, 0.25),
            ("cylinderSeam3.brep", 1, 0.25), ("cylinderSeam3.brep", 1, 0.4)]
# cases where unify is sound and must go on merging faces
MERGING = [("cylinderSeam3.brep", 0, 0.5, 8, 5), ("cylinder7_V2.brep", 2, 0.5, 4, 3)]


@pytest.mark.parametrize("name,axis,frac", BREAKING)
def test_plain_unify_breaks_these_shapes(name, axis, frac):
    """Documents the OCCT behaviour this module guards against; if this starts failing the guard is moot."""
    raw = _raw_cut(_load(name), _half_box(_load(name), axis, frac))
    plain = K._unify(raw).Shape()
    assert not K.is_valid(plain)
    assert len(K._invalid_faces(plain)) == 1


@pytest.mark.parametrize("name,axis,frac", BREAKING)
def test_default_cut_returns_valid_solid(name, axis, frac):
    shape = _load(name)
    tool = _half_box(shape, axis, frac)
    raw = _raw_cut(shape, tool)
    res = K.cut(shape, tool)                     # default check=False: the refine path
    assert K.is_valid(res)
    assert len(K.solids(res)) == 1
    approx_rel(K.volume(res), K.volume(raw), rel=1e-6)
    assert len(K.faces(res)) <= len(K.faces(raw))


def test_cylinder7_keeps_the_sound_merges():
    """Only the face that broke is left as the boolean made it; the stack on the other side still merges."""
    shape = _load("cylinder7_V2.brep")
    tool = _half_box(shape, 0, 0.5)
    raw = _raw_cut(shape, tool)
    res = K.cut(shape, tool)
    assert len(K.faces(raw)) == 10 and len(K.faces(res)) == 8
    u = K._unify(raw)
    bad = K._invalid_faces(u.Shape())
    sources = [f for f in K.faces(raw) if any(any(m.IsSame(b) for b in bad) for m in u.History().Modified(f))]
    assert len(sources) == 3
    cleaned = K.clean(raw)                      # same boolean result as `raw`, so faces can be compared by identity
    assert len(K.faces(cleaned)) == 8
    assert all(any(f.IsSame(g) for g in K.faces(cleaned)) for f in sources)


@pytest.mark.parametrize("name,axis,frac,raw_faces,merged_faces", MERGING)
def test_cut_still_merges_faces(name, axis, frac, raw_faces, merged_faces):
    shape = _load(name)
    tool = _half_box(shape, axis, frac)
    raw = _raw_cut(shape, tool)
    res = K.cut(shape, tool)
    assert K.is_valid(res)
    assert len(K.faces(raw)) == raw_faces
    assert len(K.faces(res)) == merged_faces < raw_faces
    approx_rel(K.volume(res), K.volume(raw), rel=1e-6)


def test_clean_on_broken_case_is_valid_and_partial():
    shape = _load("cylinder7_V2.brep")
    raw = _raw_cut(shape, _half_box(shape, 0, 0.5))
    res = K.clean(raw)
    assert K.is_valid(res)
    assert len(K.faces(K._unify(raw).Shape())) < len(K.faces(res)) < len(K.faces(raw))


# --- the unify settings clean() relies on, pinned on two fused unit boxes (10 faces / 20 edges)

def _fused_boxes():
    a = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), gp_Pnt(1, 1, 1)).Shape()
    b = BRepPrimAPI_MakeBox(gp_Pnt(1, 0, 0), gp_Pnt(2, 1, 1)).Shape()
    f = BRepAlgoAPI_Fuse(a, b).Shape()
    assert (len(K.faces(f)), len(K.edges(f))) == (10, 20)
    return f


def _vertices(shape):
    return [K._as_vertex(v) for v in K._explore(shape, TopAbs_VERTEX)]


def test_unify_merges_faces_and_edges():
    r = K._unify(_fused_boxes()).Shape()
    assert (len(K.faces(r)), len(K.edges(r))) == (6, 12)


def test_pinned_edge_forbids_face_merge():
    f = _fused_boxes()
    r = K._unify(f, K.edges(f)).Shape()
    assert (len(K.faces(r)), len(K.edges(r))) == (10, 20)


def test_pinned_vertex_forbids_edge_merge_only():
    f = _fused_boxes()
    r = K._unify(f, _vertices(f)).Shape()
    assert (len(K.faces(r)), len(K.edges(r))) == (6, 16)


def test_clean_merges_fused_boxes():
    assert len(K.faces(K.clean(_fused_boxes()))) == 6


def test_clean_returns_input_when_nothing_merges():
    box = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), gp_Pnt(1, 1, 1)).Shape()
    assert K.clean(box).IsSame(box)
