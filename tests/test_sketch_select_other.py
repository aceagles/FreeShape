"""Select other: every entity under the cursor is listed, hover / choose / close drive hover and selection."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import Qt  # noqa: E402

from freeshape.sketch import Sketch  # noqa: E402
from tests.test_sketch_inference import VP, _Ev, _sketcher  # noqa: E402

MID = (430, 300)        # on the stacked lines, clear of the origin and the end points (screen px)


class _ShiftEv(_Ev):
    def modifiers(self):
        return Qt.ShiftModifier


class _Key:
    def __init__(self, key):
        self._k = key

    def key(self):
        return self._k

    def modifiers(self):
        return Qt.NoModifier


class _Menu:
    def __init__(self):
        self.labels = []

    def addAction(self, label, fn=None):
        self.labels.append(label)


def _stacked():
    """Three lines on the same two end positions: l1 solid, l2 and l3 construction."""
    sk = Sketch()
    a, b = sk.add_point(-10, 0), sk.add_point(10, 0)
    c, d = sk.add_point(-10, 0), sk.add_point(10, 0)
    e, f = sk.add_point(-10, 0), sk.add_point(10, 0)
    ids = [sk.add_line(a, b), sk.add_line(c, d, True), sk.add_line(e, f, True)]
    return _sketcher(sk), ids


def _recording(s):
    made = []
    s.popup_factory = lambda vp, x, y, rows, hov, ch, cl: made.append((rows, hov, ch, cl)) or made[-1]
    return made


def test_hits_lists_all_three_and_hit_is_the_first():
    s, ids = _stacked()
    got = [i for k, i in s.hits(VP, *MID, kinds=("entity",))]
    assert sorted(got) == sorted(ids)
    assert s.hit(VP, *MID, kinds=("entity",)) == ("entity", got[0])


def test_rows_are_three_and_two_are_construction():
    s, _ = _stacked()
    rows = s.select_other_rows(VP, *MID)
    assert len(rows) == 3
    assert sum(label.endswith("(construction)") for label, _ in rows) == 2


def test_choose_replaces_selection_and_shift_adds():
    s, _ = _stacked()
    made = _recording(s)
    s.open_select_other(VP, *MID)
    rows, _hov, choose, _close = made[0]
    lines = [i for lbl, i in rows if lbl.startswith("Line")]
    choose(lines[1], Qt.NoModifier)
    assert s.selection == [lines[1]]
    choose(lines[2], Qt.ShiftModifier)
    assert set(s.selection) == {lines[1], lines[2]}


def test_hover_sets_hover_and_close_clears_it():
    s, _ = _stacked()
    made = _recording(s)
    s.open_select_other(VP, *MID)
    _rows, hover, _choose, close = made[0]
    hover("l2")
    assert s.hover == "l2"
    close()
    assert s.hover is None


def test_context_action_needs_two_hits():
    s, _ = _stacked()
    m = _Menu()
    s.add_context_actions(m, VP, *MID)
    assert m.labels.count("Select other…") == 1
    one = Sketch()
    one.add_line(one.add_point(-10, 0), one.add_point(10, 0))
    s1 = _sketcher(one)
    m1 = _Menu()
    s1.add_context_actions(m1, VP, *MID)
    assert "Select other…" not in m1.labels


def test_backtick_opens_list_only_with_select_tool():
    s, _ = _stacked()
    made = _recording(s)
    s.mouse_move(VP, *MID, _Ev())
    assert s.key_press(VP, _Key(Qt.Key_QuoteLeft))
    assert len(made) == 1
    s.set_tool("line")
    s.mouse_move(VP, *MID, _Ev())
    s.key_press(VP, _Key(Qt.Key_QuoteLeft))
    assert len(made) == 1


def test_backtick_with_one_hit_does_nothing():
    sk = Sketch()
    sk.add_line(sk.add_point(-10, 0), sk.add_point(10, 0))
    s = _sketcher(sk)
    made = _recording(s)
    s.mouse_move(VP, *MID, _Ev())
    s.key_press(VP, _Key(Qt.Key_QuoteLeft))
    assert made == []


def test_popup_keyboard_and_callbacks():
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    from freeshape.ui.select_other import SelectOtherPopup
    QApplication.instance() or QApplication([])
    log = []
    p = SelectOtherPopup([("A", 1), ("B", 2)], lambda x: log.append(("hover", x)),
                         lambda x, m: log.append(("choose", x, m)), lambda: log.append("close"))

    def press(k, mods=Qt.NoModifier):
        p.keyPressEvent(QKeyEvent(QEvent.KeyPress, k, mods))
    p.setCurrentRow(0)
    press(Qt.Key_Down)
    press(Qt.Key_Return, Qt.ShiftModifier)
    assert log == [("hover", 1), ("hover", 2), "close", ("choose", 2, Qt.ShiftModifier)]
    q = SelectOtherPopup([("A", 1)], on_close=lambda: log.append("esc"))
    press_q = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    q.keyPressEvent(press_q)
    assert log.count("esc") == 1
