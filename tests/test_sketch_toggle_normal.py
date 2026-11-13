"""Sketcher: radius <-> diameter toggle on a dimension, and the Normal constraint (line through a curve's centre)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui.numparse import parse_number  # noqa: E402
from test_sketch_inference import _sketcher  # noqa: E402


def _circle_sketch():
    sk = Sketch()
    c = sk.add_point(3, 4)
    circ = sk.add_circle(c, 5)
    dim = sk.add_constraint("radius", circ, value=5)
    sk.solve()
    return sk, c, circ, dim["id"]


def test_toggle_to_diameter_and_back_keeps_circle():
    sk, c, circ, cid = _circle_sketch()
    s = _sketcher(sk)
    before = list(sk.points[c]), sk.radius(circ)
    assert s.toggle_radius_diameter(cid)
    k = s._constraint(cid)
    assert k["type"] == "diameter" and k["value"] == 10.0 and k["refs"] == [circ]
    assert (list(sk.points[c]), sk.radius(circ)) == before
    assert s.toggle_radius_diameter(cid)
    k = s._constraint(cid)
    assert k["type"] == "radius" and k["value"] == 5.0
    assert np.allclose(sk.points[c], before[0]) and abs(sk.radius(circ) - 5) < 1e-9


def test_expression_is_wrapped_and_evaluates_double():
    sk, _c, _circ, cid = _circle_sketch()
    s = _sketcher(sk)
    s._constraint(cid)["expr"] = "#r"
    s.toggle_radius_diameter(cid)
    expr = s._constraint(cid)["expr"]
    assert expr == "(#r)*2"
    assert parse_number(expr, variables={"r": 7.0}) == 14.0
    s.toggle_radius_diameter(cid)
    assert s._constraint(cid)["expr"] == "((#r)*2)/2"
    assert parse_number(s._constraint(cid)["expr"], variables={"r": 7.0}) == 7.0


def test_undo_restores_radius_dimension():
    sk, _c, _circ, cid = _circle_sketch()
    s = _sketcher(sk)
    s.toggle_radius_diameter(cid)
    s.undo()
    k = s._constraint(cid)
    assert k["type"] == "radius" and k["value"] == 5.0


def _normal_sketch():
    sk = Sketch()
    a, b = sk.add_point(-20, 0), sk.add_point(20, 5)
    line = sk.add_line(a, b)
    for p in (a, b):
        sk.add_constraint("fix", p, value=list(sk.P(p)))
    c = sk.add_point(3, 12)
    circ = sk.add_circle(c, 4)
    sk.add_constraint("radius", circ, value=4)
    sk.solve()
    return sk, a, b, c, line, circ


def _dist(sk, a, b, c):
    A, B, C = sk.P(a), sk.P(b), sk.P(c)
    d = B - A
    return abs(d[0] * (C - A)[1] - d[1] * (C - A)[0]) / np.linalg.norm(d)


def test_normal_constraint_passes_through_centre_and_drops_dof():
    sk, a, b, c, line, circ = _normal_sketch()
    dof0 = sk.status["dof"]
    s = _sketcher(sk)
    assert s._apply_constraint("normal", [circ, line]) is True
    k = sk.constraints[-1]
    assert k["type"] == "point_on" and k["normal"] is True and k["refs"] == [c, line]
    assert sk.status["dof"] == dof0 - 1
    assert _dist(sk, a, b, c) < 1e-6
    # drag the circle off the line: the solve brings it back
    sk.points[c] = [10.0, 30.0]
    sk.solve()
    assert _dist(sk, a, b, c) < 1e-6
    # round trip and delete
    sk2 = Sketch.from_dict(sk.to_dict())
    assert sk2.constraints[-1].get("normal") is True
    sk2.delete([k["id"]])
    assert not any(x.get("normal") for x in sk2.constraints)


def test_normal_rejects_two_lines():
    sk, _a, _b, _c, line, _circ = _normal_sketch()
    x, y = sk.add_point(0, 20), sk.add_point(9, 20)
    l2 = sk.add_line(x, y)
    s = _sketcher(sk)
    assert s._apply_constraint("normal", [line, l2]) is False
