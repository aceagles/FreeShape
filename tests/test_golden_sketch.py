"""Sketch solver baseline: solved coordinates, DOF and failing constraints for a set of constrained sketches.

This is the reference for swapping the solver (e.g. to FreeCAD's planegcs):
  * full_*      fully defined sketches. Any correct solver must land on the same coordinates; each also
                asserts the analytic answer.
  * under_*     under-defined sketches. The result is whatever the current damped Gauss-Newton minimum-norm
                step produces from the starting positions; a different solver may move points differently.
  * conflict_*  over-constrained sketches: which constraints are reported as failing.
"""
import math

import numpy as np
import pytest

from conftest import assert_golden
from freeshape import sketch_ops as OPS
from freeshape.sketch import ORIGIN, Sketch

TOL = 1e-7
CASES = {}


def case(fn):
    CASES[fn.__name__] = fn
    return fn


def snapshot(s: Sketch):
    st = s.status
    return {"ok": bool(st["ok"]), "dof": int(st["dof"]), "bad": sorted(st["bad"]), "free": sorted(st["free"]),
            "points": {k: [float(v[0]), float(v[1])] for k, v in sorted(s.points.items())},
            "radii": {k: float(e["r"]) for k, e in sorted(s.entities.items()) if e["type"] == "circle"}}


def at(s, pid, xy, tol=TOL):
    assert np.allclose(s.P(pid), xy, atol=tol), (pid, s.P(pid).tolist(), xy)


def rect(s, pts, h_v=True):
    """Four lines through the four given point ids; horizontal/vertical constraints when h_v."""
    L = [s.add_line(a, b) for a, b in zip(pts, pts[1:] + pts[:1])]
    if h_v:
        for line, t in zip(L, ["horizontal", "vertical", "horizontal", "vertical"]):
            s.add_constraint(t, line)
    return L


# =========================================================================== fully defined

@case
def full_rectangle():
    s = Sketch()
    p = [ORIGIN, s.add_point(55, 2), s.add_point(58, 37), s.add_point(1, 41)]
    L = rect(s, p)
    s.add_constraint("distance", L[0], value=60)
    s.add_constraint("distance", L[1], value=40)
    s.solve()
    assert s.status["dof"] == 0 and s.status["ok"]
    at(s, p[1], (60, 0)); at(s, p[2], (60, 40)); at(s, p[3], (0, 40))
    return s


@case
def full_rectangle_resized():
    s = full_rectangle()
    for c in s.constraints:
        if c.get("value") == 60:
            c["value"] = 85.5
    s.solve()
    assert s.status["dof"] == 0
    pts = sorted(map(tuple, np.round([s.P(k) for k in s.points], 9)))
    assert pts == [(0, 0), (0, 40), (85.5, 0), (85.5, 40)], pts
    return s


@case
def full_triangle_angle():
    s = Sketch()
    b, c = s.add_point(48, 1), s.add_point(30, 20)
    ab, ac = s.add_line(ORIGIN, b), s.add_line(ORIGIN, c)
    s.add_line(b, c)
    s.add_constraint("horizontal", ab)
    s.add_constraint("distance", ab, value=50)
    s.add_constraint("distance", ac, value=40)
    s.add_constraint("angle", ab, ac, value=30)
    s.solve()
    assert s.status["dof"] == 0 and s.status["ok"]
    at(s, b, (50, 0)); at(s, c, (40 * math.cos(math.radians(30)), 20))
    return s


@case
def full_circle_tangent_to_line():
    s = Sketch()
    b = s.add_point(58, 2)
    ln = s.add_line(ORIGIN, b)
    cc = s.add_point(18, 12)
    ci = s.add_circle(cc, 8)
    s.add_constraint("horizontal", ln)
    s.add_constraint("distance", ln, value=60)
    s.add_constraint("tangent", ln, ci)
    s.add_constraint("radius", ci, value=10)
    s.add_constraint("hdistance", ORIGIN, cc, value=20)
    s.solve()
    assert s.status["dof"] == 0 and s.status["ok"]
    at(s, b, (60, 0)); at(s, cc, (20, 10))
    assert abs(s.entities[ci]["r"] - 10) < TOL
    return s


@case
def full_circles_external_tangent():
    s = Sketch()
    c1 = s.add_circle(ORIGIN, 9)
    p2 = s.add_point(17, 1)
    c2 = s.add_circle(p2, 4)
    s.add_constraint("radius", c1, value=10)
    s.add_constraint("diameter", c2, value=10)
    s.add_constraint("horizontal", ORIGIN, p2)
    s.add_constraint("tangent", c1, c2)
    s.solve()
    assert s.status["dof"] == 0 and s.status["ok"]
    at(s, p2, (15, 0))
    return s


