"""Sketch offset on both sides, with round caps on an open chain (card I1.6)."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import sketch_ops as OPS  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from test_tools import areas, rect  # noqa: E402


def _offsets(s):
    return [c for c in s.constraints if c["type"] == "offset"]


def _areas(s):
    """Region areas, unrounded."""
    return sorted(K.face_info(f)["area"] for f in K.sketch_regions(K.std_plane("Top"), s.curves()))


def _line(s, pts):
    ids = [s.add_point(*p) for p in pts]
    return [s.add_line(a, b) for a, b in zip(ids, ids[1:])]


def _dist_to_line(s, new, src):
    e, f = s.entities[new], s.entities[src]
    a, b = s.P(f["p1"]), s.P(f["p2"])
    d = b - a
    d = d / np.linalg.norm(d)
    w = s.P(e["p1"]) - a
    return abs(d[0] * w[1] - d[1] * w[0])


def test_open_line_with_round_caps_is_one_stadium():
    s = Sketch()
    (ln,) = _line(s, [(0, 0), (100, 0)])
    OPS.offset(s, ln, 5, both=True, caps="round")
    assert s.status["ok"], s.status
    a = _areas(s)
    assert len(a) == 1 and abs(a[0] - (100 * 10 + math.pi * 25)) < 1e-4, a


def test_l_shaped_chain_matches_occ_offset_plus_end_half_discs():
    s = Sketch()
    ls = _line(s, [(0, 0), (50, 0), (50, 50)])
    OPS.offset(s, ls[0], 3, both=True, caps="round")
    assert s.status["ok"], s.status
    a = _areas(s)
    assert len(a) == 1, a
    occ, discs = _occ_open_offset_area([(0, 0), (50, 0), (50, 50)], 3)
    # OCC's closed loop already has the round ends: its area is the sharp-cornered strip plus the two half-discs
    assert abs(a[0] - occ) < 1e-4, (a, occ)
    assert abs((a[0] - discs) - 2 * 3 * 100) < 1e-4            # sharp strip = 2 * d * chain length


def _occ_open_offset_area(pts, d):
    """OCC reference, test only. BRepOffsetAPI_MakeOffset with GeomAbs_Intersection (sharp corners) on the open
    chain closes the loop round the ends itself. Returns (face area of that loop, area of the two end half-discs)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from OCP.GeomAbs import GeomAbs_Intersection
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Pnt
    mw = BRepBuilderAPI_MakeWire()
    for p, q in zip(pts, pts[1:]):
        mw.Add(BRepBuilderAPI_MakeEdge(gp_Pnt(*p, 0), gp_Pnt(*q, 0)).Edge())
    mo = BRepOffsetAPI_MakeOffset()
    mo.Init(GeomAbs_Intersection, False)
    mo.AddWire(mw.Wire())
    mo.Perform(d)
    from OCP.gp import gp_Pln
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    ex = TopExp_Explorer(mo.Shape(), TopAbs_WIRE)
    wire = TopoDS.Wire_s(ex.Current()) if hasattr(TopoDS, "Wire_s") else TopoDS.Wire(ex.Current())
    face = BRepBuilderAPI_MakeFace(gp_Pln(), wire).Face()
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    return g.Mass(), math.pi * d * d


def test_arc_chain_gives_inner_and_outer_radius_and_rejects_too_big():
    s = Sketch()
    c = s.add_point(0, 0)
    arc = s.add_arc(c, s.add_point(20, 0), s.add_point(0, 20))
    new = OPS.offset(s, arc, 4, both=True)
    assert s.status["ok"], s.status
    assert sorted(round(s.radius(k), 6) for k in new) == [16.0, 24.0]
    s2 = Sketch()
    c = s2.add_point(0, 0)
    arc = s2.add_arc(c, s2.add_point(20, 0), s2.add_point(0, 20))
    with pytest.raises(ValueError, match="too large"):
        OPS.offset(s2, arc, 20, both=True)
    assert len(s2.entities) == 1       # nothing half-built
    with pytest.raises(ValueError):
        OPS.offset(s2, arc, 25, both=True, caps="round")


def test_circle_both_gives_two_circles():
    s = Sketch()
    ci = s.add_circle(s.add_point(0, 0), 10)
    new = OPS.offset(s, ci, 3, both=True)
    assert sorted(round(s.radius(k), 6) for k in new) == [7.0, 13.0]
    with pytest.raises(ValueError):
        OPS.offset(s, ci, 10, both=True)


def test_closed_rectangle_both_gives_two_loops_and_one_dimension():
    s = Sketch()
    _P, L = rect(s)
    before = set(s.entities)
    new = OPS.offset(s, L[0], 5, both=True, caps="round")
    assert len(new) == 8 and not (set(new) & before)
    assert all(s.entities[k]["type"] == "line" for k in new)
    (c,) = _offsets(s)
    assert len(c["refs"]) == 16 and len(c["signs"]) == 8
    assert s.status["ok"], s.status
    assert areas(s) == [600.0, 600.0, 800.0], areas(s)     # inner disc, inner ring, outer ring
    # the source stays a real profile: no construction, no caps
    assert not any(s.entities[k].get("construction") for k in s.entities)


def test_editing_the_dimension_moves_every_pair_and_caps_stay_tangent():
    s = Sketch()
    ls = _line(s, [(0, 0), (100, 0), (100, 60)])
    new = OPS.offset(s, ls[0], 5, both=True, caps="round")
    (c,) = _offsets(s)
    c["value"] = 8
    assert s.solve(), s.status
    pairs = [(c["refs"][i], c["refs"][i + 1]) for i in range(0, len(c["refs"]), 2)]
    assert len(pairs) == 4
    for n, src in pairs:
        assert abs(_dist_to_line(s, n, src) - 8) < 1e-6
    caps = [k for k in new if s.entities[k]["type"] == "arc"]
    assert len(caps) == 2
    for k in caps:
        assert abs(s.radius(k) - 8) < 1e-3                  # the arc radius is the solver's, not a dimension
    tang = [t for t in s.constraints if t["type"] == "tangent"]
    assert len(tang) == 4
    assert len(_areas(s)) == 1, _areas(s)


def test_moving_a_source_end_point_keeps_the_profile_closed():
    s = Sketch()
    (ln,) = _line(s, [(0, 0), (100, 0)])
    OPS.offset(s, ln, 5, both=True, caps="round")
    p2 = s.entities[ln]["p2"]
    x, y = s.P(p2)
    s.points[p2] = [x + 10.0, y + 30.0]
    assert s.solve(), s.status
    assert len(areas(s)) == 1, areas(s)


def test_concave_zigzag_too_large_raises_and_adds_nothing():
    s = Sketch()
    ls = _line(s, [(0, 0), (20, 0), (20, 4), (0, 4)])
    n_ent, n_pts = len(s.entities), len(s.points)
    with pytest.raises(ValueError, match="too large"):
        OPS.offset(s, ls[0], 3, both=True)
    assert len(s.entities) == n_ent and len(s.points) == n_pts
    OPS.offset(s, ls[0], 1.5, both=True)       # below half the gap it still works
    assert s.status["ok"], s.status


def test_default_arguments_are_unchanged():
    s1, s2 = Sketch(), Sketch()
    out = []
    for s in (s1, s2):
        _P, L = rect(s)
        out.append(OPS.offset(s, L[1], 5) if s is s1 else OPS.offset(s, L[1], 5, both=False, caps=None))
    assert out[0] == out[1]
    assert [dict(c) for c in s1.constraints] == [dict(c) for c in s2.constraints]
