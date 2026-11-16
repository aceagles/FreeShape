"""I7.5: constraint tools on the server (SketchSession.constrain / unconstrain / fits, the `sketch.constrain`,
`sketch.unconstrain` and `sketch.fits` messages) through freeshape.sketch_ops.constraints_for, the mapping the
desktop's Sketcher uses too. Each test builds what it needs, so each passes alone."""
import aiohttp
import pytest

from freeshape import sketch_ops as OPS
from freeshape.sketch import Sketch
from freeshape.sketch_session import SketchError, SketchSession
from tests.test_sketch_session import TOP, _ask, _connect, _open_doc, _run, root  # noqa: F401


def _world():
    """A sketch with one of everything. Returns (Sketch, names): points a..e, lines slant / flat / up, circles c1
    (r 10 at the origin), c2 (r 4 at (30, 0)), c3 (r 3 at (2, 0), inside c1), arc."""
    sk = Sketch()
    n = {}
    n["a"], n["b"] = sk.add_point(0, 50), sk.add_point(40, 70)
    n["slant"] = sk.add_line(n["a"], n["b"])
    n["c"], n["d"] = sk.add_point(0, 90), sk.add_point(40, 90)
    n["flat"] = sk.add_line(n["c"], n["d"])
    n["e"], n["f"] = sk.add_point(60, 50), sk.add_point(61, 80)
    n["up"] = sk.add_line(n["e"], n["f"])
    n["m"] = sk.add_point(70, 70)
    cc = {k: sk.add_point(*xy) for k, xy in {"c1": (0, 0), "c2": (30, 0), "c3": (2, 0), "c4": (0, -40)}.items()}
    n["c1"] = sk.add_circle(cc["c1"], 10)
    n["c2"] = sk.add_circle(cc["c2"], 4)
    n["c3"] = sk.add_circle(cc["c3"], 3)
    s1, s2 = sk.add_point(0, -30), sk.add_point(10, -40)
    n["arc"] = sk.add_arc(cc["c4"], s1, s2)
    n["pt"] = sk.add_point_entity(n["m"])
    return sk, n


def _session(sk):
    return SketchSession("D1", "T1", None, TOP, sk.to_dict(), name="Sketch 1")


def _types(s):
    return sorted(c["type"] for c in s.sk.constraints)


def test_horizontal_makes_a_slanted_line_horizontal_and_a_conflicting_one_is_reported_and_changes_nothing():
    sk, n = _world()
    s = _session(sk)
    a, b = s.sk.entities[n["slant"]]["p1"], s.sk.entities[n["slant"]]["p2"]
    out = s.constrain({"tool": "horizontal", "picks": [n["slant"]]})
    assert out["accepted"] is True and len(out["made"]) == 1 and out["conflicts"] == []
    assert s.sk.points[a][1] == pytest.approx(s.sk.points[b][1], abs=1e-6)
    assert s._status["ok"] and len(s.undo_stack) == 1
    # fix both ends, then ask for vertical: the line cannot be vertical any more
    s.constrain({"tool": "fix", "picks": [a]})
    s.constrain({"tool": "fix", "picks": [b]})
    depth = len(s.undo_stack)
    before = s.view()
    s.redo_stack.append(s.sk.to_dict())               # a pending redo must survive a refused step
    redo = list(s.redo_stack)
    out = s.constrain({"tool": "vertical", "picks": [n["slant"]]})
    assert out["accepted"] is False and out["made"] == [] and out["conflicts"]
    after = s.view()
    assert after["sketch"] == before["sketch"] and after["status"] == before["status"]
    assert len(s.undo_stack) == depth and s.redo_stack == redo


def test_coincident_on_a_point_and_a_circle_is_point_on_and_on_two_circles_concentric():
    sk, n = _world()
    s = _session(sk)
    out = s.constrain({"tool": "coincident", "picks": [n["a"], n["c1"]]})
    assert out["accepted"] is True
    c = s.sk.constraints[-1]
    assert c["type"] == "point_on" and c["refs"] == [n["a"], n["c1"]]
    out = s.constrain({"tool": "coincident", "picks": [n["c1"], n["c2"]]})
    assert out["accepted"] is True
    c = s.sk.constraints[-1]
    assert c["type"] == "concentric" and c["refs"] == [n["c1"], n["c2"]]
    # the other order of a curve and a point
    s.constrain({"tool": "coincident", "picks": [n["slant"], n["m"]]})
    assert s.sk.constraints[-1]["type"] == "point_on" and s.sk.constraints[-1]["refs"] == [n["m"], n["slant"]]