@case
def full_circles_internal_tangent():
    s = Sketch()
    c1 = s.add_circle(ORIGIN, 10)
    p2 = s.add_point(6, 0.5)
    c2 = s.add_circle(p2, 5)
    s.add_constraint("radius", c1, value=10)
    s.add_constraint("radius", c2, value=5)
    s.add_constraint("horizontal", ORIGIN, p2)
    s.add_constraint("tangent", c1, c2, value=-1)          # negative value: internal tangency
    s.solve()
    assert s.status["dof"] == 0 and s.status["ok"]
    at(s, p2, (5, 0))
    return s


@case
def full_symmetric_about_axis():
    s = Sketch()
    q1, q2 = s.add_point(1, -18), s.add_point(-1, 22)
    axis = s.add_line(q1, q2, True)
    s.add_constraint("vertical", axis)
    s.add_constraint("midpoint", ORIGIN, axis)
    s.add_constraint("distance", axis, value=40)
    a, b = s.add_point(12, 5), s.add_point(-10, 7)
    s.add_line(a, b)
    s.add_constraint("fix", a, value=[12, 5])
    s.add_constraint("symmetric", a, b, axis)
    s.solve()
    assert s.status["dof"] == 0 and s.status["ok"]
    at(s, b, (-12, 5)); at(s, q1, (0, -20)); at(s, q2, (0, 20))
    return s


@case
def full_square_parallel_perpendicular_equal():
    s = Sketch()
    b, c, d = s.add_point(29, 1), s.add_point(31, 28), s.add_point(-2, 31)
    l1, l2, l3 = s.add_line(ORIGIN, b), s.add_line(b, c), s.add_line(c, d)
    s.add_line(d, ORIGIN)
    s.add_constraint("horizontal", l1)
    s.add_constraint("distance", l1, value=30)
    s.add_constraint("perpendicular", l1, l2)
    s.add_constraint("equal", l1, l2)
    s.add_constraint("parallel", l1, l3)
    s.add_constraint("equal", l1, l3)
    s.solve()
    assert s.status["dof"] == 0 and s.status["ok"]
    at(s, b, (30, 0)); at(s, c, (30, 30)); at(s, d, (0, 30))
    return s


@case
def full_arc_and_concentric_circle():
    s = Sketch()
    p1, p2 = s.add_point(9, 1), s.add_point(1, 11)
    s.add_arc(ORIGIN, p1, p2)
    arc = next(k for k, e in s.entities.items() if e["type"] == "arc")
    cc = s.add_point(0.5, -0.5)
    ci = s.add_circle(cc, 3)
    s.add_constraint("horizontal", ORIGIN, p1)
    s.add_constraint("distance", ORIGIN, p1, value=10)
    s.add_constraint("vertical", ORIGIN, p2)
    s.add_constraint("concentric", ci, arc)
    s.add_constraint("diameter", ci, value=8)
    s.solve()
    assert s.status["dof"] == 0 and s.status["ok"]
    at(s, p1, (10, 0)); at(s, p2, (0, 10)); at(s, cc, (0, 0))
    assert abs(s.entities[ci]["r"] - 4) < TOL
    return s


@case
def full_point_on_circle():
    s = Sketch()
    ci = s.add_circle(ORIGIN, 9)
    p = s.add_point(7, 7)
    s.add_constraint("radius", ci, value=10)
    s.add_constraint("point_on", p, ci)
    s.add_constraint("vdistance", ORIGIN, p, value=6)
    s.solve()
    assert s.status["dof"] == 0 and s.status["ok"]
    at(s, p, (8, 6))
    return s


@case
def full_point_to_line_distance():
    s = Sketch()
    q = s.add_point(1, 48)
    ln = s.add_line(ORIGIN, q)
    r = s.add_point(10, 4)
    s.add_point_entity(r)
    s.add_constraint("vertical", ln)
    s.add_constraint("distance", ln, value=50)
    s.add_constraint("distance", r, ln, value=12)
    s.add_constraint("vdistance", ORIGIN, r, value=3)
    s.solve()
    assert s.status["dof"] == 0 and s.status["ok"]
    at(s, q, (0, 50)); at(s, r, (12, 3))
    return s


