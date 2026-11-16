"""I7.6: dimensions, trim, corner fillet and use edge on the server (SketchSession.dimension, dimension_set, trim,
fillet, use_edge, and the `sketch.dimension`, `sketch.dimension.set`, `sketch.trim`, `sketch.fillet`, `sketch.use_edge`
messages; shapes in the serve.py docstring, "Sketch sessions"). The desktop's own Sketcher is the reference for trim
and use edge. Each test builds what it needs, so each passes alone."""
import asyncio
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aiohttp  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.engine import InProcessEngine  # noqa: E402
from freeshape.engine import process as P  # noqa: E402
from freeshape.sketch import ORIGIN, Sketch  # noqa: E402
from freeshape.sketch_session import SketchError, SketchSession  # noqa: E402
from test_sketch_inference import _sketcher  # noqa: E402
from test_sketch_session import (A, BRACKET_FILE, TOP, _ask, _connect, _open_doc, _run, root)  # noqa: E402,F401
from test_sketch_worker_requests import _desktop_use, _local  # noqa: E402


def _new(sketch=None, variables=None):
    s = SketchSession("D1", "T1", None, TOP, sketch, name="Sketch 1")
    s.variables = dict(variables or {})
    return s


def _pin(s, *pts):
    """Fix each point where it is, so a dimension has something to move against."""
    for p in pts:
        s.sk.add_constraint("fix", p, value=list(s.sk.P(p)))
    s.sk.solve()


def _line(s, a, b):
    got = s.add({"shape": "line", "picks": [list(a), list(b)]})
    e = s.sk.entities[got["entities"][0]]
    return got["entities"][0], e["p1"], e["p2"]


def _snapshot(s):
    return s.sk.to_dict(), s._status, list(s.undo_stack), list(s.redo_stack)


# ---------------------------------------------------------------------- dimensions


def test_a_distance_dimension_set_to_an_expression_reads_20_and_the_geometry_follows():
    s = _new()
    lid, p1, p2 = _line(s, (0, 0), (10, 0))
    _pin(s, p1)
    s.sk.add_constraint("horizontal", lid)
    made = s.dimension({"picks": [lid], "pos": [5, 4]})
    assert made["type"] == "distance" and made["value"] == pytest.approx(10.0)
    s.dimension_set({"constraint": made["constraint"], "value": "2*10"})
    c = next(c for c in s.sk.constraints if c["id"] == made["constraint"])
    assert c["value"] == 20.0 and "expr" not in c            # no variable in it: stored as the number
    assert s.sk.P(p2) == pytest.approx([20.0, 0.0], abs=1e-6)
    assert s.sk.dim_pos(c) is not None and s._status["ok"]


def test_a_dimension_that_names_a_variable_keeps_its_text_and_a_bad_name_is_refused():
    s = _new(variables={"w": 8.0})
    lid, p1, p2 = _line(s, (0, 0), (10, 0))
    _pin(s, p1)
    s.sk.add_constraint("horizontal", lid)
    made = s.dimension({"picks": [lid], "value": "#w*2"})
    assert made["value"] == pytest.approx(16.0) and made["expr"] == "#w*2"
    assert s.sk.P(p2) == pytest.approx([16.0, 0.0], abs=1e-6)
    before = _snapshot(s)
    with pytest.raises(SketchError, match="dimension.set.value"):
        s.dimension_set({"constraint": made["constraint"], "value": "#nope"})
    assert _snapshot(s) == before