@pytest.mark.parametrize("pair,sign", [(("c1", "c2"), 1.0), (("c1", "c3"), -1.0), (("c2", "c1"), 1.0)])
def test_tangent_between_two_circles_carries_the_sign_the_desktop_gives(pair, sign):
    from tests.test_sketch_inference import _sketcher            # the desktop, headless
    picks = None
    sk, n = _world()
    picks = [n[pair[0]], n[pair[1]]]
    desk = _sketcher(sk.copy())
    assert desk._apply_constraint("tangent", picks) is True
    want = desk.sk.constraints[-1]
    s = _session(sk)
    s.constrain({"tool": "tangent", "picks": picks})
    got = s.sk.constraints[-1]
    assert got["type"] == want["type"] == "tangent" and got["refs"] == want["refs"]
    assert got["value"] == want["value"] == sign


def _rows():
    return [(t, row) for t, rows in OPS.FITS.items() for row in rows]


def _items(n, row):
    """Item ids of a sketch from `_world` that have the shape of `row`."""
    pool = {"point": ["a", "e", "m"], "line": ["slant", "flat", "up"], "circle": ["c1", "c2"], "arc": ["arc"],
            "curve": ["c1", "c2"]}
    used = {}
    out = []
    for kind in row:
        base = kind.rstrip("+")
        base = "line" if base == "any" else base
        i = used.get(base, 0)
        used[base] = i + 1
        out.append(n[pool[base][i]])
    return out


@pytest.mark.parametrize("tool,row", _rows(), ids=lambda v: v if isinstance(v, str) else "-".join(v))
def test_every_row_of_FITS_is_taken_by_constraints_for_and_listed_by_fits(tool, row):
    sk, n = _world()
    for picks in (_items(n, row), list(reversed(_items(n, row)))):
        if tool == "symmetric" and row == ("curve", "curve", "line"):
            picks = [n["c1"], n["c2"], n["flat"]]                      # the axis is the third pick
        if tool == "symmetric" and row == ("point", "point", "line"):
            picks = [n["a"], n["e"], n["flat"]]
        if tool == "symmetric" and row == ("line", "line", "line"):
            picks = [n["slant"], n["up"], n["flat"]]
        assert OPS.constraints_for(sk, tool, picks), (tool, row, picks)
        assert tool in OPS.fits(sk, picks)
        s = _session(sk)
        assert tool in s.fits({"selection": picks})


def test_a_selection_that_fits_no_row_lists_nothing_and_a_wrong_shape_is_refused_by_the_tool():
    sk, n = _world()
    s = _session(sk)
    assert s.fits({"selection": []}) == []
    assert s.fits({"selection": ["origin"]}) == [] or s.fits({"selection": [OPS.ORIGIN]}) == []
    assert OPS.fits(sk, [n["a"], n["slant"], n["c1"], n["flat"]]) == ["fix"]       # four mixed items: only fix
    # the wrong shape for a tool: two points are not parallel, a circle and a point are not midpoint
    assert "parallel" not in s.fits({"selection": [n["a"], n["e"]]})
    assert "midpoint" not in s.fits({"selection": [n["a"], n["c1"]]})
    before = s.view()
    with pytest.raises(SketchError, match="picks"):
        s.constrain({"tool": "parallel", "picks": [n["a"], n["e"]]})
    assert s.view() == before and not s.undo_stack


def test_fits_lists_the_tools_in_a_stable_order():
    sk, n = _world()
    assert OPS.fits(sk, [n["slant"], n["flat"]]) == ["horizontal", "vertical", "parallel", "perpendicular",
                                                     "equal", "fix"]
    assert OPS.fits(sk, [n["a"], n["slant"]]) == ["coincident", "midpoint", "fix"]
    assert OPS.fits(sk, [n["slant"], n["c1"]]) == ["tangent", "fix", "normal"]


