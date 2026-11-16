"""I7.4: dragging in a sketch on the server (SketchSession.drag_*, the `sketch.drag.*` messages in freeshape/serve.py,
"Dragging in a sketch"). Each test builds what it needs, so each passes alone."""
import json

import aiohttp
import pytest

from freeshape.sketch_session import SketchError, SketchSession
from tests.test_sketch_session import A, TOP, _ask, _connect, _next, _open_doc, _run, root  # noqa: F401


def _rect(dimensions=("h", "v")):
    """A rectangle 30 x 20 with its first corner fixed at (10, 10); `dimensions` say which sizes are given."""
    cons = [{"type": "fix", "refs": ["$p0"]}]
    if "h" in dimensions:
        cons.append({"type": "hdistance", "refs": ["$e0"], "value": 30})
    if "v" in dimensions:
        cons.append({"type": "vdistance", "refs": ["$e1"], "value": 20})
    s = SketchSession("D1", "T1", None, TOP, None, name="Sketch 1")
    made = s.add({"shape": "rect", "picks": [[10.0, 10.0], [40.0, 30.0]], "constraints": cons})
    return s, made["points"][1]                    # the opposite corner, at (40, 30)


def _pos(s, pid):
    return [float(x) for x in s.sk.points[pid]]


def test_a_corner_of_a_fully_dimensioned_rectangle_cannot_move():
    s, corner = _rect()
    s.drag_begin({"point": corner, "at": [40, 30]})
    for at in ([45, 37], [60, 40], [41, 29]):
        out = s.drag_move({"at": at, "t": 1})
        assert out["points"][corner] == pytest.approx([40.0, 30.0], abs=1e-3)
    s.drag_end()
    assert _pos(s, corner) == pytest.approx([40.0, 30.0], abs=1e-3)


def test_without_the_width_dimension_a_corner_moves_along_the_free_direction_only():
    s, corner = _rect(("v",))
    s.drag_begin({"point": corner, "at": [40, 30]})
    out = s.drag_move({"at": [48, 37]})
    assert out["held"] is False
    assert out["points"][corner] == pytest.approx([48.0, 30.0], abs=1e-2)       # x follows, y does not
    assert out["solve_ms"] > 0
    s.drag_end()
    assert _pos(s, corner) == pytest.approx([48.0, 30.0], abs=1e-2)


def test_a_drag_is_one_undo_step_and_other_steps_wait_for_it():
    s, corner = _rect(("v",))
    depth = len(s.undo_stack)
    s.drag_begin({"point": corner, "at": [40, 30]})
    for x in range(41, 50):
        s.drag_move({"at": [x, 30]})
    with pytest.raises(SketchError, match="drag is in progress"):
        s.add({"shape": "point", "picks": [[1.0, 1.0]]})
    with pytest.raises(SketchError, match="drag is in progress"):
        s.undo()
    assert _pos(s, corner) == pytest.approx([40.0, 30.0])               # the sketch is unchanged until the end
    s.drag_end()
    assert len(s.undo_stack) == depth + 1 and _pos(s, corner) == pytest.approx([49.0, 30.0], abs=1e-2)
    s.undo()
    assert _pos(s, corner) == pytest.approx([40.0, 30.0]) and len(s.undo_stack) == depth


def test_a_drag_that_never_moved_or_was_held_adds_no_step_and_cancel_drops_the_result():
    s, corner = _rect()
    depth = len(s.undo_stack)
    s.drag_begin({"point": corner, "at": [40, 30]})
    s.drag_end()
    s.drag_begin({"point": corner, "at": [40, 30]})
    s.drag_move({"at": [60, 40]})
    s.drag_end()
    assert len(s.undo_stack) == depth
    s, corner = _rect(("v",))
    s.drag_begin({"point": corner, "at": [40, 30]})
    s.drag_move({"at": [55, 30]})
    s.drag_end(cancel=True)
    assert _pos(s, corner) == pytest.approx([40.0, 30.0])


