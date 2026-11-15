"""Slot tool on an existing chain, and the Offset tool's Both sides toggle (card I1.7). Offscreen sketcher harness."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLineEdit  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from test_sketch_tool_keys import _Ev, _click, _sketching  # noqa: E402


def _chain(s):
    """Arc - line - arc: a 40 mm line with a quarter arc of radius 10 turning up at each end."""
    p0, p1 = s.add_point(0, 0), s.add_point(40, 0)
    p2, p3 = s.add_point(50, 10), s.add_point(-10, 10)
    c1, c0 = s.add_point(40, 10), s.add_point(0, 10)
    line = s.add_line(p0, p1)
    a1 = s.add_arc(c1, p1, p2)
    a0 = s.add_arc(c0, p3, p0)
    s.add_constraint("tangent", a1, line)
    s.add_constraint("tangent", a0, line)
    s.solve()
    return line, a0, a1, p3, 40 + 2 * (math.pi * 10 / 2)


def _areas(s):
    return sorted(K.face_info(f)["area"] for f in K.sketch_regions(K.std_plane("Top"), s.curves()))


def _type(s, text):
    ed = s._editor
    assert isinstance(ed, QLineEdit)
    ed.clear()
    QTest.keyClicks(ed, text)
    QTest.keyClick(ed, Qt.Key_Return)
    assert s._editor is None


def _offsets(s):
    return [c for c in s.sk.constraints if c["type"] == "offset"]


def test_slot_on_a_chain_makes_one_closed_region_and_the_chain_is_construction():
    w, s, vp = _sketching()
    line, a0, a1, _p, length = _chain(s.sk)
    s.set_tool("slot")
    _click(s, vp, 20, 0)
    _type(s, "4")
    assert s.sk.status["ok"], s.sk.status
    areas = _areas(s.sk)
    assert len(areas) == 1 and abs(areas[0] - (length * 4 + math.pi * 4)) < 1e-3, areas
    assert all(s.sk.entities[k]["construction"] for k in (line, a0, a1))


def test_one_undo_removes_everything_the_slot_added():
    w, s, vp = _sketching()
    _chain(s.sk)
    before = s.sk.to_dict()
    s.set_tool("slot")
    _click(s, vp, 20, 0)
    _type(s, "4")
    assert len(s.sk.entities) > len(before["entities"])
    s.undo()
    assert s.sk.to_dict() == before


def test_two_clicks_on_empty_space_still_make_the_straight_slot():
    w, s, vp = _sketching()
    s.set_tool("slot")
    _click(s, vp, 10, 10)
    _click(s, vp, 40, 10)
    _type(s, "6")
    areas = _areas(s.sk)
    assert len(areas) == 1 and abs(areas[0] - (30 * 6 + math.pi * 9)) < 1e-3, areas


def test_offset_both_sides_gives_two_copies_held_by_one_dimension():
    w, s, vp = _sketching()
    s.sk.add_line(s.sk.add_point(0, 0), s.sk.add_point(40, 0))
    s.sk.solve()
    btn = w._offset_both_btn
    s.set_tool("offset")
    assert btn.isChecked() is False and btn.isVisibleTo(btn.parent())
    btn.setChecked(True)
    assert s.offset_both is True
    _click(s, vp, 20, 0)
    _type(s, "5")
    lines = [e for e in s.sk.entities.values() if e["type"] == "line"]
    assert len(lines) == 3 and len(_offsets(s)) == 1
    assert len({c["id"] for c in _offsets(s)}) == 1
    ys = sorted(round(float(s.sk.P(e["p1"])[1]), 6) for e in lines)
    assert ys == [-5.0, 0.0, 5.0], ys


def test_offset_with_both_sides_off_is_one_copy_and_the_toggle_is_remembered():
    w, s, vp = _sketching()
    s.sk.add_line(s.sk.add_point(0, 0), s.sk.add_point(40, 0))
    s.sk.solve()
    s.set_tool("offset")
    _click(s, vp, 20, 0)
    _type(s, "5")
    assert len([e for e in s.sk.entities.values() if e["type"] == "line"]) == 2
    w._offset_both_btn.setChecked(True)
    s.set_tool("line")
    assert not w._offset_both_btn.isVisibleTo(w._offset_both_btn.parent())
    s.set_tool("offset")
    assert w._offset_both_btn.isChecked() and s.offset_both


def test_dragging_a_chain_end_keeps_the_slot_closed():
    w, s, vp = _sketching()
    _l, _a0, _a1, p3, _len = _chain(s.sk)
    s.set_tool("slot")
    _click(s, vp, 20, 0)
    _type(s, "4")
    s.set_tool("select")
    x, y = s.scr(vp, s.sk.P(p3))
    s.mouse_move(vp, x, y, _Ev())
    s.mouse_press(vp, x, y, _Ev())
    x2, y2 = s.scr(vp, s.sk.P(p3) + np.array([-3.0, 6.0]))
    s.mouse_drag(vp, x2, y2, _Ev())
    s.mouse_release(vp, x2, y2, _Ev(), True)
    assert s.sk.status["ok"], s.sk.status
    assert len(_areas(s.sk)) == 1


def test_search_finds_slot_by_offset_both_sides_while_sketching():
    from freeshape.ui.palette import search_commands
    w, s, vp = _sketching()
    cmds = w._palette_commands()
    for q in ("offset both sides", "bi-directional offset", "wall", "slot from curve"):
        assert "Slot" in [c.name for c in search_commands(cmds, q)], q
    assert "Offset" in [c.name for c in search_commands(cmds, "both sides")]