def test_radius_diameter_and_angle_dimensions_each_drive_their_geometry():
    s = _new()
    circ = s.add({"shape": "circle", "picks": [[0, 0]], "radius": 5.0})["entities"][0]
    arc = s.add({"shape": "arc3", "picks": [[30, 0], [50, 0], [40, 10]]})["entities"][0]
    d = s.dimension({"picks": [circ], "value": 14})
    assert d["type"] == "diameter" and s.sk.radius(circ) == pytest.approx(7.0, abs=1e-6)
    r = s.dimension({"picks": [arc], "value": "3*3"})
    assert r["type"] == "radius" and s.sk.radius(arc) == pytest.approx(9.0, abs=1e-6)
    l1, a1, b1 = _line(s, (100, 0), (120, 0))
    l2, a2, b2 = _line(s, (100, 0), (120, 10))
    s.sk.merge_points(a1, a2)
    s.sk.solve()
    _pin(s, a1)
    s.sk.add_constraint("horizontal", l1)
    ang = s.dimension({"picks": [l1, l2], "value": 60})
    assert ang["type"] == "angle"
    u = s.sk.P(s.sk.entities[l2]["p2"]) - s.sk.P(s.sk.entities[l2]["p1"])
    v = s.sk.P(s.sk.entities[l1]["p2"]) - s.sk.P(s.sk.entities[l1]["p1"])
    got = math.degrees(math.acos(float(u @ v) / (np.linalg.norm(u) * np.linalg.norm(v))))
    assert got == pytest.approx(60.0, abs=1e-4)
    s.dimension_set({"constraint": ang["constraint"], "value": "45 + 15 - 30"})
    assert next(c for c in s.sk.constraints if c["id"] == ang["constraint"])["value"] == pytest.approx(30.0)


def test_two_points_take_a_horizontal_or_vertical_distance_from_where_the_label_sits_or_from_kind():
    s = _new()
    a = s.add({"shape": "point", "picks": [[0, 0]]})["points"][0]
    b = s.add({"shape": "point", "picks": [[30, 40]]})["points"][0]
    assert s.dimension({"picks": [a, b], "pos": [15, 60], "value": 30})["type"] == "hdistance"
    s2 = _new()
    a = s2.add({"shape": "point", "picks": [[0, 0]]})["points"][0]
    b = s2.add({"shape": "point", "picks": [[30, 40]]})["points"][0]
    assert s2.dimension({"picks": [a, b], "kind": "vdistance"})["type"] == "vdistance"
    assert s2.dimension({"picks": [a, b]})["type"] == "distance"
    with pytest.raises(SketchError, match="dimension.kind"):
        s2.dimension({"picks": [a, b], "kind": "radius"})


def test_a_dimension_that_over_defines_or_cannot_be_met_changes_nothing():
    s = _new()
    lid, p1, p2 = _line(s, (0, 0), (10, 0))
    _pin(s, p1, p2)                                           # both ends fixed: a length dimension adds nothing
    before = _snapshot(s)
    with pytest.raises(SketchError, match="over-define"):
        s.dimension({"picks": [lid]})
    assert _snapshot(s) == before
    s = _new()
    lid, p1, p2 = _line(s, (0, 0), (10, 0))
    _pin(s, p1)
    s.sk.add_constraint("horizontal", lid)
    made = s.dimension({"picks": [lid]})
    s.sk.add_constraint("distance", p1, p2, value=10.0)       # a second, equal one: the set below must fit both
    before = _snapshot(s)
    with pytest.raises(SketchError, match="value: can't make that 25"):
        s.dimension_set({"constraint": made["constraint"], "value": 25})
    assert _snapshot(s) == before
    for bad in (0, -3, "x+", True):
        with pytest.raises(SketchError, match="dimension.set.value"):
            s.dimension_set({"constraint": made["constraint"], "value": bad})
    with pytest.raises(SketchError, match="constraint"):
        s.dimension_set({"constraint": "nope", "value": 3})
    with pytest.raises(SketchError, match="give a value or a pos"):
        s.dimension_set({"constraint": made["constraint"]})
    assert _snapshot(s) == before


def test_dimension_and_set_are_one_undo_step_each_and_refused_during_a_drag():
    s = _new()
    lid, p1, p2 = _line(s, (0, 0), (10, 0))
    _pin(s, p1)
    s.sk.add_constraint("horizontal", lid)
    steps = len(s.undo_stack)
    made = s.dimension({"picks": [lid], "value": 12})
    assert len(s.undo_stack) == steps + 1
    s.dimension_set({"constraint": made["constraint"], "value": 15})
    assert len(s.undo_stack) == steps + 2
    s.undo()
    assert next(c for c in s.sk.constraints if c["id"] == made["constraint"])["value"] == 12.0
    s.undo()
    assert not any(c["id"] == made["constraint"] for c in s.sk.constraints)
    s.drag_begin({"point": p2, "at": [10, 0]})
    with pytest.raises(SketchError, match="drag"):
        s.dimension({"picks": [lid]})
    with pytest.raises(SketchError, match="drag"):
        s.fillet({"point": p1, "radius": 1})
    with pytest.raises(SketchError, match="drag"):
        s.trim({"entity": lid, "at": [5, 0]})