def test_bad_drag_messages_are_errors_that_change_nothing():
    s, corner = _rect()
    for body in ({}, {"point": corner}, {"point": "nope", "at": [0, 0]}, {"point": "origin", "at": [0, 0]},
                 {"point": corner, "entity": "l1", "at": [0, 0]}, {"point": corner, "at": [0, "x"]}):
        with pytest.raises(SketchError):
            s.drag_begin(body)
    with pytest.raises(SketchError):
        s.drag_move({"at": [1, 1]})
    s.drag_begin({"point": corner, "at": [40, 30]})
    with pytest.raises(SketchError):
        s.drag_begin({"point": corner, "at": [40, 30]})
    with pytest.raises(SketchError):
        s.drag_move({"at": [1, 1], "t": "now"})


def test_fifty_moves_sent_without_waiting_make_fewer_solves_and_the_last_one_wins(root):
    r = root()
    _open_doc(r)

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await _connect(s, r)
            await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "plane": TOP})
            got = await _ask(ws, {"type": "sketch.add", "shape": "rect", "picks": [[10.0, 10.0], [40.0, 30.0]],
                                  "constraints": [{"type": "fix", "refs": ["$p0"]},
                                                  {"type": "vdistance", "refs": ["$e1"], "value": 20}]})
            corner = got["made"]["points"][1]
            assert (await _ask(ws, {"type": "sketch.drag.begin", "point": corner, "at": [40, 30]}))["type"] \
                == "sketch.drag.begun"
            for i in range(50):                                     # no waiting for an answer
                await ws.send_str(json.dumps({"type": "sketch.drag.move", "id": 1000 + i, "at": [41 + i, 30],
                                              "t": 5000.0 + i}))
            end = await _ask(ws, {"type": "sketch.drag.end"})
            replies = []
            while True:                                             # every answer to a move came before the end's
                try:
                    replies.append(await _next(ws, "sketch.drag.moved", timeout=0.5))
                except Exception:                                   # noqa: BLE001
                    break
            assert 1 <= len(replies) < 50
            assert replies[-1]["t"] == 5049.0 and replies[-1]["id"] == 1049
            assert [m["t"] for m in replies] == sorted(m["t"] for m in replies)
            assert all(m["solve_ms"] > 0 for m in replies)
            assert replies[-1]["points"][corner] == pytest.approx([90.0, 30.0], abs=1e-2)
            assert end["type"] == "sketch.changed" and end["sketch"]["points"][corner] == \
                pytest.approx([90.0, 30.0], abs=1e-2)
            assert end["can_undo"] is True
            await ws.close()
    _run(go())


def test_each_reply_echoes_the_client_time_and_carries_a_solve_time(root):
    r = root()
    _open_doc(r)

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await _connect(s, r)
            await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "plane": TOP})
            got = await _ask(ws, {"type": "sketch.add", "shape": "circle", "picks": [[0.0, 0.0]], "radius": 5})
            circle = got["made"]["entities"][0]
            await _ask(ws, {"type": "sketch.drag.begin", "entity": circle, "at": [5, 0]})
            out = await _ask(ws, {"type": "sketch.drag.move", "at": [9, 0], "t": 123.5})
            assert out["type"] == "sketch.drag.moved" and out["t"] == 123.5 and out["solve_ms"] > 0
            assert out["radii"][circle] == pytest.approx(9.0, abs=1e-3)
            bad = await _ask(ws, {"type": "sketch.drag.move", "at": [1], "t": 1})
            assert bad["type"] == "error" and "drag.at" in bad["message"]
            end = await _ask(ws, {"type": "sketch.drag.end"})
            assert end["type"] == "sketch.changed"
            none = await _ask(ws, {"type": "sketch.drag.move", "at": [1, 1]})
            assert none["type"] == "error" and "no drag" in none["message"]
            await ws.close()
    _run(go())
