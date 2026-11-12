"""The operation history the kernel helpers hand back with `with_history=True`.

Element names are built from that history (docs/reference/element-naming.md § 3), so what matters is
not what a raw OCCT maker reports but what survives the whole kernel path: a boolean goes through
`clean` (ShapeUpgrade_UnifySameDomain, twice with pinned faces in the awkward cases) and sometimes
ShapeFix, and an extrude of several profiles is fused together. These tests therefore reach OCCT
only through the kernel helpers and query the composed history the helpers return.

"No orphans" below means every face and edge of the result is accounted for by the history: it is
either a sub-shape of an input (a boolean leaves such elements out of its history altogether) or a
generated or modified image of one. One known gap is OCCT's, not ours, and FreeCAD has it too: a
full (360°) revolution reports no generated shape for the profile edges that sweep its end faces,
so those two faces reach nothing. FreeCAD names elements like that from their neighbours instead
(§ 4 of the spec); the revolve case here is a partial revolution, which OCCT reports in full.
"""
import os
import sys

import numpy as np
import pytest
from OCP.BRep import BRep_Builder
from OCP.BRepTools import BRepTools, BRepTools_History
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopoDS import TopoDS_Shape

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import golden_diffs, shape_fingerprint  # noqa: E402
from freeshape import kernel as K  # noqa: E402

Z = np.array([0.0, 0.0, 1.0])
BREP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "freecad", "brep")


# --------------------------------------------------------------------------- shapes, built through the kernel

def _rect(u0, v0, u1, v1):
    pts = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    return [{"type": "line", "p1": pts[i], "p2": pts[(i + 1) % 4]} for i in range(4)]


def _profile(*rects, plane=None, regions=1):
    prof = K.sketch_regions(plane or K.std_plane("Top"), [c for r in rects for c in r])
    assert len(prof) == regions
    return prof


def _box(x0=0.0, y0=0.0, x1=20.0, y1=10.0, h=6.0, z0=0.0):
    return K.extrude(_profile(_rect(x0, y0, x1, y1), plane=K.std_plane("Top").offset(z0)), Z, h)


def _pin(z=-2.0, r=2.0, h=10.0, x=5.0, y=5.0):
    return K.cylinder(np.array([x, y, z]), Z, r, h)


# --------------------------------------------------------------------------- the six helpers

def _extrude():
    prof = _profile(_rect(0, 0, 20, 10))
    return prof, lambda **kw: K.extrude(prof, Z, 6.0, **kw)


def _two_profile_extrude():
    prof = _profile(_rect(0, 0, 10, 10), _rect(5, 5, 15, 15), regions=3)
    return prof, lambda **kw: K.extrude(prof, Z, 3.0, **kw)


def _revolve():
    prof = _profile(_rect(2, 0, 5, 8), plane=K.std_plane("Front"))
    return prof, lambda **kw: K.revolve(prof, (0, 0, 0), (0, 0, 1), 270.0, **kw)


def _fuse():
    a, b = _box(0, 0, 10, 10, 4.0), _box(10, 0, 20, 10, 4.0)
    return [a, b], lambda **kw: K.fuse(a, b, **kw)


def _cut():
    box, pin = _box(), _pin()
    return [box, pin], lambda **kw: K.cut(box, pin, **kw)


def _common():
    box, pin = _box(), _pin()
    return [box, pin], lambda **kw: K.common(box, pin, **kw)


def _fuse_all():
    parts = [_box(0, 0, 10, 10, 4.0), _box(8, 0, 18, 10, 4.0), _box(16, 0, 26, 10, 4.0)]
    return parts, lambda **kw: K.fuse_all(parts, **kw)


HELPERS = {"extrude": _extrude, "revolve": _revolve, "fuse": _fuse, "cut": _cut,
           "common": _common, "fuse_all": _fuse_all}
CASES = dict(HELPERS, two_profile_extrude=_two_profile_extrude)


# --------------------------------------------------------------------------- reading a history

def _images(hist, inputs):
    """Everything the history accounts for: the input sub-shapes and all their images."""
    out = []
    for src in inputs:
        for kind in (TopAbs_SOLID, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX):
            for s in K._sub_shapes(src, kind):
                out.append(s)
                out += list(hist.Generated(s)) + list(hist.Modified(s))
    return out


def _orphans(hist, inputs, result):
    known = _images(hist, inputs)
    return [s for kind in (TopAbs_FACE, TopAbs_EDGE)
            for s in K._sub_shapes(result, kind) if not any(s.IsSame(k) for k in known)]


def _generated_faces(hist, shape):
    return [K._as_face(s) for s in hist.Generated(shape) if s.ShapeType() == TopAbs_FACE]


def _modified_faces(hist, shape):
    return [K._as_face(s) for s in hist.Modified(shape) if s.ShapeType() == TopAbs_FACE]


def _pick_faces(shape, pred):
    return [f for f in K.faces(shape) if pred(K.face_info(f))]


def _planar_at(axis, value, tol=1e-7):
    return lambda i: i["type"] == GeomAbs_Plane and abs(i["c"][axis] - value) < tol


def _same(a, b):
    return any(a.IsSame(x) for x in b)


# --------------------------------------------------------------------------- type and geometry

@pytest.mark.parametrize("name", sorted(HELPERS))
def test_history_is_an_occt_history(name):
    _, call = HELPERS[name]()
    shape, hist = call(with_history=True)
    assert shape is not None
    assert isinstance(hist, BRepTools_History)


