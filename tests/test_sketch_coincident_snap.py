"""Sketching: Coincident with a midpoint badge selected, warnings for unusable selections, and line ends snapping
onto an existing (midpoint) point."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from PySide6.QtCore import QRectF  # noqa: E402

from test_sketch_inference import VP, _Ev, _click, _sketcher  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _build():
    """Fixed horizontal line at y=10, a point entity at its midpoint (with a midpoint constraint), and a
    vertical line from (0, 40) down to a free end at (0, 20)."""
    sk = Sketch()
    a, b = sk.add_point(-20, 10), sk.add_point(20, 10)
    hl = sk.add_line(a, b)
    for p in (a, b):
        sk.add_constraint("fix", p, value=list(sk.P(p)))
    m = sk.add_point(0, 10)
    mid = sk.add_constraint("midpoint", m, hl)
    pe = sk.add_point_entity(m)
    top, bot = sk.add_point(0, 40), sk.add_point(0, 20)
    sk.add_constraint("fix", top, value=[0, 40])
    sk.add_line(top, bot)
    sk.solve()
    return sk, m, pe, mid["id"], bot


def test_coincident_with_midpoint_badge_moves_endpoint_onto_point():
    sk, m, _pe, badge, bot = _build()
    s = _sketcher(sk)
    s.selection = [bot, badge]
    assert s._apply_constraint("coincident", s.selection) is True
    assert np.allclose(sk.P(bot), sk.P(m), atol=1e-6)
    assert ("coincident" in [c["type"] for c in sk.constraints])


def test_set_tool_coincident_with_badge_selected_applies():
    sk, m, _pe, badge, bot = _build()
    s = _sketcher(sk)
    s.selection = [bot, badge]
    s.set_tool("coincident")
    assert s.tool == "select"
    assert np.allclose(sk.P(bot), sk.P(m), atol=1e-6)


def test_unusable_selection_shows_the_tool_prompt():
    sk, m, pe, badge, bot = _build()
    s = _sketcher(sk)
    # a point and its own badge resolve to one point: nothing to make coincident
    s.selection = [m, badge]
    assert s._apply_constraint("coincident", s.selection) is False
    assert s._flashing and "Select two points" in s.hint() and s.hint().startswith("⚠")
    # the warning clears on the next click
    s.mouse_press(VP, 10, 10, _Ev())
    assert not s._flashing


def test_one_pick_waiting_for_the_second_does_not_warn():
    sk, m, pe, badge, bot = _build()
    s = _sketcher(sk)
    assert s._apply_constraint("coincident", [bot]) is False
    assert not getattr(s, "_flashing", False)


def test_select_mode_prefers_point_over_constraint_glyph():
    sk, m, pe, badge, bot = _build()
    s = _sketcher(sk)
    # the point m is at screen (400, 200); the M badge rect overlaps it
    s._glyph_rects = [(QRectF(396, 190, 20, 14), badge)]
    assert s.hit(VP, 402, 200) == ("point", m)
    # away from the point the badge is still hit
    s._glyph_rects = [(QRectF(430, 190, 20, 14), badge)]
    assert s.hit(VP, 435, 195) == ("constraint", badge)


def _drawing():
    sk = Sketch()
    a, b = sk.add_point(-20, 10), sk.add_point(20, 10)
    hl = sk.add_line(a, b)
    for p in (a, b):
        sk.add_constraint("fix", p, value=list(sk.P(p)))
    m = sk.add_point(0, 10)
    sk.add_constraint("midpoint", m, hl)
    sk.add_point_entity(m)
    sk.solve()
    s = _sketcher(sk)
    s.set_tool("line")
    _click(s, 400, -100)                      # start at (0, 40)
    return s, sk, m


def test_line_end_near_midpoint_point_snaps_onto_it_without_a_new_point():
    for dx, dy in ((0, 0), (3, 3), (-5, 4), (0, -6)):
        s, sk, m = _drawing()
        n = len(sk.points)
        s.mouse_move(VP, 400 + dx, 200 + dy, _Ev())
        assert s.snap_info == ("point", m)
        s.mouse_press(VP, 400 + dx, 200 + dy, _Ev())
        assert len(sk.points) == n, "the end must reuse the existing point"
        line = [e for e in sk.entities.values() if e["type"] == "line"][-1]
        assert m in (line["p1"], line["p2"])


def test_existing_point_wins_over_vertical_alignment_inference():
    # cursor 12 px to the side: its alignment with the line start puts the end on the point, which wins
    s, sk, m = _drawing()
    n = len(sk.points)
    s.mouse_move(VP, 412, 203, _Ev())
    assert s.snap_info == ("point", m)
    assert np.allclose(s.cursor_uv, sk.P(m))
    s.mouse_press(VP, 412, 203, _Ev())
    assert len(sk.points) == n
    assert not any(c["type"] in ("vertical", "horizontal") and len(c["refs"]) == 2 for c in sk.constraints)


# ---------------------------------------------------------------- a level / plumb line ending on a curve
def _cons(sk):
    return [(c["type"], tuple(c["refs"])) for c in sk.constraints]


def test_a_plumb_line_ending_on_a_circle_is_vertical_and_coincident():
    sk = Sketch()
    circ = sk.add_circle("origin", 20)
    s = _sketcher(sk)
    s.set_tool("line")
    _click(s, 450, 300)                         # start at (5, 0), inside the circle
    _click(s, 452, 107)                         # a little off plumb, on the circle near its top
    ln = next(i for i, e in sk.entities.items() if e["type"] == "line")
    end = sk.entities[ln]["p2"]
    assert ("vertical", (ln,)) in _cons(sk) and ("point_on", (end, circ)) in _cons(sk)
    assert np.allclose(sk.P(end), [5, (400 - 25) ** 0.5], atol=1e-6)


def test_a_level_line_ending_on_a_line_is_horizontal_and_coincident():
    sk = Sketch()
    wall = sk.add_line(sk.add_point(10, -10), sk.add_point(10, 10))
    s = _sketcher(sk)
    s.set_tool("line")
    _click(s, 430, 270)                         # start at (3, 3)
    _click(s, 500, 269)                         # a little off level, on the wall (away from its midpoint)
    ln = next(i for i, e in sk.entities.items() if e["type"] == "line" and i != wall)
    end = sk.entities[ln]["p2"]
    assert ("horizontal", (ln,)) in _cons(sk) and ("point_on", (end, wall)) in _cons(sk)
    assert np.allclose(sk.P(end), [10, 3], atol=1e-6)


def test_a_crossing_too_far_from_the_cursor_keeps_only_the_alignment():
    sk = Sketch()
    sk.add_line(sk.add_point(-10, 20), sk.add_point(30, 20))
    s = _sketcher(sk)
    s.ALIGNED_ON_PX = 0.0                       # no crossing counts as near the cursor
    s.set_tool("line")
    _click(s, 450, 300)                         # start at (5, 0)
    _click(s, 451, 100)                         # on the line, a little off plumb
    kinds = [c for c, _r in _cons(sk)]
    assert "vertical" in kinds and "point_on" not in kinds