# ---------------------------------------------------------------------- trim


def _crossed_line():
    """A long line from (0,0) to (100,0) crossed by verticals at x=30 and x=70."""
    sk = Sketch()
    pa, pb = sk.add_point(0, 0), sk.add_point(100, 0)
    mid = sk.add_line(pa, pb)
    for x in (30.0, 70.0):
        sk.add_line(sk.add_point(x, -20), sk.add_point(x, 20))
    return sk, mid


def _segments(sk, skip=()):
    out = []
    for eid, e in sk.entities.items():
        if e["type"] == "line" and eid not in skip:
            a, b = sk.P(e["p1"]), sk.P(e["p2"])
            out.append(tuple(sorted([tuple(np.round(a, 6)), tuple(np.round(b, 6))])))
    return sorted(out)


def test_trimming_the_middle_of_a_line_crossed_by_two_others_leaves_two_segments_as_the_desktop_does():
    sk, mid = _crossed_line()
    desk = _sketcher(Sketch.from_dict(sk.to_dict()))
    desk._trim(mid, np.array([50.0, 0.0]))
    s = _new(sk.to_dict())
    got = s.trim({"entity": mid, "at": [50, 0]})
    assert got["removed"] == mid and len(got["made"]) == 2
    mine = _segments(s.sk)
    assert mine == _segments(desk.sk)
    horizontal = [m for m in mine if m[0][1] == m[1][1] == 0.0]
    assert horizontal == [((0.0, 0.0), (30.0, 0.0)), ((70.0, 0.0), (100.0, 0.0))]
    assert mid not in s.sk.entities and len(s.undo_stack) == 1
    s.undo()
    assert mid in s.sk.entities


def test_trimming_an_end_piece_or_a_whole_circle_matches_the_desktop():
    sk, mid = _crossed_line()
    sk.add_circle(sk.add_point(200, 0), 10.0)
    circ = next(k for k, e in sk.entities.items() if e["type"] == "circle")
    for eid, uv in ((mid, (10.0, 0.0)), (mid, (90.0, 0.0)), (circ, (210.0, 0.0))):
        desk = _sketcher(Sketch.from_dict(sk.to_dict()))
        desk._trim(eid, np.array(uv))
        s = _new(sk.to_dict())
        s.trim({"entity": eid, "at": list(uv)})
        assert _segments(s.sk) == _segments(desk.sk)
        assert sorted(s.sk.entities) == sorted(desk.sk.entities)
        assert sorted(s.sk.points) == sorted(desk.sk.points)


def test_trim_refuses_what_is_not_an_entity_and_a_bad_position():
    sk, mid = _crossed_line()
    s = _new(sk.to_dict())
    before = _snapshot(s)
    for body, field in (({"entity": "nope", "at": [1, 0]}, "trim.entity"), ({"entity": ORIGIN, "at": [1, 0]}, "trim.entity"),
                        ({"entity": mid, "at": [1]}, "trim.at"), ({"entity": mid, "at": [1, "x"]}, "trim.at"),
                        ({"entity": mid, "at": [1, 0], "extra": 1}, "extra")):
        with pytest.raises(SketchError, match=field):
            s.trim(body)
    assert _snapshot(s) == before


# ---------------------------------------------------------------------- corner fillet


def test_a_corner_fillet_of_3_between_two_lines_adds_an_arc_tangent_to_both():
    s = _new()
    corner = s.add({"shape": "point", "picks": [[0, 0]]})["points"][0]
    l1 = s.add({"shape": "line", "picks": [{"at": [0, 0], "point": corner}, [40, 0]]})["entities"][0]
    l2 = s.add({"shape": "line", "picks": [{"at": [0, 0], "point": corner}, [0, 40]]})["entities"][0]
    made = s.fillet({"point": corner, "radius": 3})
    arc = made["arc"]
    sk = s.sk
    assert sk.entities[arc]["type"] == "arc" and sk.radius(arc) == pytest.approx(3.0, abs=1e-6)
    c = sk.P(sk.entities[arc]["c"])
    for lid in (l1, l2):
        a, b = sk.P(sk.entities[lid]["p1"]), sk.P(sk.entities[lid]["p2"])
        d = b - a
        dist = abs(d[0] * (c - a)[1] - d[1] * (c - a)[0]) / np.linalg.norm(d)
        assert dist == pytest.approx(3.0, abs=1e-6)             # tangent: the centre is r from each line
    assert {"tangent"} <= {k["type"] for k in sk.constraints}
    assert sum(1 for k in sk.constraints if k["type"] == "tangent" and arc in k["refs"]) == 2
    rc = next(k for k in sk.constraints if k["id"] == made["constraint"])
    assert rc["type"] == "radius" and rc["value"] == 3.0
    assert len(s.undo_stack) == 4 and s._status["ok"]
    s.dimension_set({"constraint": made["constraint"], "value": "2*2"})
    assert sk.radius(arc) == pytest.approx(4.0, abs=1e-6)


