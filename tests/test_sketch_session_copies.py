"""I14.1: mirror, linear pattern and circular pattern on the server (SketchSession.mirror, linear_pattern,
circular_pattern and the `sketch.mirror`, `sketch.linear_pattern`, `sketch.circular_pattern` messages; shapes in the
serve.py docstring, "Sketch sessions"). The desktop's own Sketcher is the reference: the same picks go to it
(headless, through its tool clicks and prompts) and to the session, and the whole sketches are compared. Each test
builds what it needs, so each passes alone."""
import json
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aiohttp  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import sketch_ops as OPS  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.sketch_session import MAX_ITEMS, MAX_PATTERN_COUNT, SketchError, SketchSession  # noqa: E402
from test_sketch_inference import VP, _Ev, _sketcher  # noqa: E402
from test_sketch_polygon import _QtVP  # noqa: E402
from test_sketch_session import (TOP, _alive, _ask, _connect, _open_doc, _outputs, _run, _skipped,  # noqa: E402,F401
                                 root)


# ---------------------------------------------------------------------- the scene and the two drivers
def _px(u, v):
    """Screen position of sketch point (u, v) in the desktop test viewport (10 px per mm, origin at 400, 300)."""
    return 400 + 10 * u, 300 - 10 * v


def _scene():
    """A vertical construction axis through (0, 0) (both ends fixed), two lines that meet at P = (0, 10) on the axis,
    a circle, an arc, a construction line, and a distance dimension on the first line. Returns (sketch dict, names)."""
    sk = Sketch()
    a0, a1 = sk.add_point(0, -30), sk.add_point(0, 30)
    n = {"axis": sk.add_line(a0, a1, True), "a0": a0, "a1": a1}
    for p in (a0, a1):
        sk.add_constraint("fix", p, value=list(sk.P(p)))
    n["P"] = sk.add_point(0, 10)
    q1, q2 = sk.add_point(15, 0), sk.add_point(12, 22)
    n["l1"], n["l2"] = sk.add_line(n["P"], q1), sk.add_line(n["P"], q2)
    n["circle"] = sk.add_circle(sk.add_point(20, -10), 4.0)
    n["arc"] = sk.add_arc(sk.add_point(25, 15), sk.add_point(29, 15), sk.add_point(25, 19))
    n["cons"] = sk.add_line(sk.add_point(5, -20), sk.add_point(18, -25), True)
    sk.add_constraint("distance", n["l1"], value=float(np.linalg.norm(sk.P(n["P"]) - sk.P(q1))))
    sk.solve()
    return sk.to_dict(), n


def _everything(n):
    return [n["l1"], n["l2"], n["circle"], n["arc"], n["cons"]]


