"""Sketch relations (#29, #30): a tangent picked circle-first solves, a pair holds one relation of each kind, a
relation the solver can't meet is refused with a message, and dragging a centre (or its "M" badge) moves the shape.
Each test builds its own sketch, so it passes run alone."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from test_sketch_inference import VP, _Ev, _sketcher  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _square_and_circle():
    """Adam's layout: a 15.5 x 10 centre rectangle (H/V sides, construction diagonal with its midpoint as the
    centre) and a Ø10 circle on that centre, well inside the rectangle's right side."""
    sk = Sketch()
    tl, tr = sk.add_point(-11.4, -10.3), sk.add_point(4.1, -10.3)
    br, bl = sk.add_point(4.1, -20.3), sk.add_point(-11.4, -20.3)
    top, right = sk.add_line(tl, tr), sk.add_line(tr, br)
    bottom, left = sk.add_line(br, bl), sk.add_line(bl, tl)
    for ln, t in ((top, "horizontal"), (bottom, "horizontal"), (right, "vertical"), (left, "vertical")):
        sk.add_constraint(t, ln)
    diag = sk.add_line(tl, br, construction=True)
    ctr = sk.add_point(-3.65, -15.3)
    mid = sk.add_constraint("midpoint", ctr, diag)
    circ = sk.add_circle(ctr, 5.0)
    sk.add_constraint("diameter", circ, value=10.0)
    assert sk.solve()
    return sk, {"top": top, "bottom": bottom, "right": right, "left": left, "circle": circ, "centre": ctr, "mid": mid["id"],
                "tr": tr, "br": br}


def _right_x(sk, ids):
    return sk.P(sk.entities[ids["right"]]["p1"])[0]


@pytest.mark.parametrize("circle_first", [True, False])
def test_tangent_to_the_right_line_moves_the_shapes_in_either_pick_order(circle_first):
    sk, ids = _square_and_circle()
    s = _sketcher(sk)
    picks = [ids["circle"], ids["right"]] if circle_first else [ids["right"], ids["circle"]]
    assert s._apply_constraint("tangent", picks) is True
    sk = s.sk
    assert sk.status["ok"] and not sk.status["skipped"]
    gap = _right_x(sk, ids) - sk.P(ids["centre"])[0]
    assert gap == pytest.approx(5.0, abs=1e-6)          # the right side now touches the circle


def test_solver_builds_a_circle_first_line_tangent():
    sk, ids = _square_and_circle()
    c = sk.add_constraint("tangent", ids["circle"], ids["right"])
    assert any(cid == c["id"] for cid, _f, _k in sk._equations())
    assert sk.solve()
    assert _right_x(sk, ids) - sk.P(ids["centre"])[0] == pytest.approx(5.0, abs=1e-6)


def test_adding_the_same_tangent_again_is_a_no_op_with_a_message():
    sk, ids = _square_and_circle()
    s = _sketcher(sk)
    assert s._apply_constraint("tangent", [ids["circle"], ids["right"]])
    n, undo = len(s.sk.constraints), len(s.undo_stack)
    assert s._apply_constraint("tangent", [ids["right"], ids["circle"]]) is True   # reversed order: same pair
    assert len(s.sk.constraints) == n and len(s.undo_stack) == undo
    assert s.hint() == "⚠ Already tangent"


@pytest.mark.parametrize("tool,which", [("parallel", ("top", "bottom")), ("perpendicular", ("top", "right")),
                                        ("equal", ("top", "left")), ("coincident", ("centre", "top"))])
def test_every_kind_holds_one_relation_per_pair(tool, which):
    sk, ids = _square_and_circle()
    s = _sketcher(sk)
    a, b = ids[which[0]], ids[which[1]]
    s._apply_constraint(tool, [a, b])
    n = len(s.sk.constraints)
    s._apply_constraint(tool, [b, a])
    s._apply_constraint(tool, [a, b])
    assert len(s.sk.constraints) == n
    assert s.hint().startswith("⚠ Already")


def test_a_tangent_the_solver_cannot_meet_is_refused_and_said():
    sk, ids = _square_and_circle()
    sk.add_constraint("distance", ids["top"], value=15.5)     # rectangle width fixed: the right side can't come in
    sk.add_constraint("fix", sk.entities[ids["left"]]["p1"], value=list(sk.P(sk.entities[ids["left"]]["p1"])))
    assert sk.solve()
    s = _sketcher(sk)
    before = s.sk.to_dict()
    assert s._apply_constraint("tangent", [ids["circle"], ids["right"]]) is True
    assert not any(c["type"] == "tangent" for c in s.sk.constraints)
    assert s.sk.points == before["points"]
    assert s.hint().startswith("⚠ Can't add tangent")


def _drag(s, x, y, dx, dy):
    s.mouse_press(VP, x, y, _Ev())
    for k in range(1, 5):
        s.mouse_drag(VP, x + dx * k / 4, y + dy * k / 4, _Ev())
    s.mouse_release(VP, x + dx, y + dy, _Ev(), True)


def test_dragging_the_centre_point_moves_the_rectangle_and_circle():
    sk, ids = _square_and_circle()
    s = _sketcher(sk)
    c0, tr0 = s.sk.P(ids["centre"]).copy(), s.sk.P(ids["tr"]).copy()
    x, y = s.scr(VP, c0)
    _drag(s, x, y, 30, -20)                                   # 10 px per mm in the stub: (+3, +2) mm
    assert np.allclose(s.sk.P(ids["centre"]) - c0, [3, 2], atol=1e-3)
    assert np.allclose(s.sk.P(ids["tr"]) - tr0, [3, 2], atol=1e-3)    # the rectangle came with it
    assert s.sk.status["ok"]


def test_dragging_the_midpoint_badge_moves_its_point():
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    sk, ids = _square_and_circle()
    s = _sketcher(sk)
    img = QImage(800, 600, QImage.Format_ARGB32)
    p = QPainter(img)
    s.draw_overlay(VP, p)                                     # lays out the badges' hit boxes
    p.end()
    badge = next(r for r, cid in s._glyph_rects if cid == ids["mid"])
    x, y = badge.center().x(), badge.center().y()
    assert s.hit(VP, x, y) == ("constraint", ids["mid"])      # the "M" itself, not the point
    c0 = s.sk.P(ids["centre"]).copy()
    _drag(s, x, y, 30, -20)
    assert np.allclose(s.sk.P(ids["centre"]) - c0, [3, 2], atol=1e-3)
