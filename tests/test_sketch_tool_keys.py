"""Sketch tool keys: R / G for the rectangles (as in Onshape), and switching tool with a key straight after drawing,
while the dimension box that pops up is still untouched."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLineEdit  # noqa: E402

from conftest import TOP  # noqa: E402


class _Settings(dict):
    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


class _Ev:
    def modifiers(self):
        return Qt.NoModifier


def _sketching():
    """A window in a new sketch on the Top plane. Returns (window, sketcher, viewport)."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    w.settings = _Settings()
    app.processEvents()
    w.start_sketch()
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    return w, w.sketcher, w.viewport


def _click(s, vp, u, v):
    x, y = s.scr(vp, (u, v))
    s.mouse_move(vp, x, y, _Ev())
    s.mouse_press(vp, x, y, _Ev())


# tool -> the clicks (sketch mm, clear of the origin so nothing snaps) that draw one shape with it
DRAW = {
    "line": [(10, 10), (30, 22)],
    "rect": [(10, 10), (30, 22)],
    "center_rect": [(20, 15), (30, 22)],
    "circle": [(20, 15), (28, 15)],
    "arc_center": [(20, 15), (30, 15), (20, 25)],     # the 3-point arc offers no box
}


def _draw(s, vp, tool):
    """Draw one shape with `tool`; returns the dimension box that is offered for it."""
    s.set_tool(tool)
    for u, v in DRAW[tool]:
        _click(s, vp, u, v)
    assert isinstance(s._editor, QLineEdit), f"drawing a {tool} should offer a dimension box"
    return s._editor


def _dims(s):
    return [c for c in s.sk.constraints if c["type"] in ("distance", "diameter", "radius")]


def test_r_is_the_centre_rectangle_and_g_the_corner_rectangle():
    w, s, vp = _sketching()
    QTest.keyClick(vp, Qt.Key_R)
    assert s.tool == "center_rect"
    QTest.keyClick(vp, Qt.Key_G)
    assert s.tool == "rect"


def test_the_rectangle_keys_are_shown_where_the_tools_are_listed():
    w, s, vp = _sketching()
    assert "Corner rectangle (G)" in w._sketch_buttons["rect"].toolTip()
    assert "Center rectangle (R)" in w._sketch_buttons["center_rect"].toolTip()


@pytest.mark.parametrize("tool, key, want", [
    ("line", Qt.Key_C, "circle"),
    ("rect", Qt.Key_L, "line"),
    ("center_rect", Qt.Key_L, "line"),
    ("circle", Qt.Key_L, "line"),
    ("arc_center", Qt.Key_G, "rect"),
])
def test_a_tool_key_in_the_untouched_dimension_box_switches_tool(tool, key, want):
    w, s, vp = _sketching()
    ed = _draw(s, vp, tool)
    QTest.keyClick(ed, key)
    assert s.tool == want
    assert s._editor is None, "the box closes"
    assert _dims(s) == [], "and no dimension is added"


def test_q_in_the_untouched_dimension_box_is_the_construction_key():
    w, s, vp = _sketching()
    ed = _draw(s, vp, "circle")
    QTest.keyClick(ed, Qt.Key_Q)
    assert s._editor is None and s.tool == "circle" and _dims(s) == []


def test_a_letter_after_a_typed_value_stays_in_the_box():
    w, s, vp = _sketching()
    ed = _draw(s, vp, "circle")
    QTest.keyClicks(ed, "1 in")                    # "i" is the coincident key, "1 in" is one inch
    assert s._editor is ed and ed.text() == "1 in" and s.tool == "circle"
    QTest.keyClick(ed, Qt.Key_Return)
    assert [round(c["value"], 3) for c in _dims(s)] == [25.4]


def test_a_letter_stays_in_the_box_once_the_offered_value_was_edited():
    w, s, vp = _sketching()
    ed = _draw(s, vp, "circle")
    QTest.keyClick(ed, Qt.Key_Backspace)           # cleared the offered value: the user is typing now
    QTest.keyClick(ed, Qt.Key_L)
    assert s._editor is ed and ed.text() == "l" and s.tool == "circle"


def test_enter_in_the_dimension_box_still_adds_the_dimension():
    w, s, vp = _sketching()
    ed = _draw(s, vp, "line")
    QTest.keyClicks(ed, "25")
    QTest.keyClick(ed, Qt.Key_Return)
    assert [(c["type"], c["value"]) for c in _dims(s)] == [("distance", 25.0)]
    assert s._editor is None and s.tool == "line"


def test_a_tool_key_in_the_height_box_of_a_rectangle_switches_tool():
    w, s, vp = _sketching()
    width = _draw(s, vp, "rect")
    QTest.keyClicks(width, "40")
    QTest.keyClick(width, Qt.Key_Return)
    height = s._editor
    assert isinstance(height, QLineEdit) and height is not width, "the height is offered after the width"
    QTest.keyClick(height, Qt.Key_C)
    assert s.tool == "circle" and s._editor is None
    assert [c["value"] for c in _dims(s)] == [40.0], "only the width was dimensioned"


def test_a_letter_after_a_typed_height_stays_in_the_box():
    w, s, vp = _sketching()
    width = _draw(s, vp, "rect")
    QTest.keyClick(width, Qt.Key_Return)
    height = s._editor
    QTest.keyClicks(height, "10 cm")
    assert s._editor is height and height.text() == "10 cm" and s.tool == "rect"
    QTest.keyClick(height, Qt.Key_Return)
    assert sorted(round(c["value"], 3) for c in _dims(s)) == [20.0, 100.0]