def test_unconstrain_removes_exactly_the_named_constraint():
    sk, n = _world()
    s = _session(sk)
    s.constrain({"tool": "horizontal", "picks": [n["slant"]]})
    s.constrain({"tool": "equal", "picks": [n["slant"], n["flat"]]})
    s.constrain({"tool": "fix", "picks": [n["m"]]})
    ids = [c["id"] for c in s.sk.constraints]
    keep = [i for i in ids if i != ids[1]]
    depth = len(s.undo_stack)
    s.unconstrain({"constraint": ids[1]})
    assert [c["id"] for c in s.sk.constraints] == keep
    assert len(s.undo_stack) == depth + 1
    s.undo()
    assert [c["id"] for c in s.sk.constraints] == ids
    for bad in ({"constraint": "nope"}, {"constraint": n["a"]}, {"constraint": 4}, {}):
        with pytest.raises(SketchError, match="unconstrain.constraint"):
            s.unconstrain(bad)


def test_bad_constrain_messages_are_errors_naming_the_field_and_change_nothing():
    sk, n = _world()
    s = _session(sk)
    before = s.view()
    for body, word in (({"tool": "dance", "picks": [n["a"]]}, "constrain.tool"),
                       ({"tool": "fix", "picks": []}, "constrain.picks"),
                       ({"tool": "fix", "picks": ["zz"]}, r"constrain.picks\[0\]"),
                       ({"tool": "fix", "picks": [n["a"]], "x": 1}, "constrain.x"),
                       ({"tool": "fix", "picks": ["origin"]}, "constrain.picks")):
        with pytest.raises(SketchError, match=word):
            s.constrain(body)
    with pytest.raises(SketchError, match="fits.selection"):
        s.fits({"selection": "a"})
    s.drag_begin({"point": n["a"], "at": [0, 50]})
    with pytest.raises(SketchError, match="drag is in progress"):
        s.constrain({"tool": "fix", "picks": [n["a"]]})
    with pytest.raises(SketchError, match="drag is in progress"):
        s.unconstrain({"constraint": "c1"})
    s.drag_end(cancel=True)
    assert s.view() == before


def test_the_messages_over_the_socket(root):
    r = root()
    _open_doc(r)

    async def go():
        async with aiohttp.ClientSession() as http:
            ws = await _connect(http, r)
            await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "plane": TOP})
            got = await _ask(ws, {"type": "sketch.add", "shape": "line", "picks": [[0.0, 0.0], [30.0, 10.0]]})
            line = got["made"]["entities"][0]
            fit = await _ask(ws, {"type": "sketch.fits", "selection": [line]})
            assert fit["type"] == "sketch.fitted" and fit["tools"] == ["horizontal", "vertical", "fix"]
            out = await _ask(ws, {"type": "sketch.constrain", "tool": "horizontal", "picks": [line]})
            assert out["type"] == "sketch.constrained" and out["accepted"] is True and len(out["made"]) == 1
            assert out["status"]["constraints"][out["made"][0]] == "ok" and out["can_undo"] is True
            p1, p2 = [out["sketch"]["entities"][line][k] for k in ("p1", "p2")]
            assert out["sketch"]["points"][p1][1] == pytest.approx(out["sketch"]["points"][p2][1], abs=1e-6)
            for p in (p1, p2):
                await _ask(ws, {"type": "sketch.constrain", "tool": "fix", "picks": [p]})
            bad = await _ask(ws, {"type": "sketch.constrain", "tool": "vertical", "picks": [line]})
            assert bad["type"] == "sketch.constrained" and bad["accepted"] is False and bad["conflicts"]
            assert bad["sketch"]["constraints"] and not any(c["type"] == "vertical"
                                                            for c in bad["sketch"]["constraints"])
            gone = await _ask(ws, {"type": "sketch.unconstrain", "constraint": out["made"][0]})
            assert gone["type"] == "sketch.changed"
            assert out["made"][0] not in [c["id"] for c in gone["sketch"]["constraints"]]
            for msg in ({"type": "sketch.constrain", "tool": "parallel", "picks": [line]},
                        {"type": "sketch.unconstrain", "constraint": "zz"}, {"type": "sketch.fits", "selection": ["zz"]},
                        {"type": "sketch.constrain", "tool": "fix", "picks": [line], "extra": 1}):
                err = await _ask(ws, msg)
                assert err["type"] == "error" and err["for"] == msg["type"]
            assert (await _ask(ws, {"type": "sketch.state"}))["type"] == "sketch.state"      # the socket lives
            await ws.close()
    _run(go())