def test_a_fillet_with_a_variable_keeps_the_expression_on_the_radius_and_bad_input_changes_nothing():
    s = _new(variables={"r": 2.5})
    corner = s.add({"shape": "point", "picks": [[0, 0]]})["points"][0]
    for end in ([40, 0], [0, 40]):
        s.add({"shape": "line", "picks": [{"at": [0, 0], "point": corner}, end]})
    before = _snapshot(s)
    for body, match in (({"point": corner, "radius": 0}, "fillet.radius"),
                        ({"point": corner, "radius": 500}, "too large"),
                        ({"point": "nope", "radius": 3}, "fillet.point"),
                        ({"point": ORIGIN, "radius": 1}, "Click a corner"),
                        ({"point": corner, "radius": "#zz"}, "fillet.radius")):
        with pytest.raises(SketchError, match=match):
            s.fillet(body)
    assert _snapshot(s) == before
    made = s.fillet({"point": corner, "radius": "#r"})
    rc = next(k for k in s.sk.constraints if k["id"] == made["constraint"])
    assert rc["value"] == 2.5 and rc["expr"] == "#r"


# ---------------------------------------------------------------------- use edge


def _engine_answer(pick, plane=TOP):
    eng = InProcessEngine()
    try:
        tab = eng.open_tab(BRACKET_FILE)
        return eng.project_edge(tab, plane, pick), eng.sketch_frame(tab, plane)
    finally:
        eng.close()
        P.SPARES.clear()


def _a_line_edge():
    eng = InProcessEngine()
    try:
        tab = eng.open_tab(BRACKET_FILE)
        for e in eng.sketch_frame(tab, TOP)["edges"]:
            if e["height"] < 1e-9 and len(e["points"]) == 2 and math.dist(*e["points"]) > 5:
                return e["pick"]
    finally:
        eng.close()
        P.SPARES.clear()
    pytest.fail("the bracket has no straight edge lying in the top plane")


def test_using_a_model_edge_adds_a_construction_copy_tied_to_the_edge_reference_as_the_desktop_does():
    pick = _a_line_edge()
    got, _ = _engine_answer(pick)
    assert got["kind"] == "line" and got["ref"] is not None
    dsk, deid = _desktop_use(BRACKET_FILE, pick)
    s = _new()
    steps = len(s.undo_stack)
    out = s.use_edge({"pick": pick}, got)
    e = s.sk.entities[out["entity"]]
    d = dsk.entities[deid]
    assert e["type"] == "line" and e["construction"] is True and out["reused"] is False
    assert e["src"] == got["ref"] == d["src"]
    assert np.allclose([s.sk.P(e["p1"]), s.sk.P(e["p2"])], [dsk.P(d["p1"]), dsk.P(d["p2"])], atol=1e-12)
    assert out["points"] == [e["p1"], e["p2"]]
    for p in (e["p1"], e["p2"]):                              # fixed, as the desktop's copy is
        assert any(c["type"] == "fix" and c["refs"][0] == p for c in s.sk.constraints)
    assert len(s.undo_stack) == steps + 1
    # the same edge again adds nothing; asked as solid it flips the copy and shows it
    before = _snapshot(s)
    again = s.use_edge({"pick": pick}, got)
    assert again["entity"] == out["entity"] and again["reused"] is True and _snapshot(s) == before
    s.use_edge({"pick": pick, "construction": False}, got)
    assert s.sk.entities[out["entity"]]["construction"] is False and len(s.undo_stack) == steps + 2
    assert len(s.sk.entities) == 1


