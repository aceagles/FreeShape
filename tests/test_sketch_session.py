"""I7.3: a sketch editing session on the server (freeshape/sketch_session.py, the `sketch.*` messages on /ws in
freeshape/serve.py; the message shapes are in the serve.py docstring, "Sketch sessions"). The first tests drive the
Qt-free SketchSession directly; the rest drive a real server over the socket. Each test builds what it needs, so each
passes alone."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading

import aiohttp
import pytest

from freeshape import autosave as AS
from freeshape import doc_lock
from freeshape import doc_registry as R
from freeshape import serve as SV
from freeshape import sketch_ops as OPS
from freeshape.engine import process as P
from freeshape.engine import wire
from freeshape.sketch import ORIGIN, Sketch
from freeshape.sketch_session import SketchError, SketchSession

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
BRACKET_FILE = os.path.join(BRACKET, "bracket.fshape")
A = "client-aaaa1111"
B = "client-bbbb2222"
TOP = {"kind": "std", "name": "Top"}
RECT = {"shape": "rect", "picks": [[10.0, 10.0], [40.0, 30.0]]}


def _new():
    return SketchSession("D1", "T1", None, TOP, None, name="Sketch 1")


def _lines(sketch):
    return [k for k, e in sketch["entities"].items() if e["type"] == "line" and not e.get("construction")]


# ---------------------------------------------------------------------- the module alone


def test_importing_the_module_loads_no_qt_and_no_kernel():
    code = ("import sys; import freeshape.sketch_session; "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('PySide6', 'PyQt5', 'PyQt6', 'shiboken6', 'OCP')"
            " or m in ('freeshape.kernel', 'freeshape.features')]; assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, timeout=120,
                   env=dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")))


def test_a_line_with_a_coincident_to_an_existing_point_shares_that_point():
    s = _new()
    corner = s.add(RECT)["points"][1]                         # the rectangle's far corner, at (40, 30)
    made = s.add({"shape": "line", "picks": [[41.0, 29.0], [70.0, 60.0]],
                  "constraints": [{"type": "coincident", "refs": ["$p0", corner]}]})
    line = s.sk.entities[made["entities"][0]]
    assert line["p1"] == corner and made["points"][0] == corner
    assert s.sk.P(line["p1"]).tolist() == pytest.approx([40.0, 30.0])
    assert s.view()["status"]["ok"] is True
    # a coincident between two points the sketch already had is a constraint, kept as given
    made = s.add({"shape": "point", "picks": [[5.0, 5.0]], "constraints": [{"type": "coincident",
                                                                           "refs": ["$p0", ORIGIN]}]})
    assert made["points"] == [ORIGIN]


def test_a_centre_rectangle_and_a_clockwise_arc_are_what_the_builders_give():
    s = _new()
    before = s.sk.copy()
    made = s.add({"shape": "center_rect", "picks": [[0.0, 0.0], [20.0, 10.0]]})
    want = before.copy()
    ids = OPS.center_rect(want, [0.0, 0.0], [20.0, 10.0])
    want.solve()
    assert made["entities"] == ids and s.sk.to_dict() == want.to_dict()
    diag = s.sk.entities[ids[4]]
    assert diag["helper"] is True and diag["construction"] is True
    assert any(c["type"] == "midpoint" and c["refs"] == [made["points"][0], ids[4]] for c in s.sk.constraints)
    # clockwise: start (10, 0), end (-10, 0), through (0, -10) under them -> the builder swaps the ends
    before = s.sk.copy()
    made = s.add({"shape": "arc3", "picks": [[10.0, 0.0], [-10.0, 0.0], [0.0, -10.0]]})
    want = before.copy()
    aid = OPS.arc3(want, [10.0, 0.0], [-10.0, 0.0], [0.0, -10.0])
    want.solve()
    arc = s.sk.entities[made["entities"][0]]
    assert made["entities"] == [aid] and s.sk.to_dict() == want.to_dict()
    assert arc["p1"] == made["points"][1] and arc["p2"] == made["points"][0]      # swapped: end first


def test_undo_removes_a_circle_and_redo_restores_it():
    s = _new()
    s.add(RECT)
    with_rect = s.sk.to_dict()
    cid = s.add({"shape": "circle", "picks": [[0.0, 0.0]], "radius": 5.0})["entities"][0]
    assert s.sk.entities[cid]["type"] == "circle"
    with_circle = s.sk.to_dict()
    s.undo()
    assert cid not in s.sk.entities and s.sk.to_dict() == with_rect and s.view()["can_redo"] is True
    s.redo()
    assert s.sk.to_dict() == with_circle and s.view()["can_redo"] is False
    s.undo()
    s.undo()
    assert s.sk.entities == {} and s.view()["can_undo"] is False
    with pytest.raises(SketchError):
        s.undo()


def test_delete_and_construction_and_a_refused_step_changes_nothing():
    s = _new()
    lines = s.add(RECT)["entities"]
    s.construction({"ids": [lines[0]]})
    assert s.sk.entities[lines[0]]["construction"] is True
    s.construction({"ids": [lines[0], lines[1]], "value": False})
    assert not s.sk.entities[lines[0]]["construction"] and not s.sk.entities[lines[1]]["construction"]
    s.delete({"ids": [lines[2]]})
    assert lines[2] not in s.sk.entities and lines[0] in s.sk.entities
    state, undo = s.sk.to_dict(), len(s.undo_stack)
    for bad in ({"shape": "rect", "picks": [[1.0, 1.0], [1.0, 1.0]]},                 # the builder's ValueError
                {"shape": "line", "picks": [[0.0, 0.0], {"at": [1.0, 1.0], "point": "p999"}]},
                {"shape": "circle", "picks": [[0.0, 0.0]], "radius": -1},
                {"shape": "line", "picks": [[0.0, 0.0], [1.0, 0.0]],
                 "constraints": [{"type": "parallel", "refs": ["$e0", "$p0"]}]},
                {"shape": "line", "picks": [[0.0, 0.0], [1.0, 0.0]],
                 "constraints": [{"type": "radius", "refs": ["$e0"], "value": 3}]},
                {"shape": "line", "picks": [[0.0, 0.0], [1.0, 0.0]],
                 "constraints": [{"type": "distance", "refs": ["$e0"], "value": float("nan")}]},
                {"shape": "line", "picks": [[0.0, 0.0], [1.0, 0.0]], "constraints": [{"type": "fix", "refs": ["$e3"]}]}):
        with pytest.raises(SketchError):
            s.add(bad)
        assert s.sk.to_dict() == state and len(s.undo_stack) == undo
    with pytest.raises(SketchError):
        s.delete({"ids": [ORIGIN]})
    with pytest.raises(SketchError):
        s.construction({"ids": ["p1"]})


# ---------------------------------------------------------------------- over the socket


class _Root:
    """A Documents root with its own registry, data directory and server; the bracket copied in and opened."""

    def __init__(self, tmp_path, client_ttl=SV.CLIENT_TTL, desktop_lock=False):
        self.root = str(tmp_path / "Documents")
        self.data = str(tmp_path / "data")
        os.makedirs(self.root)
        self.registry = R.DocRegistry(os.path.join(self.data, R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=self.registry, root=self.root, client_ttl=client_ttl,
                                     data_dir=self.data, autosave_interval=3600.0, autosave_burst=3600.0)
        self.port = self.server.port
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)
        path = os.path.join(self.root, "Doc")
        shutil.copytree(BRACKET, path)
        with open(os.path.join(path, "document.json")) as fh:
            self.doc = json.load(fh)["id"]
        self.registry.register_created(self.doc, path)
        self.folder = os.path.realpath(path)
        if desktop_lock:
            doc_lock.acquire(self.folder, "desktop")

    def url(self, client):
        return f"{self.base}/ws?client={client}&document={self.doc}"

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()


@pytest.fixture
def root(tmp_path):
    made = []

    def make(**kw):
        r = _Root(tmp_path, **kw)
        made.append(r)
        return r
    try:
        yield make
    finally:
        for r in made:
            r.close()


def _run(coro, timeout=180):
    return asyncio.run(asyncio.wait_for(coro, timeout))


async def _post(s, r, path, body=None):
    async with s.post(f"{r.base}{path}", json=body or {}) as resp:
        assert resp.status == 200, await resp.text()
        return await resp.json()


async def _outputs(s, r):
    async with s.get(f"{r.base}/documents/{r.doc}/tabs/T1/outputs") as resp:
        assert resp.status == 200
        return wire.from_wire(await resp.read())


_skipped = {}            # id(ws) -> messages `_ask` read past, for `_next`


async def _next(ws, kind, timeout=60):
    held = _skipped.setdefault(id(ws), [])
    for i, data in enumerate(held):
        if data.get("type") == kind:
            return held.pop(i)
    while True:
        msg = await asyncio.wait_for(ws.receive(), timeout)
        assert msg.type == aiohttp.WSMsgType.TEXT, msg
        data = json.loads(msg.data)
        if data.get("type") == kind:
            return data


_ids = iter(range(1, 10 ** 9))


async def _ask(ws, msg, timeout=90):
    """Send `msg` with a fresh id; the answer that carries that id (pushes in between are skipped)."""
    ident = next(_ids)
    await ws.send_str(json.dumps({**msg, "id": ident}))
    while True:
        got = await asyncio.wait_for(ws.receive(), timeout)
        assert got.type == aiohttp.WSMsgType.TEXT, got          # never a closed socket
        data = json.loads(got.data)
        if data.get("id") == ident:
            return data
        _skipped.setdefault(id(ws), []).append(data)


async def _alive(ws):
    await ws.send_json({"type": "ping", "t": 42})
    assert await _next(ws, "pong") == {"type": "pong", "t": 42}


async def _connect(s, r, client=A):
    ws = await s.ws_connect(r.url(client))
    await _next(ws, "hello")
    return ws


def _open_doc(r):
    async def go():
        async with aiohttp.ClientSession() as s:
            await _post(s, r, f"/documents/{r.doc}/open")
    _run(go())


def test_a_new_sketch_on_top_with_a_rectangle_commits_four_lines_and_one_region(root):
    r = root()
    _open_doc(r)

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await _connect(s, r)
            got = await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "plane": TOP})
            assert got["type"] == "sketch.opened" and got["feature"] is None and got["name"] == "Sketch 1"
            assert got["frame"]["normal"] == pytest.approx([0, 0, 1]) and got["edges"]
            assert set(got["edges"][0]) == {"pick", "points", "height"}
            assert got["sketch"]["entities"] == {} and got["status"]["ok"] is True
            before = [f.id for f in (await _outputs(s, r)).features]
            got = await _ask(ws, {"type": "sketch.add", **RECT})
            assert got["type"] == "sketch.changed" and len(got["made"]["entities"]) == 4
            assert len(_lines(got["sketch"])) == 4
            assert set(got["status"]["entities"].values()) == {"free"}       # no dimensions yet
            assert got["can_undo"] is True
            done = await _ask(ws, {"type": "sketch.commit"})
            assert done["type"] == "sketch.committed" and done["rebuilt"][0] == "T1" and done["revision"]
            changed = await _next(ws, "changed")
            assert changed["client"] == A and changed["revisions"]["T1"] == done["revision"]
            out = await _outputs(s, r)
            fid = done["feature"]
            assert [f.id for f in out.features] == before + [fid]
            feat = next(f for f in out.features if f.id == fid)
            assert feat.type == "sketch" and feat.name == "Sketch 1" and feat.error is None
            sk = next(x for x in out.sketches if x.id == fid)
            assert len(_lines(sk.sketch)) == 4 and len(sk.regions) == 1
            gone = await _ask(ws, {"type": "sketch.state"})
            assert gone["type"] == "error" and "no sketch" in gone["message"]
            await ws.close()
    _run(go())


def test_opening_an_existing_sketch_returns_its_stored_points_entities_and_constraints(root):
    r = root()
    _open_doc(r)
    with open(BRACKET_FILE) as fh:
        stored = next(f for f in json.load(fh)["features"] if f["id"] == "F1")

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await _connect(s, r)
            got = await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "feature": "F1"})
            assert got["type"] == "sketch.opened" and got["feature"] == "F1" and got["name"] == stored["name"]
            assert got["plane"] == stored["params"]["plane"]
            want = Sketch.from_dict(stored["params"]["sketch"]).to_dict()
            assert got["sketch"]["points"] == want["points"]
            assert got["sketch"]["entities"] == want["entities"]
            assert got["sketch"]["constraints"] == want["constraints"]
            assert set(got["status"]["entities"]) == set(want["entities"])
            # an extrude is no sketch
            assert (await _ask(ws, {"type": "sketch.cancel"}))["type"] == "sketch.cancelled"
            bad =await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "feature": "F2"})
            assert bad["type"] == "error" and "not a sketch" in bad["message"]
            # an existing sketch commits through replace_feature: same id, same feature list, the new geometry
            before = [f.id for f in (await _outputs(s, r)).features]
            await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "feature": "F4"})
            await _ask(ws, {"type": "sketch.add", "shape": "circle", "picks": [[0.0, 0.0]], "radius": 3.0})
            done = await _ask(ws, {"type": "sketch.commit"})
            assert done["type"] == "sketch.committed" and done["feature"] == "F4"
            out = await _outputs(s, r)
            assert [f.id for f in out.features] == before
            sk = next(x for x in out.sketches if x.id == "F4")
            assert [e["type"] for e in sk.sketch["entities"].values()].count("circle") == 1
            await ws.close()
    _run(go())


def test_a_second_open_is_refused_and_cancel_leaves_the_features_unchanged(root):
    r = root()
    _open_doc(r)

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await _connect(s, r)
            before = await _outputs(s, r)
            assert (await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "plane": TOP}))["type"] \
                == "sketch.opened"
            again = await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "feature": "F1"})
            assert again["type"] == "error" and again["for"] == "sketch.open" and "already open" in again["message"]
            other = await _connect(s, r, B)                       # another client has its own sketch
            assert (await _ask(other, {"type": "sketch.open", "document": r.doc, "tab": "T1", "feature": "F1"}))[
                "type"] == "sketch.opened"
            await _ask(ws, {"type": "sketch.add", **RECT})
            got = await _ask(ws, {"type": "sketch.add", "shape": "circle", "picks": [[0.0, 0.0]], "radius": 4})
            cid = got["made"]["entities"][0]
            assert cid in got["sketch"]["entities"]
            assert cid not in (await _ask(ws, {"type": "sketch.undo"}))["sketch"]["entities"]
            assert cid in (await _ask(ws, {"type": "sketch.redo"}))["sketch"]["entities"]
            done = await _ask(ws, {"type": "sketch.cancel"})
            assert done == {"type": "sketch.cancelled", "id": done["id"], "document": r.doc, "tab": "T1",
                            "feature": None}
            after = await _outputs(s, r)
            assert [f.id for f in after.features] == [f.id for f in before.features]
            assert after.revision == before.revision
            assert (await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "plane": TOP}))["type"] \
                == "sketch.opened"                                # cancelled: a new one may open
            await ws.close()
            await other.close()
    _run(go())


def test_the_session_and_its_geometry_survive_a_reconnect_with_the_same_client_id(root):
    r = root()
    _open_doc(r)

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await _connect(s, r)
            await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "plane": TOP})
            drawn = (await _ask(ws, {"type": "sketch.add", **RECT}))["sketch"]
            await ws.close()
            ws = await _connect(s, r)
            got = await _ask(ws, {"type": "sketch.state"})
            assert got["type"] == "sketch.state" and got["sketch"] == drawn and got["can_undo"] is True
            assert got["frame"]["normal"] == pytest.approx([0, 0, 1])
            other = await _connect(s, r, B)                       # the session is A's, not the socket's
            assert (await _ask(other, {"type": "sketch.state"}))["type"] == "error"
            assert (await _ask(ws, {"type": "sketch.commit"}))["type"] == "sketch.committed"
            await ws.close()
            await other.close()
    _run(go())


def test_a_read_only_document_refuses_commit_and_keeps_the_session(root):
    r = root(desktop_lock=True)

    async def go():
        async with aiohttp.ClientSession() as s:
            assert (await _post(s, r, f"/documents/{r.doc}/open"))["read_only"] is True
            ws = await _connect(s, r)
            await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "plane": TOP})
            drawn = (await _ask(ws, {"type": "sketch.add", **RECT}))["sketch"]
            before = await _outputs(s, r)
            got = await _ask(ws, {"type": "sketch.commit"})
            assert got["type"] == "error" and got["locked"] is True and got["holder"]["program"] == "desktop"
            assert (await _outputs(s, r)).revision == before.revision
            kept = await _ask(ws, {"type": "sketch.state"})
            assert kept["type"] == "sketch.state" and kept["sketch"] == drawn
            await ws.close()
    _run(go())


def test_an_expired_client_leaves_its_unfinished_sketch_in_the_autosave(root):
    r = root(client_ttl=0.3)
    _open_doc(r)

    async def go():
        async with aiohttp.ClientSession() as s:
            keeper = await _connect(s, r, B)                      # B holds the Document open (no idle close)
            ws = await _connect(s, r, A)
            await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "plane": TOP})
            await _ask(ws, {"type": "sketch.add", **RECT})
            features = [f.id for f in (await _outputs(s, r)).features]
            await ws.close()                                      # A's entry expires 0.3 s after this
            for _ in range(600):
                infos = AS.index(r.data).get(r.doc)
                if infos:
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("no autosave was written when the client expired")
            snap = AS.load(r.data, infos[-1])
            data = json.loads(snap["tabs"]["T1"].decode("utf-8"))
            new = [f for f in data["features"] if f["name"] == "Sketch 1"]
            assert len(new) == 1 and new[0]["type"] == "sketch" and new[0]["params"]["plane"] == TOP
            assert len(_lines(new[0]["params"]["sketch"])) == 4
            assert new[0]["id"] not in features and [f["id"] for f in data["features"]][:-1] == features
            assert [f.id for f in (await _outputs(s, r)).features] == features      # the open model is unchanged
            ws = await _connect(s, r, A)                          # a new entry: the session is gone
            assert (await _ask(ws, {"type": "sketch.state"}))["type"] == "error"
            await ws.close()
            await keeper.close()
    _run(go())


def test_closing_the_document_keeps_the_sketch_and_recover_brings_it_back(root):
    r = root()
    _open_doc(r)

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await _connect(s, r)
            await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "feature": "F4"})
            await _ask(ws, {"type": "sketch.add", "shape": "circle", "picks": [[0.0, 0.0]], "radius": 3.0})
            await _post(s, r, f"/documents/{r.doc}/close")
            assert (await _ask(ws, {"type": "sketch.state"}))["type"] == "error"
            opened = await _post(s, r, f"/documents/{r.doc}/open")
            assert opened["autosave"] is not None and [t["id"] for t in opened["autosave"]["tabs"]] == ["T1"]
            await _post(s, r, f"/documents/{r.doc}/recover")
            out = await _outputs(s, r)
            sk = next(x for x in out.sketches if x.id == "F4")
            assert sum(1 for e in sk.sketch["entities"].values() if e["type"] == "circle"
                       and e["r"] == pytest.approx(3.0)) == 1
            await ws.close()
    _run(go())


HOSTILE = [
    "not json",
    "[1, 2]",
    json.dumps({"type": "sketch.nope"}),
    json.dumps({"type": "sketch.open"}),
    json.dumps({"type": "sketch.open", "tab": "T1", "plane": TOP}),                  # no document on a root
    json.dumps({"type": "sketch.open", "document": "x" * 65, "tab": "T1", "plane": TOP}),
    json.dumps({"type": "sketch.open", "document": "{doc}", "tab": "T9", "plane": TOP}),
    json.dumps({"type": "sketch.open", "document": "{doc}", "tab": "T1"}),
    json.dumps({"type": "sketch.open", "document": "{doc}", "tab": "T1", "plane": TOP, "feature": "F1"}),
    json.dumps({"type": "sketch.open", "document": "{doc}", "tab": "T1", "feature": "../F1"}),
    json.dumps({"type": "sketch.open", "document": "{doc}", "tab": "T1", "feature": "F99"}),
    json.dumps({"type": "sketch.open", "document": "{doc}", "tab": "T1", "plane": {"kind": "std", "name": "Up"}}),
    json.dumps({"type": "sketch.open", "document": "{doc}", "tab": "T1", "plane": {"kind": "face", "face": {"x": [1]}}}),
    json.dumps({"type": "sketch.open", "document": "{doc}", "tab": "T1", "plane": {"kind": "plane", "fid": 7}}),
    json.dumps({"type": "sketch.open", "document": "{doc}", "tab": "T1", "plane": "Top"}),
    json.dumps({"type": "sketch.open", "document": "{doc}", "tab": "T1", "plane": TOP, "extra": 1}),
    json.dumps({"type": "sketch.add", "shape": "rect", "picks": [[0, 0], [1, 1]]}),          # nothing open yet
    json.dumps({"type": "sketch.commit", "id": {"a": 1}}),
]
HOSTILE_OPEN = [
    json.dumps({"type": "sketch.add"}),
    json.dumps({"type": "sketch.add", "shape": "hexagon", "picks": []}),
    json.dumps({"type": "sketch.add", "shape": "rect", "picks": [[0, 0]]}),
    json.dumps({"type": "sketch.add", "shape": "rect", "picks": [[0, 0], [1, "x"]]}),
    json.dumps({"type": "sketch.add", "shape": "rect", "picks": [[0, 0], [1e300, 1]]}),
    json.dumps({"type": "sketch.add", "shape": "rect", "picks": [[0, 0], [1, 1, 1]]}),
    json.dumps({"type": "sketch.add", "shape": "rect", "picks": [[0, 0], {"at": [1, 1], "point": ["p1"]}]}),
    json.dumps({"type": "sketch.add", "shape": "rect", "picks": [[0, 0], [1, 1]], "construction": "yes"}),
    json.dumps({"type": "sketch.add", "shape": "rect", "picks": [[0, 0], [1, 1]], "radius": 3}),
    json.dumps({"type": "sketch.add", "shape": "circle", "picks": [[0, 0]], "radius": 0}),
    json.dumps({"type": "sketch.add", "shape": "circle", "picks": [[0, 0]], "radius": True}),
    json.dumps({"type": "sketch.add", "shape": "line", "picks": [[0, 0], [0, 0]]}),
    json.dumps({"type": "sketch.add", "shape": "arc3", "picks": [[0, 0], [1, 0], [2, 0]]}),
    json.dumps({"type": "sketch.add", "shape": "line", "picks": [[0, 0], [1, 0]], "constraints": "all"}),
    json.dumps({"type": "sketch.add", "shape": "line", "picks": [[0, 0], [1, 0]],
                "constraints": [{"type": "coincident", "refs": ["$p0", "$p0"]}]}),
    json.dumps({"type": "sketch.add", "shape": "line", "picks": [[0, 0], [1, 0]],
                "constraints": [{"type": "symmetric", "refs": ["$p0", "$p1"]}]}),
    json.dumps({"type": "sketch.add", "shape": "line", "picks": [[0, 0], [1, 0]],
                "constraints": [{"type": "offset", "refs": ["$e0", "$e0"], "value": 1}]}),
    json.dumps({"type": "sketch.add", "shape": "line", "picks": [[0, 0], [1, 0]],
                "constraints": [{"type": "horizontal", "refs": ["$e0"], "value": 1}]}),
    json.dumps({"type": "sketch.add", "shape": "line", "picks": [[0, 0], [1, 0]],
                "constraints": [{"type": "angle", "refs": ["$e0", "$e0x"], "value": 400}]}),
    json.dumps({"type": "sketch.add", "shape": "line", "picks": [[0, 0], [1, 0]],
                "constraints": [{"type": "distance", "refs": ["$e0"], "value": 1, "pos": [0, 0]}]}),
    json.dumps({"type": "sketch.add", "shape": "line", "picks": [[0, 0], [1, 0]],
                "constraints": [{"type": "distance", "refs": ["$e0"]}] * 65}),
    json.dumps({"type": "sketch.delete", "ids": []}),
    json.dumps({"type": "sketch.delete", "ids": ["origin"]}),
    json.dumps({"type": "sketch.delete", "ids": ["nope"]}),
    json.dumps({"type": "sketch.delete", "ids": [1]}),
    json.dumps({"type": "sketch.construction", "ids": ["origin"]}),
    json.dumps({"type": "sketch.construction", "ids": ["nope"], "value": 1}),
    json.dumps({"type": "sketch.redo"}),
    json.dumps({"type": "sketch.undo", "document": "other-doc"}),
    json.dumps({"type": "sketch.redo", "steps": 3}),
    '{"type": "sketch.add", "shape": "rect", "picks": [[NaN, 0], [1, 1]]}',
    '{"type": "sketch.add", "shape": "rect", "picks": [[Infinity, 0], [1, 1]]}',
    '{"type": "sketch.add", "shape": "line", "picks": ' + "[" * 5000 + "]" * 5000 + "}",
]


def test_hostile_messages_answer_an_error_and_never_close_the_socket(root):
    r = root()
    _open_doc(r)

    async def go():
        async with aiohttp.ClientSession() as s:
            ws = await _connect(s, r)

            async def refused(text):
                await ws.send_str(text.replace("{doc}", r.doc))
                while True:
                    got = await asyncio.wait_for(ws.receive(), 90)
                    assert got.type == aiohttp.WSMsgType.TEXT, (text[:80], got)
                    data = json.loads(got.data)
                    if data.get("type") in ("error",):
                        assert isinstance(data["message"], str) and data["message"] != "internal error", \
                            (text[:120], data)
                        return
                    assert data.get("type") in ("changed", "tabs"), (text[:120], data)
            for text in HOSTILE:
                await refused(text)
            await _alive(ws)
            await _ask(ws, {"type": "sketch.open", "document": r.doc, "tab": "T1", "plane": TOP})
            drawn = (await _ask(ws, {"type": "sketch.add", **RECT}))["sketch"]
            for text in HOSTILE_OPEN:
                await refused(text)
            await _alive(ws)
            got = await _ask(ws, {"type": "sketch.state"})
            assert got["sketch"] == drawn and got["can_undo"] is True and got["can_redo"] is False
            await ws.close()
    _run(go(), 300)
