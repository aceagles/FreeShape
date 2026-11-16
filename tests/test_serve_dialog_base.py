"""F3: picks inside a feature dialog resolve against the model before the feature (the dialog's base), as the
desktop's dialog picks do. POST .../refs with "dialog": did, GET .../dialog/<did>/base, and the engine's
`refs(..., base)` and `base_outputs` (contract.Engine). Each test builds its own root, server or engine, so each
passes alone."""
import asyncio
import http.client
import json
import os
import shutil
import threading

import aiohttp
import numpy as np
import pytest

from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape import serve as SV
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine import process as P
from freeshape.engine import wire
from freeshape.engine.contract import EngineError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
BRACKET_FILE = os.path.join(BRACKET, "bracket.fshape")
A = "client-aaaa1111"
B = "client-bbbb2222"
PART = "F2/1"           # the Bracket's one part, as the outputs name it


class _Root:
    def __init__(self, tmp_path):
        self.root = str(tmp_path / "Documents")
        os.makedirs(self.root)
        self.registry = R.DocRegistry(os.path.join(str(tmp_path / "data"), R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=self.registry, root=self.root)
        self.port = self.server.port
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)

    def open_bracket(self):
        path = os.path.join(self.root, "Doc")
        shutil.copytree(BRACKET, path)
        with open(os.path.join(path, "document.json")) as fh:
            doc_id = json.load(fh)["id"]
        self.registry.register_created(doc_id, path)
        self.ok("POST", f"/documents/{doc_id}/open")
        return doc_id

    def request(self, method, path, body=None, client=A):
        h = {"Host": f"127.0.0.1:{self.port}"}
        if client is not None:
            h["X-Client-Id"] = client
        data = None
        if method == "POST":
            h["Content-Type"] = "application/json"
            data = json.dumps(body if body is not None else {}).encode()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=90)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if data is not None:
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            payload = r.read()
            if r.getheader("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return r.status, payload, dict(r.getheaders())
        finally:
            c.close()

    def ok(self, method, path, body=None, expect=200, client=A):
        status, payload, _ = self.request(method, path, body, client=client)
        assert status == expect, (method, path, status, payload)
        return payload

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()


@pytest.fixture
def server(tmp_path):
    r = _Root(tmp_path)
    try:
        yield r
    finally:
        r.close()


def _path(doc_id, did, verb):
    return f"/documents/{doc_id}/tabs/T1/dialog/{did}/{verb}"


def _refs(doc_id):
    return f"/documents/{doc_id}/tabs/T1/refs"


def _part(out, pid=PART):
    return next(p for p in out.parts if p.id == pid)


def _fillet_edge_index(part):
    """The 1-based index, in `part`, of the edge Fillet 1 rounds: the straight edge along y at x = 6, z = 6 (the
    inside corner of the bracket). It exists only before the fillet."""
    for i, e in enumerate(K.edges(K.shape_from_brep(part.brep))):
        pts = np.asarray(K.discretize_edge(e), float)
        if np.allclose(pts[:, 0], 6.0, atol=1e-6) and np.allclose(pts[:, 2], 6.0, atol=1e-6) \
                and np.ptp(pts[:, 1]) > 39.0:
            return i + 1
    return None


def _pick(index):
    return {"part": PART, "kind": "edge", "index": index, "want": ["edge"]}


async def _next_of(ws, kind):
    while True:
        msg = json.loads((await asyncio.wait_for(ws.receive(), 60)).data)
        if msg["type"] == kind:
            return msg


# ---------------------------------------------------------------------- the server


def test_a_pick_in_the_fillet_dialog_reads_the_model_before_the_fillet_and_its_preview_builds(server):
    r = server
    doc_id = r.open_bracket()
    committed = wire.from_wire(r.request("GET", f"/documents/{doc_id}/tabs/T1/outputs")[1])
    assert _fillet_edge_index(_part(committed)) is None              # the fillet consumed its edge
    loop_run = lambda *a: asyncio.get_running_loop().run_in_executor(None, *a)      # noqa: E731

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(f"{r.base}/ws?client={A}&document={doc_id}") as ws:
                await _next_of(ws, "hello")
                opened = await loop_run(r.ok, "POST", f"/documents/{doc_id}/tabs/T1/features/F3/dialog", {}, 201)
                did = opened["dialog"]
                status, data, headers = await loop_run(r.request, "GET", _path(doc_id, did, "base"))
                assert status == 200 and headers["Content-Type"].startswith("application/octet-stream")
                assert headers["X-Revision"] == str(committed.revision)
                base = wire.from_wire(data)
                assert base.errors == {} and base.revision == committed.revision
                index = _fillet_edge_index(_part(base))
                assert index is not None                             # the base model still has the edge
                got = await loop_run(r.ok, "POST", _refs(doc_id), {"picks": [_pick(index)], "dialog": did})
                item = got["items"][0]
                assert item["ok"] is True and item["kind"] == "edge"
                assert item["name"] == _part(base).names["edge"][index]
                # the same pick without the dialog still answers from the last build
                status, plain_, _ = await loop_run(r.request, "POST", _refs(doc_id), {"picks": [_pick(index)]})
                if status == 200:
                    assert plain_["items"][0]["name"] == _part(committed).names["edge"].get(index)
                    assert plain_["items"][0]["name"] != item["name"]
                else:
                    assert status == 400 and "index" in plain_["error"]
                # the field takes it, and the preview builds with no "gone" error
                upd = await loop_run(r.ok, "POST", _path(doc_id, did, "update"),
                                     {"values": {"edges": [{"kind": item["kind"], "ref": item["ref"]}]}})
                msg = await _next_of(ws, "preview")
                assert msg["preview"] == upd["preview"]
                assert msg["ok"] is True and msg["error"] is None, msg
                done = await loop_run(r.ok, "POST", _path(doc_id, did, "accept"), {})
                assert done["rebuilt"]
    asyncio.run(asyncio.wait_for(go(), 180))
    after = wire.from_wire(r.request("GET", f"/documents/{doc_id}/tabs/T1/outputs")[1])
    assert after.errors == {}


def test_the_dialog_on_refs_and_base_belongs_to_its_client(server):
    r = server
    doc_id = r.open_bracket()
    did = r.ok("POST", f"/documents/{doc_id}/tabs/T1/features/F3/dialog", {}, 201)["dialog"]
    body = {"picks": [_pick(1)], "dialog": did}
    assert r.request("POST", _refs(doc_id), body, client=B)[0] == 404             # another client's dialog
    assert r.request("GET", _path(doc_id, did, "base"), client=B)[0] == 404
    assert r.request("POST", _refs(doc_id), body, client=None)[0] == 400          # no X-Client-Id
    assert r.request("GET", _path(doc_id, did, "base"), client=None)[0] == 400
    assert r.request("POST", _refs(doc_id), {**body, "dialog": "0" * 16})[0] == 404     # no such dialog
    assert r.request("POST", _refs(doc_id), {**body, "dialog": 5})[0] == 400
    assert r.request("POST", _refs(doc_id), {**body, "dialog": "../x"})[0] == 400
    assert r.request("POST", _path(doc_id, did, "base"), {})[0] == 405
    assert r.ok("POST", _refs(doc_id), body)["items"][0]["ok"] is True
    r.ok("POST", _path(doc_id, did, "cancel"), {})
    assert r.request("POST", _refs(doc_id), body)[0] == 404                       # a closed dialog is gone
    assert r.request("GET", _path(doc_id, did, "base"))[0] == 404


# ---------------------------------------------------------------------- the engine, on both executors


def _engines():
    yield InProcessEngine()
    yield ProcessEngine()


def _hashes(out):
    return sorted(p.hash for p in out.parts)


@pytest.mark.parametrize("which", [0, 1])
def test_base_outputs_and_refs_read_the_model_before_the_feature(which):
    eng = list(_engines())[which]
    try:
        tab = eng.open_tab(BRACKET_FILE)
        committed = eng.outputs(tab)
        base = eng.base_outputs(tab, "F3")
        assert base.revision == committed.revision and base.errors == {}
        index = _fillet_edge_index(_part(base))
        assert index is not None and _fillet_edge_index(_part(committed)) is None
        got = eng.refs(tab, [_pick(index)], {"feature": "F3"})[0]
        assert got["ok"] is True and got["name"] == _part(base).names["edge"][index]
        assert eng.refs(tab, [_pick(1)])[0]["name"] == _part(committed).names["edge"][1]
        assert eng.outputs(tab).revision == committed.revision                   # a query
        with pytest.raises(EngineError):
            eng.base_outputs(tab, "nope")
        with pytest.raises(EngineError):
            eng.refs(tab, [_pick(1)], {"feature": "nope"})
        with pytest.raises(EngineError):
            eng.refs(tab, [_pick(1)], {"feature": 3})
    finally:
        eng.close()
        P.SPARES.clear()


@pytest.mark.parametrize("which", [0, 1])
def test_a_new_feature_base_is_the_model_at_the_rollback_bar(which):
    eng = list(_engines())[which]
    try:
        tab = eng.open_tab(BRACKET_FILE)
        assert _hashes(eng.base_outputs(tab, None)) == _hashes(eng.outputs(tab))     # no rollback: the whole model
        before_fillet = _hashes(eng.base_outputs(tab, "F3"))
        eng.submit_edit(tab, {"op": "set_rollback", "index": 2})                      # the bar after Extrude 1
        assert _hashes(eng.base_outputs(tab, None)) == before_fillet == _hashes(eng.outputs(tab))
        index = _fillet_edge_index(_part(eng.base_outputs(tab, None)))
        assert eng.refs(tab, [_pick(index)], {"feature": None})[0]["ok"] is True
    finally:
        eng.close()
        P.SPARES.clear()
