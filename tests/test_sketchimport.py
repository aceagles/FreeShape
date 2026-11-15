"""freeshape/sketchimport.py: weld, de-duplicate, close and place imported curves in a sketch."""
import math
import os
import sys

import numpy as np
import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from freeshape import kernel as K  # noqa: E402
from freeshape import sketch as S  # noqa: E402
from freeshape import sketchimport as SI  # noqa: E402

PLANE = K.std_plane("Top")
KAPPA = 0.5523


def line(a, b, layer="0"):
    return {"type": "line", "p1": a, "p2": b, "layer": layer}


def circle(c, r):
    return {"type": "circle", "c": c, "r": r, "layer": "0"}


def bez(*pts):
    return {"type": "bezier", "pts": list(pts), "layer": "0"}


def square(x0, y0, x1, y1):
    q = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [line(q[i], q[(i + 1) % 4]) for i in range(4)]


def area(face):
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    return g.Mass()


def regions(sk):
    return K.sketch_regions(PLANE, sk.curves())


def run(prims, **kw):
    sk = S.Sketch()
    ids, rep = SI.insert(sk, prims, **kw)
    return sk, ids, rep


def bezier_circle(r, cx=0.0, cy=0.0):
    k = KAPPA * r
    pts = [(cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)]
    ctl = [((cx + r, cy + k), (cx + k, cy + r)), ((cx - k, cy + r), (cx - r, cy + k)),
           ((cx - r, cy - k), (cx - k, cy - r)), ((cx + k, cy - r), (cx + r, cy - k))]
    return [bez(pts[i], ctl[i][0], ctl[i][1], pts[(i + 1) % 4]) for i in range(4)]


def test_square_with_round_hole_two_regions():
    sk, ids, rep = run(square(-20, -20, 20, 20) + [circle((0, 0), 10)])
    areas = sorted(area(f) for f in regions(sk))
    assert len(areas) == 2
    assert areas[0] == pytest.approx(100 * math.pi, abs=1e-6)
    assert areas[1] == pytest.approx(1600 - 100 * math.pi, abs=1e-6)
    assert rep["open_ends"] == []


def test_bezier_circle_tolerance():
    results = {}
    for tol in (0.01, 0.001):
        sk, ids, rep = run(bezier_circle(20), tol=tol)
        rs = regions(sk)
        assert len(rs) == 1
        assert abs(area(rs[0]) - 400 * math.pi) / (400 * math.pi) < 0.0005
        results[tol] = (len(sk.points), rep["spline_max_error"])
        assert rep["spline_max_error"] <= tol
    assert results[0.001][0] > results[0.01][0]
    assert results[0.001][1] < results[0.01][1]


def near_closed(gap):
    pts = [(0, 0), (10, 0), (10, 10), (0, 10), (0, gap)]
    return [line(pts[i], pts[i + 1]) for i in range(4)]


def test_near_closed_chain_welds_or_reports_open_ends():
    sk, _, rep = run(near_closed(5e-4), weld=0.001)
    assert len(regions(sk)) == 1
    assert rep["open_ends"] == []
    sk, _, rep = run(near_closed(5e-4), weld=1e-5)
    assert len(regions(sk)) == 0
    assert len(rep["open_ends"]) == 2
    assert all(q in sk.points for q in rep["open_ends"])


def test_duplicate_square_dropped():
    sk, ids, rep = run(square(0, 0, 40, 40) + square(0, 0, 40, 40))
    assert len([e for e in sk.entities.values() if e["type"] == "line"]) == 4
    assert rep["dropped_duplicate"] == 4
    assert len(regions(sk)) == 1


