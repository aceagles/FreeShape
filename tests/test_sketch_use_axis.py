"""The Use and Dimension tools take a construction axis the way they take a part edge."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui.sketcher import Sketcher  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402


class _Cam:
    scale = 1.0

    def to_screen(self, w):                      # 10 px per mm, origin at (400, 300), y up
        return (w[0] * 10 + 400, 300 - w[1] * 10, 0)


class _VP:
    cam = _Cam()
    part_shapes = {}
    hover = None
    status_text = ""

    def __init__(self, axes):
        self.axes = axes                          # [(("axis", fid), origin, direction, span, label)]
        self.under_cursor = None                  # the axis fid a click lands on

    def ray_plane(self, x, y, plane):
        return ((x - 400) / 10, (300 - y) / 10, 0)

    def pick(self, x, y, kinds=None):
        if self.under_cursor is not None and kinds == {"axis"}:
            return Pick("axis", (self.under_cursor,))
        return None

    def axis_ends(self, origin, direction, span):
        o, d = np.asarray(origin, float), np.asarray(direction, float)
        return o + span[0] * d, o + span[1] * d


class _Ev:
    def modifiers(self):
        return Qt.NoModifier


def _axis(o, d, span=(-10.0, 10.0)):
    d = np.asarray(d, float)
    return (("axis", "a1"), np.asarray(o, float), d / np.linalg.norm(d), span, "")


def _setup(axis):
    s = Sketcher(K.std_plane("Top"), Sketch(), "f1", lambda *a: None)
    s._offer_dims = lambda *a, **k: None
    s.edit_dimension = lambda *a, **k: None
    vp = _VP([axis])
    vp.under_cursor = "a1"
    return s, vp


def _use(s, vp, x=400, y=300):
    s.set_tool("use")
    s.mouse_move(vp, x, y, _Ev())
    s.mouse_press(vp, x, y, _Ev())


def _fixed_pts(s):
    return {c["refs"][0] for c in s.sk.constraints if c["type"] == "fix"}


def test_an_axis_in_the_plane_becomes_one_fixed_construction_line():
    s, vp = _setup(_axis([0, 0, 0], [1, 0, 0]))
    _use(s, vp)
    (eid,) = [e for e, v in s.sk.entities.items() if v["type"] == "line"]
    e = s.sk.entities[eid]
    assert e.get("construction")
    assert {e["p1"], e["p2"]} <= _fixed_pts(s)
    assert sorted([s.sk.P(e["p1"])[0], s.sk.P(e["p2"])[0]]) == [-10, 10]
    assert abs(s.sk.P(e["p1"])[1]) < 1e-9 and abs(s.sk.P(e["p2"])[1]) < 1e-9


def test_an_axis_square_to_the_plane_becomes_a_fixed_point():
    s, vp = _setup(_axis([3, 4, -7], [0, 0, 1]))
    _use(s, vp)
    assert not [e for e in s.sk.entities.values() if e["type"] == "line"]
    pts = [p for p in s.sk.points if p != "origin"]
    assert len(pts) == 1 and s.sk.points[pts[0]] == [3.0, 4.0] and pts[0] in _fixed_pts(s)


def test_a_skew_axis_becomes_a_line_along_its_projection():
    s, vp = _setup(_axis([0, 0, 5], [1, 1, 1], span=(0.0, 10.0)))
    _use(s, vp)
    (eid,) = [e for e, v in s.sk.entities.items() if v["type"] == "line"]
    e = s.sk.entities[eid]
    a, b = np.array(s.sk.P(e["p1"])), np.array(s.sk.P(e["p2"]))
    k = 10 / np.sqrt(3)
    assert np.allclose(sorted([tuple(a), tuple(b)]), [(0, 0), (k, k)], atol=1e-9)
    assert {e["p1"], e["p2"]} <= _fixed_pts(s) and e.get("construction")


def test_using_the_same_axis_twice_adds_nothing():
    for axis in (_axis([0, 0, 0], [1, 0, 0]), _axis([3, 4, -7], [0, 0, 1])):
        s, vp = _setup(axis)
        _use(s, vp)
        before = (dict(s.sk.points), dict(s.sk.entities), len(s.sk.constraints))
        _use(s, vp)
        assert (dict(s.sk.points), dict(s.sk.entities), len(s.sk.constraints)) == before


def test_hovering_an_axis_with_the_use_tool_highlights_it():
    s, vp = _setup(_axis([0, 0, 0], [1, 0, 0]))
    s.set_tool("use")
    s.mouse_move(vp, 400, 300, _Ev())
    assert vp.hover == Pick("axis", ("a1",))


def test_dimension_from_a_point_to_an_axis_adds_a_distance():
    s, vp = _setup(_axis([0, 0, 0], [1, 0, 0]))
    p = s.sk.add_point(5, 8)
    s.set_tool("dimension")
    s.mouse_move(vp, 450, 300, _Ev())
    s.mouse_press(vp, 450, 300, _Ev())            # the axis (no sketch geometry there yet): projected, picked
    vp.under_cursor = None
    s.mouse_press(vp, 450, 220, _Ev())            # the sketch point
    s.mouse_press(vp, 470, 250, _Ev())            # place it
    (line,) = [e for e, v in s.sk.entities.items() if v["type"] == "line"]
    (c,) = [c for c in s.sk.constraints if c["type"] == "distance"]
    assert set(c["refs"]) == {p, line} and abs(c["value"] - 8) < 1e-6
