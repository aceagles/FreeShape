"""Esc reaches idle in one press from each common state, as in Onshape, and each sketch tool has its own cursor.

Esc is pressed as the user presses it: a key click on whichever widget has the focus, so the window's Esc
shortcut and any widget that would eat the key are both in play. `_escape` is counted so a press that does
nothing shows up as a wasted press."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from conftest import TOP, block_doc  # noqa: E402


class _Ev:
    def modifiers(self):
        return Qt.NoModifier


def _window(monkeypatch, tmp_path, doc=None):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    if doc is not None:
        w._add_tab(doc)
    w.resize(1200, 800)
    w.show()
    w.activateWindow()
    app.processEvents()
    return w


@pytest.fixture
def sk(monkeypatch, tmp_path):
    """A window in a new sketch on the Top plane: (window, sketcher, viewport)."""
    w = _window(monkeypatch, tmp_path)
    w.start_sketch()
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    yield w, w.sketcher, w.viewport
    w.deleteLater()


@pytest.fixture
def part(monkeypatch, tmp_path):
    doc = block_doc(40, 20, 10)
    doc.rebuild()
    w = _window(monkeypatch, tmp_path, doc)
    yield w
    if w.dialog is not None:
        w.cancel_dialog()
    w.deleteLater()


def esc(w):
    """Press Esc on the focused widget, as the keyboard would."""
    from PySide6.QtWidgets import QApplication
    QTest.keyClick(QApplication.focusWidget() or w, Qt.Key_Escape)
    QApplication.processEvents()


def presses_to(w, done, limit=5):
    n = 0
    while not done() and n < limit:
        esc(w)
        n += 1
    assert done(), f"still not back after {limit} presses"
    return n


def click(s, vp, u, v):
    x, y = s.scr(vp, (u, v))
    s.mouse_move(vp, x, y, _Ev())
    s.mouse_press(vp, x, y, _Ev())
    s.mouse_release(vp, x, y, _Ev(), False)


def idle(s):
    return s.tool == "select" and not s.selection and s._editor is None and not s.temp \
        and s.chain_anchor is None and s.dim_first is None and not s.cons_picks


def _a_line(s, vp):
    s.set_tool("line")
    click(s, vp, 10, 10)
    click(s, vp, 30, 22)
    s.set_tool("select")
    return next(i for i, e in s.sk.entities.items() if e.get("type") == "line")


# ------------------------------------------------------------------ sketch: one press from each state
def test_line_mid_chain_with_its_box_offered_is_one_press(sk):
    w, s, vp = sk
    s.set_tool("line")
    click(s, vp, 10, 10)
    click(s, vp, 30, 22)
    assert s._editor is not None and s.chain_anchor is not None
    assert presses_to(w, lambda: idle(s)) == 1


def test_rectangle_just_drawn_is_one_press(sk):
    w, s, vp = sk
    s.set_tool("rect")
    click(s, vp, 10, 10)
    click(s, vp, 30, 22)
    assert s._editor is not None
    assert presses_to(w, lambda: idle(s)) == 1
    assert not [c for c in s.sk.constraints if c["type"] == "distance"], "Esc must not add the offered dimension"


def test_half_drawn_circle_is_one_press_and_is_dropped(sk):
    w, s, vp = sk
    s.set_tool("circle")
    click(s, vp, 20, 15)
    assert s.temp
    assert presses_to(w, lambda: idle(s)) == 1
    assert not [e for e in s.sk.entities.values() if e.get("type") == "circle"]


def test_idle_tool_is_one_press(sk):
    w, s, vp = sk
    s.set_tool("line")
    assert presses_to(w, lambda: idle(s)) == 1


def test_dimension_tool_after_a_pick_is_one_press(sk):
    w, s, vp = sk
    lid = _a_line(s, vp)
    s.set_tool("dimension")
    s.dim_first = lid
    s.selection = [lid]
    assert presses_to(w, lambda: idle(s)) == 1


def test_constraint_tool_after_a_pick_is_one_press(sk):
    w, s, vp = sk
    lid = _a_line(s, vp)
    s.set_tool("parallel")
    s.cons_picks = [lid]
    s.selection = [lid]
    assert presses_to(w, lambda: idle(s)) == 1


def test_selection_with_select_tool_is_one_press(sk):
    w, s, vp = sk
    s.selection = [_a_line(s, vp)]
    assert presses_to(w, lambda: idle(s)) == 1


def test_spline_keeps_its_points_and_leaves_the_tool_in_one_press(sk):
    w, s, vp = sk
    s.set_tool("spline")
    for u, v in ((10, 10), (20, 18), (30, 12)):
        click(s, vp, u, v)
    assert presses_to(w, lambda: idle(s)) == 1
    assert [e for e in s.sk.entities.values() if e.get("type") == "spline"]


def test_idle_sketch_spends_no_press(sk):
    """From idle, Esc changes nothing and nothing is left to clear."""
    w, s, vp = sk
    esc(w)
    assert idle(s)


# ------------------------------------------------------------------ sketch: value boxes
def _dimensioned_line(s, vp):
    lid = _a_line(s, vp)
    c = s.sk.add_constraint("distance", lid, value=25.0)
    s.sk.solve()
    return c


def test_esc_in_a_dimension_being_edited_cancels_only_that_edit(sk):
    w, s, vp = sk
    c = _dimensioned_line(s, vp)
    s.set_tool("circle")                       # the editor opens over a tool, which must survive the Esc
    s.edit_dimension(vp, c)
    s._editor.setText("40")
    assert presses_to(w, lambda: s._editor is None) == 1
    assert c["value"] == pytest.approx(25.0)
    assert s.tool == "circle"


def test_dimension_editor_after_a_value_prompt_closes_in_one_press(sk):
    """A value prompt closed with Enter used to leave its finish behind, so the next box took two presses."""
    w, s, vp = sk
    c = _dimensioned_line(s, vp)
    s._prompt(vp, (0, 0), "Value", 5.0, lambda v: None)
    s._editor.returnPressed.emit()
    assert s._editor is None
    s.edit_dimension(vp, c)
    assert presses_to(w, lambda: s._editor is None) == 1


def test_typing_in_an_offered_box_makes_esc_cancel_just_that(sk):
    w, s, vp = sk
    s.set_tool("rect")
    click(s, vp, 10, 10)
    click(s, vp, 30, 22)
    QTest.keyClicks(s._editor, "7")
    esc(w)
    assert s._editor is None and s.tool == "rect"
    assert not [c for c in s.sk.constraints if c["type"] == "distance"]
    esc(w)
    assert idle(s)


def test_tool_keys_work_straight_after_esc(sk):
    """Esc closing an offered box left the focus on the window, so the next tool key did nothing."""
    w, s, vp = sk
    s.set_tool("rect")
    click(s, vp, 10, 10)
    click(s, vp, 30, 22)
    esc(w)
    from PySide6.QtWidgets import QApplication
    QTest.keyClick(QApplication.focusWidget() or w, Qt.Key_L)
    assert s.tool == "line"


# ------------------------------------------------------------------ part studio
def test_part_selection_is_one_press(part):
    from freeshape.ui.viewport import Pick
    vp = part.viewport
    vp.selection = [Pick("face", (next(iter(vp.part_shapes)), 0))]
    vp.setFocus()
    assert presses_to(part, lambda: not vp.selection) == 1


def test_feature_dialog_is_one_press(part):
    part.start_feature("extrude")
    assert part.dialog is not None
    assert presses_to(part, lambda: part.dialog is None and part.mode == "idle") == 1


def test_feature_picked_in_the_list_is_one_press(part):
    """Esc used to clear only the model's selection: a feature picked in the list stayed picked for good."""
    from freeshape.ui import panels
    fl = part.left.features
    fl.setFocus()
    it = next(fl.item(i) for i in range(fl.count()) if fl.item(i).data(panels.ROLE_KIND) == "feature")
    fl.setCurrentItem(it)
    it.setSelected(True)
    assert fl.selected_feature_ids() and part.viewport.feature_hl
    assert presses_to(part, lambda: not fl.selected_feature_ids() and not part.viewport.feature_hl) == 1