def test_arc_off_radius_closes_and_equal_radii():
    # a half-disc: arc from (10, 0) to (-10, 0) about the origin, stored end 1e-4 off radius
    prims = [{"type": "arc", "c": (0, 0), "p1": (10, 0), "p2": (-10.0001, 1e-5), "layer": "0"},
             line((-10.0001, 1e-5), (10, 0))]
    sk, ids, rep = run(prims)
    arc = next(e for e in sk.entities.values() if e["type"] == "arc")
    c, p1, p2 = (np.array(sk.points[arc[k]]) for k in ("c", "p1", "p2"))
    assert abs(np.hypot(*(p1 - c)) - np.hypot(*(p2 - c))) < 1e-9
    rs = regions(sk)
    assert len(rs) == 1
    assert area(rs[0]) == pytest.approx(50 * math.pi, rel=1e-3)
    assert rep["open_ends"] == []


def test_bezier_chain_with_corner_makes_two_splines_sharing_the_corner():
    # an S-bend up to (10, 10), a 90 degree corner, then a bend on to (20, 0)
    prims = [bez((0, 0), (5, 0), (10, 5), (10, 10)), bez((10, 10), (15, 10), (20, 5), (20, 0))]
    # tangent in at (10, 10) is +y, out is +x: a corner
    sk, ids, rep = run(prims, use_file_origin=True)
    sp = [e for e in sk.entities.values() if e["type"] == "spline"]
    assert len(sp) == 2
    assert sp[0]["pts"][-1] == sp[1]["pts"][0]
    assert sk.points[sp[0]["pts"][-1]] == pytest.approx([10, 10])


def test_smooth_bezier_chain_is_one_spline():
    prims = [bez((0, 0), (5, 0), (10, 0), (15, 0.5)), bez((15, 0.5), (20, 1), (25, 2), (30, 3))]
    sk, ids, rep = run(prims)
    assert [e["type"] for e in sk.entities.values()] == ["spline"]


def test_flat_bezier_becomes_line():
    sk, ids, rep = run([bez((0, 0), (3, 0), (7, 0), (10, 0))])
    assert [e["type"] for e in sk.entities.values()] == ["line"]


def bbox(sk):
    xy = np.array([v for k, v in sk.points.items() if k != S.ORIGIN])
    return xy.min(0), xy.max(0)


def test_scale_and_placement():
    prims = square(100, 200, 110, 230)
    sk1, _, _ = run(prims, use_file_origin=True)
    lo, hi = bbox(sk1)
    assert lo == pytest.approx([100, 200]) and hi == pytest.approx([110, 230])
    sk2, _, _ = run(prims, use_file_origin=True, scale=2)
    lo, hi = bbox(sk2)
    assert (hi - lo) == pytest.approx([20, 60])
    sk3, _, rep = run(prims, scale=2)
    lo, hi = bbox(sk3)
    assert (hi - lo) == pytest.approx([20, 60])
    assert (lo + hi) / 2 == pytest.approx([0, 0])
    sk4, _, _ = run(prims, origin=(5, 7))
    lo, hi = bbox(sk4)
    assert (lo + hi) / 2 == pytest.approx([5, 7])
    sk5, _, _ = run(prims, rotation=90)
    lo, hi = bbox(sk5)
    assert (hi - lo) == pytest.approx([30, 10])


def test_construction_layers():
    sk, ids, _ = run([line((0, 0), (10, 0), "guide"), line((10, 0), (10, 5), "0")], construction=("guide",))
    assert [sk.entities[i]["construction"] for i in ids] == [True, False]


def test_adds_no_constraints_and_solves():
    sk, ids, rep = run(square(-20, -20, 20, 20) + [circle((0, 0), 10)] + bezier_circle(5)
                       + [{"type": "arc", "c": (0, 0), "p1": (30, 0), "p2": (0, 30), "layer": "0"}])
    assert sk.constraints == []
    assert sk.solve() is True


def test_extrude_outer_region():
    sk, _, _ = run(square(-20, -20, 20, 20) + [circle((0, 0), 10)])
    outer = max(regions(sk), key=area)
    solid = K.extrude([outer], np.array([0.0, 0.0, 1.0]), 5.0)
    assert K.volume(solid) == pytest.approx(5 * (1600 - 100 * math.pi), rel=1e-6)
