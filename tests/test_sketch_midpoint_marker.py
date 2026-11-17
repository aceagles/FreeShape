"""Sketching: hovering a line shows a square at its midpoint (like Onshape), filled once the cursor snaps to it;
clicking there adds a midpoint constraint."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui import theme  # noqa: E402
from test_sketch_inference import VP, _Ev, _sketcher  # noqa: E402


def _line_sketch():
    sk = Sketch()
    a = sk.add_point(-10, 5)
    b = sk.add_point(10, 5)                      # midpoint (0, 5): screen (400, 250)
    return sk, sk.add_line(a, b)


def _paint(s):
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    img = QImage(800, 600, QImage.Format_ARGB32)
    img.fill(0xFFFFFFFF)
    p = QPainter(img)
    try:
        s.draw_overlay(VP, p)
    finally:
        p.end()
    return img


def _is_hover(c):
    want = [round(v * 255) for v in theme.HOVER]
    return all(abs(a - b) <= 40 for a, b in zip((c.red(), c.green(), c.blue()), want))


def _left_side(img):
    """True when the square's left side (x = 395.5 on screen) is painted in the hover colour."""
    return any(_is_hover(img.pixelColor(x, 250)) for x in range(394, 398))


def test_hovering_a_line_shows_a_hollow_square_at_its_midpoint():
    sk, ln = _line_sketch()
    s = _sketcher(sk)
    s.set_tool("point")
    s.mouse_move(VP, 450, 250, _Ev())            # on the line, 5 mm right of the midpoint
    assert s.snap_info == ("on", ln)
    assert s.mid_mark == (ln, False)
    img = _paint(s)
    assert _left_side(img)
    assert not _is_hover(img.pixelColor(400 - 2, 250 - 2))  # hollow inside


def test_on_the_midpoint_the_square_fills_and_the_click_adds_a_midpoint_constraint():
    sk, ln = _line_sketch()
    s = _sketcher(sk)
    s.set_tool("point")
    s.mouse_move(VP, 403, 252, _Ev())            # near the midpoint
    assert s.snap_info == ("mid", ln)
    assert s.mid_mark == (ln, True)
    assert _is_hover(_paint(s).pixelColor(400 - 2, 250 - 2))   # filled
    s.mouse_press(VP, 403, 252, _Ev())
    mids = [c for c in s.sk.constraints if c["type"] == "midpoint"]
    assert len(mids) == 1 and mids[0]["refs"][1] == ln
    assert s.sk.points[mids[0]["refs"][0]] == [0.0, 5.0]


def test_no_square_off_the_line_or_on_a_circle():
    sk, _ln = _line_sketch()
    c = sk.add_point(0, -10)
    sk.add_circle(c, 3)
    s = _sketcher(sk)
    s.set_tool("line")
    s.mouse_move(VP, 600, 500, _Ev())            # empty space
    assert s.mid_mark is None
    s.mouse_move(VP, 430, 400, _Ev())            # on the circle (3, -10)
    assert s.snap_info and s.snap_info[0] == "on"
    assert s.mid_mark is None
    img = _paint(s)
    assert not _left_side(img)


def test_changing_tool_clears_the_square():
    sk, ln = _line_sketch()
    s = _sketcher(sk)
    s.set_tool("line")
    s.mouse_move(VP, 450, 250, _Ev())
    assert s.mid_mark == (ln, False)
    s.set_tool("select")
    assert s.mid_mark is None
    s.mouse_move(VP, 450, 250, _Ev())            # hovering in select: no snap, no square
    assert s.mid_mark is None