def test_use_edge_checks_its_message_before_the_engine_is_asked():
    s = _new()
    for body, field in (({}, "use_edge"), ({"pick": {"part": "p", "index": 0}}, "use_edge.pick.index"),
                        ({"pick": {"part": 5, "index": 1}}, "use_edge.pick.part"),
                        ({"pick": {"part": "p", "index": 1, "kind": "face"}}, "use_edge.pick.kind"),
                        ({"pick": {"part": "p", "index": 1, "x": 1}}, "use_edge.pick.x"),
                        ({"pick": {"part": "p", "index": 1}, "construction": "yes"}, "use_edge.construction"),
                        ({"pick": {"part": "p", "index": 1}, "tool": 1}, "tool")):
        with pytest.raises(SketchError, match=field):
            s.use_edge_pick(body)
    assert s.use_edge_pick({"pick": {"part": "p", "index": 3}}) == \
        {"part": "p", "kind": "edge", "index": 3, "want": ["edge"]}
    before = _snapshot(s)
    with pytest.raises(SketchError, match="use_edge.pick"):
        s.use_edge({"pick": {"part": "p", "index": 1}}, {"kind": "curve", "entity": {}, "ref": None})
    assert _snapshot(s) == before


# ---------------------------------------------------------------------- over the socket


def test_the_new_messages_over_the_socket_make_one_undo_step_each_and_never_close_it(root):
    r = root()
    _open_doc(r)
    pick = _a_line_edge()

    async def go():
        async with aiohttp.ClientSession() as http:
            ws = await _connect(http, r)
            op = await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "plane": TOP})
            assert op["type"] == "sketch.opened"
            used = await _ask(ws, {"type": "sketch.use_edge", "pick": pick})
            assert used["type"] == "sketch.changed" and used["made"]["reused"] is False
            eid = used["made"]["entity"]
            ent = used["sketch"]["entities"][eid]
            assert ent["construction"] is True and ent["src"]["name"]
            line = await _ask(ws, {"type": "sketch.add", "shape": "line", "picks": [[1000, 1000], [1010, 1000]]})
            lid = line["made"]["entities"][0]
            p1 = line["sketch"]["entities"][lid]["p1"]
            fix = await _ask(ws, {"type": "sketch.constrain", "tool": "fix", "picks": [p1]})
            assert fix["accepted"] is True
            hor = await _ask(ws, {"type": "sketch.constrain", "tool": "horizontal", "picks": [lid]})
            assert hor["accepted"] is True
            dim = await _ask(ws, {"type": "sketch.dimension", "picks": [lid], "pos": [1005, 1004], "value": "2*5"})
            assert dim["type"] == "sketch.changed" and dim["made"]["type"] == "distance"
            assert dim["made"]["value"] == 10.0
            cid = dim["made"]["constraint"]
            setv = await _ask(ws, {"type": "sketch.dimension.set", "constraint": cid, "value": "2*10"})
            assert setv["made"] == {"constraint": cid, "value": 20.0}
            p2 = setv["sketch"]["entities"][lid]["p2"]
            assert setv["sketch"]["points"][p2][0] == pytest.approx(1020.0, abs=1e-5)
            assert setv["can_undo"] is True
            clash = await _ask(ws, {"type": "sketch.dimension.set", "constraint": cid, "value": -1})
            assert clash["type"] == "error" and clash["for"] == "sketch.dimension.set" and "value" in clash["message"]
            trim = await _ask(ws, {"type": "sketch.trim", "entity": lid, "at": [1005, 1000]})
            assert trim["type"] == "sketch.changed" and trim["made"]["removed"] == lid
            for bad in ({"type": "sketch.use_edge", "pick": {"part": "nope", "index": 1}},
                        {"type": "sketch.use_edge", "pick": pick, "kind": "x"},
                        {"type": "sketch.fillet", "point": ORIGIN, "radius": 1},
                        {"type": "sketch.dimension", "picks": []},
                        {"type": "sketch.trim", "entity": "zz", "at": [0, 0]}):
                got = await _ask(ws, bad)
                assert got["type"] == "error" and got["message"] != "internal error", got
            state = await _ask(ws, {"type": "sketch.state"})
            assert state["type"] == "sketch.state"
            await ws.close()
    _run(go(), 300)
