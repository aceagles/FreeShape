"""Sketch offset: the copy is held by one `offset` dimension, so it follows its source and the gap can be edited."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import sketch_ops as OPS  # noqa: E402
from freeshape.sketch import DIMENSIONS, Sketch  # noqa: E402
from test_tools import areas, rect  # noqa: E402


def _offsets(s):
    return [c for c in s.constraints if c["type"] == "offset"]


def _set(s, ctype, old, new):
    for c in s.constraints:
        if c["type"] == ctype and c.get("value") == old:
            c["value"] = new
    assert s.solve(), s.status


def test_an_offset_rectangle_has_one_dimension_and_is_fully_defined():
    s = Sketch()
    _P, L = rect(s)
    new = OPS.offset(s, L[1], 5)
    (c,) = _offsets(s)
    assert "offset" in DIMENSIONS and c["value"] == 5 and len(c["refs"]) == 8
    assert c["refs"][1] == L[1] and c["refs"][0] in new          # the clicked side leads: it carries the dimension
    assert s.status["ok"] and s.status["dof"] == 0
    assert abs(sum(areas(s)) - 50 * 40) < 0.01


def test_the_copy_follows_its_source_and_the_gap_can_be_edited():
    s = Sketch()
    _P, L = rect(s)
    OPS.offset(s, L[0], 5)
    _set(s, "distance", 40, 60)                                  # the source grows: the copy stays 5 outside it
    assert abs(sum(areas(s)) - 70 * 40) < 0.01, areas(s)
    _set(s, "offset", 5, 8)                                      # the dimension drives the gap
    assert abs(sum(areas(s)) - 76 * 46) < 0.01, areas(s)


def test_an_inward_offset_keeps_a_positive_value_and_stays_inside():
    s = Sketch()
    _P, L = rect(s)
    OPS.offset(s, L[0], -5)
    (c,) = _offsets(s)
    assert c["value"] == 5 and sorted(areas(s)) == [600.0, 600.0]            # the inner rectangle and the ring
    _set(s, "offset", 5, 3)
    assert areas(s) == [1200 - 34 * 24, 34 * 24], areas(s)      # ring, inner rectangle
    c["value"] = -3                                              # a negative value (e.g. from an expression) is the same gap
    assert s.solve() and areas(s) == [1200 - 34 * 24, 34 * 24]


def test_an_offset_circle_follows_the_radius_of_its_source():
    s = Sketch()
    ci = s.add_circle(s.add_point(0, 0), 10)
    s.add_constraint("radius", ci, value=10)
    (new,) = OPS.offset(s, ci, 3)
    assert abs(s.radius(new) - 13) < 1e-6
    _set(s, "radius", 10, 20)
    assert abs(s.radius(new) - 23) < 1e-6
    (inner,) = OPS.offset(s, ci, -4)
    _set(s, "radius", 20, 30)
    assert abs(s.radius(inner) - 26) < 1e-6 and abs(s.radius(new) - 33) < 1e-6


def test_a_chain_with_arcs_is_held_on_every_segment():
    s = Sketch()
    P, L = rect(s)
    OPS.fillet_corner(s, P[2], 6)
    new = OPS.offset(s, L[0], 4)
    (c,) = _offsets(s)
    assert s.status["ok"] and len(c["refs"]) == 2 * len(new) == 10
    _set(s, "distance", 40, 55)
    _set(s, "offset", 4, 7)
    for n_, src in zip(c["refs"][0::2], c["refs"][1::2]):
        e = s.entities[src]
        if e["type"] == "line":
            a, b = s.P(e["p1"]), s.P(e["p2"])
            d, w = b - a, s.P(s.entities[n_]["p1"]) - a
            assert abs(abs(d[0] * w[1] - d[1] * w[0]) / np.linalg.norm(d) - 7) < 1e-6
        else:
            assert abs(s.radius(n_) - s.radius(src) - 7) < 1e-6
    assert abs(sum(areas(s)) - (69 * 44 - (4 - math.pi) * 13 ** 2 / 4)) < 0.05, areas(s)


def test_deleting_the_copy_removes_the_dimension():
    s = Sketch()
    _P, L = rect(s)
    new = OPS.offset(s, L[0], 5)
    s.delete([new[0]])
    assert not _offsets(s) and s.solve()


def test_the_dimension_survives_a_save_and_draws():
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from test_sketch_inference import VP, _sketcher
    QApplication.instance() or QApplication([])
    s = Sketch()
    _P, L = rect(s)
    OPS.offset(s, L[0], 5)
    ci = s.add_circle(s.add_point(80, 0), 10)
    OPS.offset(s, ci, 3)
    s = Sketch.from_dict(s.to_dict())
    assert [c["signs"] for c in _offsets(s)] and s.solve()
    sk = _sketcher(s)
    img = QImage(800, 600, QImage.Format_ARGB32)
    p = QPainter(img)
    try:
        for c in _offsets(s):
            sk._draw_dimension(VP, p, c, p.fontMetrics())
    finally:
        p.end()
    assert {c["id"] for c in _offsets(s)} <= set(sk._dim_rects)