@case
def under_coincident_chain_end_free():
    s = Sketch()
    a, b, c = s.add_point(1, 1), s.add_point(20, 2), s.add_point(21, 3)
    s.add_line(a, b)
    s.add_line(c, s.add_point(20, 30))
    s.add_constraint("fix", a, value=[0, 0])
    s.add_constraint("coincident", b, c)
    s.add_constraint("hdistance", a, b, value=25)
    s.add_constraint("vdistance", a, b, value=5)
    s.solve()
    assert s.status["ok"]
    at(s, b, (25, 5)); at(s, c, (25, 5))
    return s


@case
def full_sketch_fillet_corner():
    s = Sketch()
    p = [ORIGIN, s.add_point(40, 0), s.add_point(40, 30), s.add_point(0, 30)]
    L = rect(s, p)
    s.add_constraint("distance", L[0], value=40)
    s.add_constraint("distance", L[1], value=30)
    s.solve()
    OPS.fillet_corner(s, p[2], 5)
    assert s.status["ok"] and s.status["dof"] == 0
    arc = next(e for e in s.entities.values() if e["type"] == "arc")
    at(s, arc["c"], (35, 25))
    return s


# =========================================================================== under-defined

@case
def under_rectangle_no_dimensions():
    s = Sketch()
    p = [ORIGIN, s.add_point(50, 1), s.add_point(52, 30), s.add_point(1, 31)]
    rect(s, p)
    s.solve()
    assert s.status["ok"] and s.status["dof"] == 2
    return s


@case
def under_floating_rectangle_resized():
    """No anchor to the origin: changing the width moves both sides (minimum-norm), not just one."""
    s = Sketch()
    p = [s.add_point(10, 10), s.add_point(50, 10), s.add_point(50, 30), s.add_point(10, 30)]
    L = rect(s, p)
    w = s.add_constraint("distance", L[0], value=40)
    s.add_constraint("distance", L[1], value=20)
    s.solve()
    assert s.status["dof"] == 2
    w["value"] = 60
    s.solve()
    assert s.status["ok"]
    assert abs(s.P(p[1])[0] - s.P(p[0])[0] - 60) < TOL
    return s


@case
def under_dragged_corner():
    s = Sketch()
    p = [ORIGIN, s.add_point(50, 0), s.add_point(50, 30), s.add_point(0, 30)]
    rect(s, p)
    s.solve()
    s.points[p[2]] = [70.0, 45.0]
    s.solve(weights={(p[2], 0): 1e3, (p[2], 1): 1e3})
    assert s.status["ok"]
    return s


@case
def under_unconstrained_triangle_nudged():
    s = Sketch()
    a, b, c = s.add_point(0, 0), s.add_point(30, 0), s.add_point(10, 20)
    l1, l2, _ = s.add_line(a, b), s.add_line(b, c), s.add_line(c, a)
    s.add_constraint("equal", l1, l2)
    s.solve()
    assert s.status["ok"]
    return s


@case
def under_circle_free_radius():
    s = Sketch()
    cc = s.add_point(3, 4)
    s.add_circle(cc, 7)
    s.add_constraint("coincident", cc, ORIGIN)
    s.solve()
    assert s.status["ok"] and s.status["dof"] == 1
    return s


# =========================================================================== conflicts

@case
def conflict_overdimensioned_rectangle():
    s = Sketch()
    p = [ORIGIN, s.add_point(60, 0), s.add_point(60, 40), s.add_point(0, 40)]
    L = rect(s, p)
    s.add_constraint("distance", L[0], value=60)
    s.add_constraint("distance", L[1], value=40)
    s.add_constraint("distance", ORIGIN, p[2], value=10)       # the diagonal can't be 10
    s.solve()
    assert not s.status["ok"] and s.status["bad"]
    return s


@case
def conflict_redundant_but_consistent():
    s = full_rectangle()
    L = [k for k, e in s.entities.items() if e["type"] == "line"]
    s.add_constraint("equal", L[0], L[2])
    s.solve()
    assert s.status["ok"] and s.status["dof"] == 0
    return s


@pytest.mark.parametrize("name", sorted(CASES))
def test_sketch_solver_golden(name):
    s = CASES[name]()
    assert_golden(f"sketch/{name}", snapshot(s), rel_tol=1e-9, abs_tol=TOL)


@pytest.mark.parametrize("name", sorted(CASES))
def test_sketch_dict_roundtrip_resolves_identically(name):
    s = CASES[name]()
    t = Sketch.from_dict(s.to_dict())
    t.solve()
    a, b = snapshot(s), snapshot(t)
    assert a["dof"] == b["dof"] and a["bad"] == b["bad"]
    for k in a["points"]:
        assert np.allclose(a["points"][k], b["points"][k], atol=TOL), k