def _equal(a, b, path="sketch"):
    """The same dicts: the same keys and values, numbers within 1e-9."""
    if isinstance(a, dict):
        assert isinstance(b, dict) and sorted(a) == sorted(b), f"{path}: keys {sorted(a)} vs {sorted(b)}"
        for k in a:
            _equal(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        assert isinstance(b, (list, tuple)) and len(a) == len(b), f"{path}: {a} vs {b}"
        for i, (x, y) in enumerate(zip(a, b)):
            _equal(x, y, f"{path}[{i}]")
    elif isinstance(a, bool) or isinstance(a, str) or a is None:
        assert a == b, f"{path}: {a} vs {b}"
    else:
        assert abs(float(a) - float(b)) <= 1e-9, f"{path}: {a} vs {b}"


def _desk_mirror(d, sel, axis_at):
    s = _sketcher(Sketch.from_dict(d))
    s.selection = list(sel)
    s.set_tool("sk_mirror")
    s.mouse_move(VP, *_px(*axis_at), _Ev())
    s.mouse_press(VP, *_px(*axis_at), _Ev())
    assert len(s.sk.entities) > len(d["entities"]) and s.tool == "select"
    return s


def _type(s, text):
    s._editor.setText(text)
    s._editor.textEdited.emit(text)
    s._editor.returnPressed.emit()


def _desk_pattern(d, sel, tool, click_at, first, second):
    """Drive the desktop: select, choose the tool, click, type `first` and Enter, `second` and Enter."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    vp = _QtVP()
    try:
        s = _sketcher(Sketch.from_dict(d))
        s.selection = list(sel)
        s.set_tool(tool)
        s.mouse_move(vp, *_px(*click_at), _Ev())
        s.mouse_press(vp, *_px(*click_at), _Ev())
        assert s._editor is not None
        _type(s, first)
        assert s._editor is not None
        _type(s, second)
        assert s._editor is None and s.tool == "select"
        return s
    finally:
        vp.close()
        vp.deleteLater()


def _new(sketch, variables=None):
    s = SketchSession("D1", "T1", None, TOP, sketch, name="Sketch 1")
    s.variables = dict(variables or {})
    return s


def _snapshot(s):
    return s.sk.to_dict(), s._status, list(s.undo_stack), list(s.redo_stack)


def _same_as_desktop(desk, ses, before, made):
    _equal(desk.sk.to_dict(), ses.sk.to_dict())
    new = sorted(set(desk.sk.entities) - set(before["entities"]))
    assert new and sorted(made) == new
    assert len(ses.undo_stack) == 1


# ---------------------------------------------------------------------- mirror
@pytest.mark.parametrize("which", ["all", "line1 only"])
def test_mirror_matches_the_desktop_sketch_for_sketch(which):
    d, n = _scene()
    sel = _everything(n) if which == "all" else [n["l1"]]
    desk = _desk_mirror(d, sel, (0, -15))
    s = _new(d)
    made = s.mirror({"ids": sel, "line": n["axis"]})
    assert list(made) == ["entities"]
    _same_as_desktop(desk, s, d, made["entities"])


def test_mirror_links_the_copies_shares_the_axis_point_and_copies_no_dimension():
    d, n = _scene()
    s = _new(d)
    made = s.mirror({"ids": _everything(n), "line": n["axis"]})["entities"]
    sk = s.sk
    new = {sk.entities[e]["type"]: e for e in made}
    old_pts = {p for i in _everything(n) for p in Sketch.from_dict(d).entity_points(i)}
    sym = [c for c in sk.constraints if c["type"] == "symmetric"]
    assert all(c["refs"][2] == n["axis"] for c in sym)
    # one symmetric per copied point; P, on the axis, is shared and gets none
    assert sorted(c["refs"][0] for c in sym) == sorted(old_pts - {n["P"]})
    mirrored_circle = [e for e in made if sk.entities[e]["type"] == "circle"][0]
    assert ("equal", [n["circle"], mirrored_circle]) in [(c["type"], c["refs"]) for c in sk.constraints]
    assert len([c for c in sk.constraints if c["type"] == "equal"]) == 1
    # P is shared: both mirrored lines end on it, and no copy of it exists
    mirrored_lines = [sk.entities[e] for e in made if sk.entities[e]["type"] == "line"]
    assert sum(1 for m in mirrored_lines if n["P"] in (m["p1"], m["p2"])) == 2
    assert len(sk.points) == len(Sketch.from_dict(d).points) + len(old_pts) - 1
    # the arc's ends are swapped so it still runs counter-clockwise
    arc_new, arc_old = sk.entities[new["arc"]], sk.entities[n["arc"]]
    assert (arc_new["p1"], arc_new["p2"]) != (arc_old["p1"], arc_old["p2"])
    a = sk.arc_angles(new["arc"])
    assert sk.P(arc_new["p1"])[0] == pytest.approx(-sk.P(arc_old["p2"])[0], abs=1e-6)
    assert abs(a[1] - a[0]) == pytest.approx(abs(sk.arc_angles(n["arc"])[1] - sk.arc_angles(n["arc"])[0]), abs=1e-6)
    # the construction line stays construction, the others are not
    cons_new = [e for e in made if sk.entities[e]["type"] == "line" and sk.entities[e]["construction"]]
    assert len(cons_new) == 1
    assert not any(sk.entities[e]["construction"] for e in made if e not in cons_new)
    # no dimension was copied
    assert len([c for c in sk.constraints if c["type"] == "distance"]) == 1
    # drag the source circle: its mirror image follows
    c_old, c_new = sk.entities[n["circle"]]["c"], sk.entities[mirrored_circle]["c"]
    s.drag_begin({"entity": n["circle"], "at": [20, -10]})
    s.drag_move({"at": [24, -7]})
    s.drag_end(False)
    assert s.sk.P(c_old) == pytest.approx([24, -7], abs=1e-5)
    assert s.sk.P(c_new) == pytest.approx([-24, -7], abs=1e-5)


# ---------------------------------------------------------------------- linear pattern
@pytest.mark.parametrize("side", [-15, 20])
def test_linear_pattern_matches_the_desktop_and_a_click_on_the_other_side_reverses_it(side):
    d, n = _scene()
    sel = _everything(n)
    desk = _desk_pattern(d, sel, "sk_lpattern", (0, side), "4", "12")
    s = _new(d)
    made = s.linear_pattern({"ids": sel, "line": n["axis"], "at": [0, side], "count": 4, "spacing": 12})
    _same_as_desktop(desk, s, d, made["entities"])
    assert made["count"] == 4 and made["spacing"] == 12.0
    # the copies lie along the axis, up for a click above the selection's middle, down for one below
    first = s.sk.entities[made["entities"][0]]
    step = s.sk.P(first["p1"]) - Sketch.from_dict(d).P(s.sk.entities[n["l1"]]["p1"])
    assert abs(step[0]) < 1e-9 and np.sign(step[1]) == np.sign(made["direction"][1])
    assert (made["direction"][1] > 0) == (side > 0)


def test_linear_pattern_defaults_are_three_and_the_spacing_the_desktop_prompt_opens_with():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    d, n = _scene()
    sel = _everything(n)
    vp = _QtVP()
    try:
        s = _sketcher(Sketch.from_dict(d))
        floor = s._nice(vp, 40)
        s.selection = list(sel)
        s.set_tool("sk_lpattern")
        s.mouse_press(vp, *_px(0, 20), _Ev())
        assert s._editor is not None and len(s.sk.entities) == len(d["entities"]) + 2 * len(sel)      # 3 instances
        opened = s.sk.P(s.sk.entities[[k for k in s.sk.entities if k not in d["entities"]][0]]["p1"]) \
            - Sketch.from_dict(d).P(Sketch.from_dict(d).entities[n["l1"]]["p1"])
        s._end_editor()
    finally:
        vp.close()
        vp.deleteLater()
    ses = _new(d)
    made = ses.linear_pattern({"ids": sel, "line": n["axis"], "at": [0, 20], "floor": floor})
    assert made["count"] == 3
    sk = Sketch.from_dict(d)
    direction = OPS.pattern_direction(sk, sel, n["axis"], [0, 20])
    assert made["spacing"] == OPS.pattern_spacing(sk, sel, direction, floor)
    assert made["direction"] == pytest.approx(list(direction))
    assert len(made["entities"]) == 2 * len(sel)
    # that is what the desktop's prompt opened with: the first copy is one spacing along the direction
    unit = direction / np.linalg.norm(direction)
    assert opened == pytest.approx(unit * made["spacing"], abs=1e-6)
    # no floor and no spacing: 1.25 times the extent
    bare = _new(d).linear_pattern({"ids": sel, "line": n["axis"], "at": [0, 20]})
    assert bare["spacing"] == round(OPS.extent_along(sk, sel, direction) * 1.25, 3)


def test_the_desktop_direction_and_opening_spacing_are_the_shared_functions_on_each_side():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    d, n = _scene()
    vp = _QtVP()
    try:
        for sel in ([n["l1"], n["circle"]], _everything(n)):
            for side in (-15, 20):
                s = _sketcher(Sketch.from_dict(d))
                s.selection = list(sel)
                s.set_tool("sk_lpattern")
                s.mouse_press(vp, *_px(0, side), _Ev())
                base = Sketch.from_dict(d)
                d_ = OPS.pattern_direction(base, sel, n["axis"], np.array([0.0, side]))
                spacing = OPS.pattern_spacing(base, sel, d_, s._nice(vp, 40))
                first = [k for k in s.sk.entities if k not in d["entities"] and s.sk.entities[k]["type"] == "line"][0]
                offset = s.sk.P(s.sk.entities[first]["p1"]) - base.P(base.entities[sel[0]]["p1"])
                assert offset == pytest.approx(d_ / np.linalg.norm(d_) * spacing, abs=1e-6)
                s._end_editor()
    finally:
        vp.close()
        vp.deleteLater()


# ---------------------------------------------------------------------- circular pattern
@pytest.mark.parametrize("centre,count,angle", [("point", 6, 360), ("space", 4, 90), ("space", 3, -120)])
def test_circular_pattern_matches_the_desktop(centre, count, angle):
    d, n = _scene()
    sel = _everything(n)
    at = (0, 10) if centre == "point" else (-20, -20)
    desk = _desk_pattern(d, sel, "sk_cpattern", at, str(count), str(angle))
    s = _new(d)
    pick = {"at": list(at), "point": n["P"]} if centre == "point" else list(at)
    made = s.circular_pattern({"ids": sel, "centre": pick, "count": count, "angle": angle})
    _same_as_desktop(desk, s, d, made["entities"])
    assert made["count"] == count and made["angle"] == angle and made["centre"] == list(at)
    # 360 divides by the count, any other angle by the count less one: the last copy of the circle's centre
    c0 = np.array(Sketch.from_dict(d).P(s.sk.entities[n["circle"]]["c"])) - np.array(at)
    last = [e for e in made["entities"] if s.sk.entities[e]["type"] == "circle"][-1]
    turned = np.array(s.sk.P(s.sk.entities[last]["c"])) - np.array(at)
    step = angle / (count if abs(angle) == 360 else count - 1)
    want = math.radians(step * (count - 1))
    R = np.array([[math.cos(want), -math.sin(want)], [math.sin(want), math.cos(want)]])
    assert turned == pytest.approx(R @ c0, abs=1e-6)


def test_a_centre_point_is_a_position_not_a_link():
    d, n = _scene()
    s = _new(d)
    s.circular_pattern({"ids": [n["circle"]], "centre": {"at": [0, 10], "point": n["P"]}, "count": 3})
    assert not any(n["P"] in c["refs"] for c in s.sk.constraints if c["type"] == "coincident")
    assert [c for c in s.sk.constraints if c["id"] not in {x["id"] for x in d["constraints"]}] == []


# ---------------------------------------------------------------------- what is copied
def _rectangle_with_dimension():
    sk = Sketch()
    pts = [sk.add_point(0, 0), sk.add_point(10, 0), sk.add_point(10, 6), sk.add_point(0, 6)]
    ls = [sk.add_line(pts[i], pts[(i + 1) % 4]) for i in range(4)]
    for lid in (ls[0], ls[2]):
        sk.add_constraint("horizontal", lid)
    for lid in (ls[1], ls[3]):
        sk.add_constraint("vertical", lid)
    sk.add_constraint("distance", ls[0], value=10.0)
    far = sk.add_line(sk.add_point(-40, 20), sk.add_point(60, 20))
    sk.solve()
    return sk.to_dict(), ls, far


def test_a_linear_pattern_copies_horizontal_and_vertical_a_circular_one_does_not_and_no_dimension_is_copied():
    d, ls, far = _rectangle_with_dimension()
    old = {c["id"] for c in d["constraints"]}
    s = _new(d)
    lin = s.linear_pattern({"ids": ls, "line": far, "at": [0, 30], "count": 3, "spacing": 20})
    types = [c["type"] for c in s.sk.constraints if c["id"] not in old]
    assert types.count("horizontal") == 4 and types.count("vertical") == 4 and "distance" not in types
    assert types.count("coincident") >= 0 and len(lin["entities"]) == 8
    s2 = _new(d)
    s2.circular_pattern({"ids": ls, "centre": [30, 30], "count": 3, "angle": 90})
    types2 = [c["type"] for c in s2.sk.constraints if c["id"] not in old]
    assert "horizontal" not in types2 and "vertical" not in types2 and "distance" not in types2
    # the copies carry no tie to the source: moving the source leaves them where they are
    for sess, made in ((s, lin), (s2, None)):
        new_pts = {k: list(v) for k, v in sess.sk.points.items() if k not in d["points"]}
        sess.drag_begin({"entity": ls[0], "at": [5, 0]})
        sess.drag_move({"at": [8, 3]})
        sess.drag_end(False)
        _equal({k: list(sess.sk.points[k]) for k in new_pts}, new_pts)
        assert sess.sk.P(sess.sk.entities[ls[0]]["p1"]) != pytest.approx([0, 0], abs=1e-3)


# ---------------------------------------------------------------------- one undo step each
@pytest.mark.parametrize("kind", ["mirror", "linear_pattern", "circular_pattern"])
def test_each_operation_is_one_undo_step_and_redo_restores_it(kind):
    d, n = _scene()
    s = _new(d)
    before = s.sk.to_dict()
    body = {"mirror": {"ids": _everything(n), "line": n["axis"]},
            "linear_pattern": {"ids": _everything(n), "line": n["axis"], "at": [0, 20], "count": 5, "spacing": 7},
            "circular_pattern": {"ids": _everything(n), "centre": [3, 3], "count": 7}}[kind]
    getattr(s, kind)(body)
    after = s.sk.to_dict()
    assert after != before and len(s.undo_stack) == 1 and s._status["ok"] is not None
    s.undo()
    assert s.sk.to_dict() == before and not s.undo_stack
    s.redo()
    assert s.sk.to_dict() == after and len(s.undo_stack) == 1 and not s.redo_stack


# ---------------------------------------------------------------------- refusals
def _big_chain(count):
    sk = Sketch()
    prev = sk.add_point(0, 0)
    ids = []
    for i in range(count):
        nxt = sk.add_point(i + 1, (i % 3))
        ids.append(sk.add_line(prev, nxt))
        prev = nxt
    return sk.to_dict(), ids


def test_every_refusal_names_the_field_and_changes_nothing():
    d, n = _scene()
    s = _new(d)
    e = _everything(n)
    nan, inf = float("nan"), float("inf")
    cases = [
        ("mirror", {"ids": ["nope"], "line": n["axis"]}, "ids\\[0\\]"),
        ("mirror", {"ids": [n["P"]], "line": n["axis"]}, "ids\\[0\\]"),                  # a point id
        ("mirror", {"ids": [], "line": n["axis"]}, "ids"),                               # an empty selection
        ("mirror", {"line": n["axis"]}, "ids"),
        ("mirror", {"ids": e, "line": n["circle"]}, "line"),                             # not a line
        ("mirror", {"ids": e + [n["axis"]], "line": n["axis"]}, "line"),                 # the line is one of ids
        ("mirror", {"ids": e, "line": "nope"}, "line"),
        ("mirror", {"ids": e}, "line"),
        ("mirror", {"ids": e, "line": n["axis"], "extra": 1}, "extra"),
        ("mirror", {"ids": e, "line": n["axis"], "preview": "yes"}, "preview"),
        ("linear_pattern", {"ids": e, "line": n["axis"], "at": [0, 20], "count": 1}, "count"),
        ("linear_pattern", {"ids": e, "line": n["axis"], "at": [0, 20], "count": MAX_PATTERN_COUNT + 1}, "count"),
        ("linear_pattern", {"ids": e, "line": n["axis"], "at": [0, 20], "count": nan}, "count"),
        ("linear_pattern", {"ids": e, "line": n["axis"], "at": [0, 20], "count": True}, "count"),
        ("linear_pattern", {"ids": e, "line": n["axis"], "at": [0, 20], "count": "x+"}, "count"),
        ("linear_pattern", {"ids": e, "line": n["axis"], "at": [0, 20], "spacing": inf}, "spacing"),
        ("linear_pattern", {"ids": e, "line": n["axis"], "at": [0, 20], "floor": -1}, "floor"),
        ("linear_pattern", {"ids": e, "line": n["axis"], "at": [0]}, "at"),
        ("linear_pattern", {"ids": e, "line": n["axis"]}, "at"),
        ("linear_pattern", {"ids": e + [n["axis"]], "line": n["axis"], "at": [0, 20]}, "line"),
        ("linear_pattern", {"ids": e, "line": n["circle"], "at": [0, 20]}, "line"),
        ("linear_pattern", {"ids": ["nope"], "line": n["axis"], "at": [0, 20]}, "ids\\[0\\]"),
        ("linear_pattern", {"ids": e, "line": n["axis"], "at": [0, 20], "extra": 1}, "extra"),
        ("circular_pattern", {"ids": e, "centre": [0, 0], "count": 1}, "count"),
        ("circular_pattern", {"ids": e, "centre": [0, 0], "count": MAX_PATTERN_COUNT + 1}, "count"),
        ("circular_pattern", {"ids": e, "centre": [0, 0], "angle": nan}, "angle"),
        ("circular_pattern", {"ids": e, "centre": [0, nan]}, "centre"),
        ("circular_pattern", {"ids": e, "centre": {"at": [0, 0], "point": "nope"}}, "centre.point"),
        ("circular_pattern", {"ids": e}, "centre"),
        ("circular_pattern", {"ids": [], "centre": [0, 0]}, "ids"),
        ("circular_pattern", {"ids": e, "centre": [0, 0], "extra": 1}, "extra"),
    ]
    for kind, body, field in cases:
        for preview in (False, True):
            before = _snapshot(s)
            with pytest.raises(SketchError, match=field):
                getattr(s, kind)({**body, "preview": True} if preview and "preview" not in body else body)
            assert _snapshot(s) == before, (kind, body)


def test_a_line_with_no_length_and_copies_that_pass_the_item_limit_are_refused_whole():
    d, ids = _big_chain(12)
    sk = Sketch.from_dict(d)
    sk.add_line(sk.add_point(100, 100), sk.entities[ids[0]]["p1"])
    zero = sk.add_line(sk.add_point(5, 5), sk.add_point(5, 5))
    d = sk.to_dict()
    s = _new(d)
    before = _snapshot(s)
    with pytest.raises(SketchError, match="line: the line has no length"):
        s.mirror({"ids": ids, "line": zero})
    with pytest.raises(SketchError, match="line: the line has no length"):
        s.linear_pattern({"ids": ids, "line": zero, "at": [0, 0]})
    # 12 lines and 13 points a copy: 499 copies pass MAX_ITEMS; 150 do not and 100 do
    far = next(k for k, e in s.sk.entities.items() if e["p1"] != e["p2"] and k not in ids and k != zero)
    for body in ({"ids": ids, "line": far, "at": [0, 0], "count": MAX_PATTERN_COUNT},
                 {"ids": ids, "line": far, "at": [0, 0], "count": 200}):
        for run in (s.linear_pattern, s.circular_pattern):
            b = dict(body, centre=[0, 0]) if run == s.circular_pattern else body
            if run == s.circular_pattern:
                b.pop("line"), b.pop("at")
            with pytest.raises(SketchError, match=f"count: .*{MAX_ITEMS}"):
                run(b)
    assert _snapshot(s) == before
    # the limit itself is exact: what fits is made, one more copy is refused
    fits = (MAX_ITEMS - len(s.sk.points) - len(s.sk.entities)) // (12 + 13)
    s.circular_pattern({"ids": ids, "centre": [0, 0], "count": fits + 1})
    assert len(s.sk.points) + len(s.sk.entities) <= MAX_ITEMS
    s.undo()
    with pytest.raises(SketchError, match="count"):
        s.circular_pattern({"ids": ids, "centre": [0, 0], "count": fits + 2})


def test_any_of_the_three_is_refused_during_a_drag_and_leaves_the_drag_alone():
    d, n = _scene()
    s = _new(d)
    s.drag_begin({"entity": n["circle"], "at": [20, -10]})
    before = _snapshot(s)
    drag = dict(s.drag)
    for kind, body in (("mirror", {"ids": [n["l1"]], "line": n["axis"]}),
                       ("linear_pattern", {"ids": [n["l1"]], "line": n["axis"], "at": [0, 20]}),
                       ("circular_pattern", {"ids": [n["l1"]], "centre": [0, 0]})):
        for extra in ({}, {"preview": True}):
            with pytest.raises(SketchError, match="drag"):
                getattr(s, kind)({**body, **extra})
    assert _snapshot(s) == before and s.drag == drag
    s.drag_end(True)


def test_a_pattern_of_500_instances_is_accepted():
    sk = Sketch()
    c = sk.add_circle(sk.add_point(5, 0), 1.0)
    s = _new(sk.to_dict())
    made = s.circular_pattern({"ids": [c], "centre": [0, 0], "count": MAX_PATTERN_COUNT})
    assert len(made["entities"]) == MAX_PATTERN_COUNT - 1 and len(s.sk.entities) == MAX_PATTERN_COUNT


# ---------------------------------------------------------------------- preview
def test_a_preview_answers_made_and_outlines_and_changes_nothing_and_the_real_call_matches_it():
    d, n = _scene()
    e = _everything(n)
    bodies = {"mirror": {"ids": e, "line": n["axis"]},
              "linear_pattern": {"ids": e, "line": n["axis"], "at": [0, 20], "count": 3},
              "circular_pattern": {"ids": e, "centre": [3, -4], "count": 5, "angle": 200}}
    for kind, body in bodies.items():
        s = _new(d)
        s.undo_stack.append({"points": {}, "entities": {}, "constraints": []})      # something to compare
        view, undo, redo = s.view(), list(s.undo_stack), list(s.redo_stack)
        got = getattr(s, kind)({**body, "preview": True})
        assert set(got) == {"made", "outlines"}
        assert s.view() == view and s.undo_stack == undo and s.redo_stack == redo
        assert s.sk.to_dict() == d
        real = getattr(s, kind)(body)
        assert real == got["made"], "a preview names the entities the real call makes"
        want = [[[float(x), float(y)] for x, y in line] for eid in real["entities"] for line in s.sk.polylines(eid, 24)]
        _equal(got["outlines"], want)
        assert len(got["outlines"]) == len(real["entities"])


# ---------------------------------------------------------------------- expressions and ids
def test_count_and_spacing_evaluate_expressions_and_none_is_stored():
    d, n = _scene()
    s = _new(d, variables={"gap": 12.0})
    made = s.linear_pattern({"ids": [n["l1"]], "line": n["axis"], "at": [0, 20], "count": "2*2", "spacing": "#gap"})
    assert made["count"] == 4 and made["spacing"] == 12.0 and len(made["entities"]) == 3
    c = _new(d, variables={"gap": 12.0})
    got = c.circular_pattern({"ids": [n["l1"]], "centre": [0, 0], "count": "#gap/4", "angle": "90"})
    assert got["count"] == 3 and got["angle"] == 90.0
    for sess in (s, c):
        assert "expr" not in json.dumps(sess.sk.to_dict())
    with pytest.raises(SketchError, match="spacing"):
        s.linear_pattern({"ids": [n["l1"]], "line": n["axis"], "at": [0, 20], "spacing": "#nope"})


def test_new_ids_are_the_next_free_numbers_and_do_not_depend_on_what_was_deleted_before():
    d, n = _scene()
    a, b = _new(d), _new(d)
    junk = a.add({"shape": "line", "picks": [[50, 50], [60, 60]]})["entities"]
    a.delete({"ids": junk})                           # a's counter is now ahead of b's
    ma = a.mirror({"ids": _everything(n), "line": n["axis"]})["entities"]
    mb = b.mirror({"ids": _everything(n), "line": n["axis"]})["entities"]
    assert ma == mb
    top = max(int("".join(ch for ch in k if ch.isdigit()) or 0)
              for k in list(d["points"]) + list(d["entities"]) + [c["id"] for c in d["constraints"]])
    assert min(int(m[1:]) for m in mb) > top and not set(mb) & set(d["entities"])   # numbers after the highest used


# ---------------------------------------------------------------------- over the socket
def test_the_three_messages_over_the_socket_answer_step_preview_and_refuse(root):
    r = root()
    _open_doc(r)

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await _connect(s, r)
            op = await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "plane": TOP})
            assert op["type"] == "sketch.opened"
            axis = await _ask(ws, {"type": "sketch.add", "shape": "line", "picks": [[0, -30], [0, 30]],
                                   "construction": True})
            aid = axis["made"]["entities"][0]
            for p in (axis["made"]["points"][0], axis["made"]["points"][1]):
                assert (await _ask(ws, {"type": "sketch.constrain", "tool": "fix", "picks": [p]}))["type"] \
                    == "sketch.constrained"
            circ = await _ask(ws, {"type": "sketch.add", "shape": "circle", "picks": [[20, 0]], "radius": 4})
            cid = circ["made"]["entities"][0]
            base = (await _ask(ws, {"type": "sketch.state"}))["sketch"]
            _skipped[id(ws)] = []

            msgs = [("sketch.mirror", {"ids": [cid], "line": aid}),
                    ("sketch.linear_pattern", {"ids": [cid], "line": aid, "at": [0, 20], "count": 3, "spacing": 12}),
                    ("sketch.circular_pattern", {"ids": [cid], "centre": [0, 0], "count": 4})]
            for kind, body in msgs:
                state = await _ask(ws, {"type": "sketch.state"})
                pre = await _ask(ws, {"type": kind, **body, "preview": True})
                assert pre["type"] == "sketch.previewed" and pre["for"] == kind
                assert pre["outlines"] and pre["made"]["entities"]
                after = await _ask(ws, {"type": "sketch.state"})
                for k in ("sketch", "status", "can_undo", "can_redo"):
                    assert after[k] == state[k], k
                await _alive(ws)
                assert _skipped[id(ws)] == [], "a preview is answered to the sender only: nothing else is pushed"
                got = await _ask(ws, {"type": kind, **body})
                assert got["type"] == "sketch.changed" and got["made"]["entities"] == pre["made"]["entities"]
                assert got["can_undo"] is True
                und = await _ask(ws, {"type": "sketch.undo"})
                assert und["sketch"] == state["sketch"] and und["can_redo"] is True
                red = await _ask(ws, {"type": "sketch.redo"})
                assert red["sketch"] == got["sketch"]
                await _ask(ws, {"type": "sketch.undo"})
            assert (await _ask(ws, {"type": "sketch.state"}))["sketch"] == base
            # a bad message answers error and the socket stays open
            for bad in ({"type": "sketch.mirror", "line": aid}, {"type": "sketch.mirror", "ids": [cid], "line": cid},
                        {"type": "sketch.linear_pattern", "ids": [cid], "line": aid, "at": [0, 20], "count": 501},
                        {"type": "sketch.circular_pattern", "ids": [cid], "centre": [0, 0], "wrong": 1}):
                got = await _ask(ws, bad)
                assert got["type"] == "error" and got["for"] == bad["type"] and got["message"]
                await _alive(ws)
            assert (await _ask(ws, {"type": "sketch.state"}))["sketch"] == base
            # mirror for real, then commit: the stored sketch holds the symmetric constraints
            got = await _ask(ws, {"type": "sketch.mirror", "ids": [cid], "line": aid})
            assert got["type"] == "sketch.changed"
            done = await _ask(ws, {"type": "sketch.commit"})
            assert done["type"] == "sketch.committed"
            out = await _outputs(s, r)
            sk = next(x for x in out.sketches if x.id == done["feature"])
            assert [c["type"] for c in sk.sketch["constraints"]].count("symmetric") == 1
            assert [c["type"] for c in sk.sketch["constraints"]].count("equal") == 1
            await ws.close()
    _run(go())