# ------------------------------------------------------------------ cursors
def _tools():
    from freeshape.ui.sketcher import CONSTRAINT_TOOLS, TOOL_HINTS
    return sorted((set(TOOL_HINTS) | set(CONSTRAINT_TOOLS)) - {"select", "insert_file"})


def _img(cur):
    return cur.pixmap().toImage()


def test_every_sketch_tool_has_its_own_cursor():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui import cursors
    seen = {}
    for t in _tools():
        cur = cursors.tool_cursor(t)
        pm = cur.pixmap()
        assert not pm.isNull() and (pm.width(), pm.height()) == (cursors.SIZE, cursors.SIZE), t
        assert (cur.hotSpot().x(), cur.hotSpot().y()) == (cursors.HOT, cursors.HOT), t
        key = bytes(pm.toImage().constBits())
        assert key not in seen, f"{t} looks the same as {seen.get(key)}"
        seen[key] = t
    assert cursors.tool_cursor("select").shape() == Qt.ArrowCursor
    orbit = cursors.nav_cursor("orbit")
    assert not orbit.pixmap().isNull() and bytes(orbit.pixmap().toImage().constBits()) not in seen
    assert cursors.nav_cursor("pan").shape() == Qt.ClosedHandCursor


def test_sketch_viewport_shows_the_tool_cursor_and_esc_restores_the_arrow(sk):
    w, s, vp = sk
    from freeshape.ui import cursors
    assert _img(vp.cursor()) == _img(cursors.tool_cursor(s.tool))       # a new sketch opens in Line
    for t in ("line", "circle", "dimension", "trim"):
        w._sk_tool(t)
        assert _img(vp.cursor()) == _img(cursors.tool_cursor(t)), t
    esc(w)
    assert vp.cursor().shape() == Qt.ArrowCursor
    w._sk_tool("rect")
    w._exit_sketch()
    assert vp.cursor().shape() == Qt.ArrowCursor
