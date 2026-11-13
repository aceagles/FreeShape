"""Sketch Extend tool: the sketch_ops.extend builder and the sketcher's Extend tool."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import sketch_ops as OPS  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from test_sketch_inference import VP, _Ev, _sketcher  # noqa: E402


class _VP(type(VP)):
    status_text = ""

    def update(self):
        pass


def _hline(sk, x1, x2, y=0.0):
    a, b = sk.add_point(x1, y), sk.add_point(x2, y)
    return sk.add_line(a, b), a, b


def _vline(sk, x, y1, y2):
    a, b = sk.add_point(x, y1), sk.add_point(x, y2)
    return sk.add_line(a, b), a, b


def _cons(sk):
    return [(c["type"], tuple(c["refs"])) for c in sk.constraints]


def test_extends_to_vertical_line():
    sk = Sketch()
    h, a, b = _hline(sk, 0, 5)
    v, _, _ = _vline(sk, 10, -5, 5)
    res = OPS.extend(sk, h, "p2")
    assert res["target"] == v and res["point"] == b
    assert abs(sk.P(b)[0] - 10) < 1e-6 and abs(sk.P(b)[1]) < 1e-6
    assert ("point_on", (b, v)) in _cons(sk)
    assert np.allclose(sk.P(a), [0, 0])
    assert sk.status["ok"]


def test_extends_near_end_backwards():
    sk = Sketch()
    h, a, b = _hline(sk, 0, 5)
    _vline(sk, -4, -5, 5)
    OPS.extend(sk, h, "p1")
    assert abs(sk.P(a)[0] + 4) < 1e-6


def test_nearest_circle_crossing_and_arc_span():
    sk = Sketch()
    h, a, b = _hline(sk, 0, 2)
    c = sk.add_circle(sk.add_point(10, 0), 3)
    OPS.extend(sk, h, "p2")
    assert abs(sk.P(b)[0] - 7) < 1e-6                 # nearer of x=7 and x=13
    assert ("point_on", (b, c)) in _cons(sk)
    # an arc covering only the upper half: the line at y=0 meets its ends only; use a line at y=1
    sk = Sketch()
    h, a, b = _hline(sk, 0, 2, 1.0)
    cen = sk.add_point(10, 0)
    lower = sk.add_arc(cen, sk.add_point(13, 0), sk.add_point(7, 0))   # ccw 0..180 = upper half
    OPS.extend(sk, h, "p2")
    assert sk.entities[lower]["type"] == "arc"
    assert abs(sk.P(b)[1] - 1) < 1e-6 and sk.P(b)[0] > 7      # first crossing on the upper half is x=10-sqrt(8)
    sk = Sketch()
    h, a, b = _hline(sk, 0, 2, -1.0)           # lower half is not on the arc
    cen = sk.add_point(10, 0)
    sk.add_arc(cen, sk.add_point(13, 0), sk.add_point(7, 0))
    assert OPS.extend(sk, h, "p2") is None


def test_dimension_and_horizontal_survive():
    sk = Sketch()
    h, a, b = _hline(sk, 0, 5, 2.0)
    _vline(sk, 10, -5, 5)
    sk.add_constraint("horizontal", h)
    k = sk.add_constraint("vdistance", "origin", b, value=2.0)
    assert sk.solve()
    OPS.extend(sk, h, "p2")
    assert ("horizontal", (h,)) in _cons(sk) and k in sk.constraints
    assert abs(sk.P(b)[0] - 10) < 1e-6 and abs(sk.P(b)[1] - 2) < 1e-6


def test_length_dimension_refuses_and_leaves_sketch():
    sk = Sketch()
    h, a, b = _hline(sk, 0, 5)
    _vline(sk, 10, -5, 5)
    sk.add_constraint("distance", a, b, value=5.0)
    sk.add_constraint("fix", a)
    sk.solve()
    before = sk.to_dict()
    with pytest.raises(ValueError):
        OPS.extend(sk, h, "p2")
    assert sk.to_dict() == before


def test_shared_end_refuses():
    sk = Sketch()
    h, a, b = _hline(sk, 0, 5)
    sk.add_line(b, sk.add_point(5, 3))
    _vline(sk, 10, -5, 5)
    before = sk.to_dict()
    with pytest.raises(ValueError, match="shared"):
        OPS.extend(sk, h, "p2")
    assert sk.to_dict() == before


def test_no_target_returns_none_and_ignores_behind_and_coincident():
    sk = Sketch()
    h, a, b = _hline(sk, 0, 5)
    _vline(sk, -3, -5, 5)          # behind the p2 end
    _vline(sk, 5, 1, 5)            # parallel to nothing, but does not reach y=0
    sk.add_line(sk.add_point(5, -5), sk.add_point(5, -1))
    assert OPS.extend(sk, h, "p2") is None
    assert not any(c["type"] == "point_on" for c in sk.constraints)
    # a curve already touching the end is not a target
    sk2 = Sketch()
    h, a, b = _hline(sk2, 0, 5)
    _vline(sk2, 5, -5, 5)
    assert OPS.extend(sk2, h, "p2") is None


def test_tool_click_extends_with_one_undo_step():
    s = _sketcher()
    sk = s.sk
    h, a, b = _hline(sk, 0, 5)
    v, _, _ = _vline(sk, 10, -5, 5)
    s.set_tool("extend")
    n = len(s.undo_stack)
    s.mouse_move(VP, 445, 300, _Ev())
    s.mouse_press(VP, 445, 300, _Ev())            # near the p2 end (x=4.5 mm)
    assert abs(sk.P(b)[0] - 10) < 1e-6 and len(s.undo_stack) == n + 1
    s.undo()
    assert abs(s.sk.P(b)[0] - 5) < 1e-6


def test_tool_click_no_target_pushes_no_undo():
    s = _sketcher()
    h, a, b = _hline(s.sk, 0, 5)
    s.set_tool("extend")
    n = len(s.undo_stack)
    vp = _VP()
    s.mouse_move(vp, 445, 300, _Ev())
    s.mouse_press(vp, 445, 300, _Ev())
    assert len(s.undo_stack) == n and abs(s.sk.P(b)[0] - 5) < 1e-6
    assert "nothing to extend" in vp.status_text.lower()