@pytest.mark.parametrize("name", sorted(HELPERS))
def test_geometry_is_the_same_with_and_without_history(name):
    _, call = HELPERS[name]()
    with_hist, _ = call(with_history=True)
    assert golden_diffs(shape_fingerprint(call()), shape_fingerprint(with_hist), 1e-12, 1e-12) == []


@pytest.mark.parametrize("name", sorted(CASES))
def test_no_orphans(name):
    inputs, call = CASES[name]()
    shape, hist = call(with_history=True)
    assert _orphans(hist, inputs, shape) == []


# --------------------------------------------------------------------------- what the history says

def test_extrude_generates_one_side_face_per_profile_edge():
    prof, call = _extrude()
    shape, hist = call(with_history=True)
    sides = _pick_faces(shape, lambda i: i["type"] == GeomAbs_Plane and abs(i["n"][2]) < 1e-7)
    assert len(K.edges(prof[0])) == 4 and len(sides) == 4
    for e in K.edges(prof[0]):
        made = _generated_faces(hist, e)
        assert len(made) == 1
        assert _same(made[0], sides)


def test_cut_reports_the_holed_face_the_hole_wall_and_the_removed_ends():
    (box, pin), call = _cut()
    shape, hist = call(with_history=True)
    top = _pick_faces(box, _planar_at(2, 6.0))
    assert len(top) == 1
    holed = _modified_faces(hist, top[0])
    assert len(holed) == 1 and K.face_loops(holed[0]) == 2        # outer wire plus the hole
    assert _same(holed[0], K.faces(shape))

    side = _pick_faces(pin, lambda i: i["type"] == GeomAbs_Cylinder)
    assert len(side) == 1
    wall = _modified_faces(hist, side[0])
    assert len(wall) == 1 and K.face_info(wall[0])["type"] == GeomAbs_Cylinder
    assert _same(wall[0], K.faces(shape))
    assert K.face_info(wall[0])["area"] == pytest.approx(2 * np.pi * 2.0 * 6.0, rel=1e-9)

    outside = _pick_faces(pin, _planar_at(2, 8.0))                # the pin's end, clear of the box
    assert len(outside) == 1
    assert hist.IsRemoved(outside[0])
    assert not _same(outside[0], K.faces(shape))


def test_two_profile_extrude_reaches_a_side_face_of_the_first_profile():
    prof, call = _two_profile_extrude()
    shape, hist = call(with_history=True)
    assert len(K.solids(shape)) == 1                              # so fuse_all really ran
    reached = [f for e in K.edges(prof[0]) for f in _generated_faces(hist, e)
               if _same(f, K.faces(shape))]
    assert reached
    assert all(K.face_info(f)["type"] == GeomAbs_Plane and abs(K.face_info(f)["n"][2]) < 1e-7
               for f in reached)


def test_refine_maps_both_coplanar_faces_to_the_one_merged_face():
    (a, b), call = _fuse()
    shape, hist = call(with_history=True)
    unrefined = K.fuse(a, b, check=True)                          # same boolean, no refine step
    assert len(K.faces(shape)) < len(K.faces(unrefined))
    tops = [f for src in (a, b) for f in _pick_faces(src, _planar_at(2, 4.0))]
    assert len(tops) == 2
    images = [_modified_faces(hist, f) for f in tops]
    assert [len(i) for i in images] == [1, 1]
    assert images[0][0].IsSame(images[1][0])
    assert _same(images[0][0], K.faces(shape))


def test_careful_booleans_also_compose_a_history():
    """check=True skips refine and may run ShapeFix instead; the history still covers the result."""
    box, pin = _box(), _pin()
    shape, hist = K.cut(box, pin, check=True, volume_check=True, with_history=True)
    assert isinstance(hist, BRepTools_History)
    assert _orphans(hist, [box, pin], shape) == []


def test_the_healer_hands_back_its_reshape_history():
    """The ShapeFix step of the careful path (nothing to fix here, so the step is the identity)."""
    box = _box()
    shape, hist = K._repair(box, with_history=True)
    assert isinstance(hist, BRepTools_History)
    assert _orphans(hist, [box], shape) == []


# --------------------------------------------------------------------------- clean()'s pinned / fallback path

def _load(name):
    shape = TopoDS_Shape()
    assert BRepTools.Read_s(shape, os.path.join(BREP_DIR, name), BRep_Builder()), name
    assert K.is_valid(shape)
    return shape


def _half_box(shape, axis, frac):
    """The tool tests/test_cut_refine.py cuts these shapes with, built through the kernel."""
    lo, hi = K.bbox(shape)
    mx = list(hi + 1)
    mx[axis] = lo[axis] + frac * (hi[axis] - lo[axis])
    plane = K.std_plane("Top").offset(lo[2] - 1)
    return K.extrude(_profile(_rect(lo[0] - 1, lo[1] - 1, mx[0], mx[1]), plane=plane),
                     Z, mx[2] - lo[2] + 1)


@pytest.mark.parametrize("name,axis,frac", [("cylinder7_V2.brep", 0, 0.5),
                                            ("cylinderSeam3.brep", 1, 0.25)])
def test_history_has_no_orphans_where_refine_pins_or_falls_back(name, axis, frac):
    shape = _load(name)
    tool = _half_box(shape, axis, frac)
    unrefined = K.cut(shape, tool, check=True)                    # same boolean, no refine step
    assert not K.is_valid(K._unify(unrefined).Shape())            # so clean() pins or falls back
    res, hist = K.cut(shape, tool, with_history=True)
    assert K.is_valid(res) and len(K.solids(res)) == 1
    assert _orphans(hist, [shape, tool], res) == []
