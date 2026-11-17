"""I6.1: start a NEW feature on the server (POST .../features/dialog, GET .../features/types) and the create mode
of the dialog session (update / accept / cancel are the routes of an existing feature's dialog, test_serve_dialog.py).
Each test builds its own root and server, so each passes alone."""
import asyncio
import http.client
import json
import os
import shutil
import threading
import urllib.parse

import aiohttp
import pytest

from freeshape import dialog_schema as DSC
from freeshape import doc_lock
from freeshape import doc_registry as R
from freeshape import serve as SV
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine import process as P
from freeshape.engine import wire
from freeshape.engine.contract import BadPick, EngineError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
A = "client-aaaa1111"
B = "client-bbbb2222"
EDGES = (1, 2)              # two edges of the bracket's part (1-based) that a fillet can take; set after a probe


class _Root:
    def __init__(self, tmp_path):
        self.root = str(tmp_path / "Documents")
        self.data = str(tmp_path / "data")
        os.makedirs(self.root)
        self.registry = R.DocRegistry(os.path.join(self.data, R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=self.registry, root=self.root)
        self.port = self.server.port
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)

    def copy(self, source, name):
        path = os.path.join(self.root, name)
        shutil.copytree(source, path)
        with open(os.path.join(path, "document.json")) as fh:
            doc_id = json.load(fh)["id"]
        self.registry.register_created(doc_id, path)
        return doc_id, os.path.realpath(path)

    def request(self, method, path, body=None, headers=None, raw=None, client=A):
        h = {"Host": f"127.0.0.1:{self.port}"}
        if client is not None:
            h["X-Client-Id"] = client
        if method == "POST":
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body if body is not None else {}).encode()
                                            if method == "POST" else None)
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
            return r.status, payload
        finally:
            c.close()

    def ok(self, method, path, body=None, expect=200, client=A):
        status, payload = self.request(method, path, body, client=client)
        assert status == expect, (method, path, status, payload)
        return payload

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()


@pytest.fixture
def root(tmp_path):
    made = []

    def make():
        r = _Root(tmp_path)
        made.append(r)
        return r
    try:
        yield make
    finally:
        for r in made:
            r.close()


def _open(root):
    r = root()
    doc_id, folder = r.copy(BRACKET, "Doc")
    r.ok("POST", f"/documents/{doc_id}/open")
    return r, doc_id, folder


def _tab(doc_id, tail):
    return f"/documents/{doc_id}/tabs/T1/{tail}"


def _edit(r, doc_id, edit):
    return r.ok("POST", _tab(doc_id, "edit"), edit)


def _outputs(r, doc_id):
    status, raw = r.request("GET", _tab(doc_id, "outputs"))
    assert status == 200
    return wire.from_wire(raw)


def _revision(r, doc_id):
    return r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"][0]["revision"]


def _new(r, doc_id, ftype, picks=None, expect=201, client=A):
    body = {"type": ftype} if picks is None else {"type": ftype, "picks": picks}
    return r.ok("POST", _tab(doc_id, "features/dialog"), body, expect=expect, client=client)


def _path(doc_id, did, verb):
    return _tab(doc_id, f"dialog/{did}/{verb}")


def _pick(part, kind, index=None, **kw):
    p = {"part": part, "kind": kind}
    if index is not None:
        p["index"] = index
    p.update(kw)
    return p


def _types(feature_list):
    return [(f.id, f.type) for f in feature_list]


def _no_extrude(r, doc_id):
    """The bracket cut back to the sketch F1 alone: no part."""
    _edit(r, doc_id, {"op": "delete_features", "ids": [f"F{n}" for n in range(2, 9)]})
    out = _outputs(r, doc_id)
    assert _types(out.features) == [("F1", "sketch")] and not out.parts
    return out


def _faces(out):
    return sum(len(p.names["face"]) for p in out.parts)


def _region_picks(out, n=1):
    return [_pick("F1", "region", n, want=["region"])]


# ---------------------------------------------------------------------- starting with a selection


def test_extrude_with_a_sketch_region_picked_holds_it_and_accept_adds_a_part(root):
    r, doc_id, _ = _open(root)
    _no_extrude(r, doc_id)
    before = _revision(r, doc_id)
    got = _new(r, doc_id, "extrude", [_pick("F1", "region", 1)])         # `want` may be left out
    assert got["feature"] is None and got["type"] == "extrude" and got["tab"] == "T1" and got["document"] == doc_id
    assert got["name"] == "Extrude 1" and got["revision"] == before and got["active"] is None
    assert got["description"]["type"] == "extrude" and len(got["dialog"]) == 16
    items = got["fields"]["profiles"]
    assert len(items) == 1 and items[0]["ok"] is True and items[0]["kind"] == "region"
    assert set(items[0]) == {"ok", "kind", "ref", "name"} and items[0]["ref"]["sketch"] == "F1"
    assert got["values"]["depth"] == {"value": 25.0, "text": "25"}            # the type's defaults
    assert _revision(r, doc_id) == before                                       # opening is a query
    r.ok("POST", _path(doc_id, got["dialog"], "update"), {"values": {"depth": "2*10 mm"}})
    done = r.ok("POST", _path(doc_id, got["dialog"], "accept"))
    assert done["rebuilt"][0] == "T1" and done["revision"] == _revision(r, doc_id) == before + 1
    out = _outputs(r, doc_id)
    assert _types(out.features)[0] == ("F1", "sketch") and len(out.features) == 2 and len(out.parts) == 1
    new_id = out.features[1].id
    assert out.features[1].type == "extrude" and out.features[1].error is None
    again = r.ok("POST", _tab(doc_id, f"features/{new_id}/dialog"), {}, expect=201)
    assert again["values"]["depth"]["value"] == 20.0 and again["fields"]["profiles"][0]["ref"]["sketch"] == "F1"
    r.ok("POST", _path(doc_id, got["dialog"], "accept"), expect=404)           # closed by accept


def test_fillet_with_two_edges_picked_fills_both_and_accept_changes_the_face_count(root):
    r, doc_id, _ = _open(root)
    out = _outputs(r, doc_id)
    pid = out.parts[0].id
    faces = _faces(out)
    got = _new(r, doc_id, "fillet", [_pick(pid, "edge", EDGES[0]), _pick(pid, "edge", EDGES[1])])
    edges = got["fields"]["edges"]
    assert [e["kind"] for e in edges] == ["edge", "edge"] and edges[0]["ref"] != edges[1]["ref"]
    assert [e["name"] for e in edges] == [out.parts[0].names["edge"][i] for i in EDGES]    # 1-based Element names
    assert got["values"]["radius"]["value"] == 2.0
    r.ok("POST", _path(doc_id, got["dialog"], "accept"))
    after = _outputs(r, doc_id)
    assert _types(after.features)[-1][1] == "fillet" and len(after.features) == len(out.features) + 1
    assert after.features[-1].error is None and _faces(after) != faces


def test_the_new_feature_goes_in_at_the_rollback_bar_not_at_the_end(root):
    r, doc_id, _ = _open(root)
    full = _types(_outputs(r, doc_id).features)
    assert [t for _, t in full][:3] == ["sketch", "extrude", "fillet"]
    _edit(r, doc_id, {"op": "set_rollback", "index": 1})                        # only the sketch builds
    out = _outputs(r, doc_id)
    assert out.rollback == 1 and not out.parts
    got = _new(r, doc_id, "extrude", [_pick("F1", "region", 1)])
    assert got["fields"]["profiles"]
    r.ok("POST", _path(doc_id, got["dialog"], "accept"))
    after = _outputs(r, doc_id)
    ids = [i for i, _ in _types(after.features)]
    assert len(ids) == len(full) + 1 and ids[0] == "F1" and ids[2:] == [i for i, _ in full][1:]
    assert after.features[1].type == "extrude" and ids[1] not in {i for i, _ in full}      # a new id, at the bar
    assert after.rollback == 2                                                  # the bar stays just below it


def test_a_new_feature_without_picks_opens_with_its_defaults_and_unplaceable_picks_are_dropped(root):
    r, doc_id, _ = _open(root)
    out = _outputs(r, doc_id)
    plain_ = _new(r, doc_id, "extrude")
    assert plain_["fields"]["profiles"] == [] and plain_["feature"] is None
    got = _new(r, doc_id, "extrude", [_pick(out.parts[0].id, "edge", 1)])      # an edge goes in no extrude field
    assert got["fields"]["profiles"] == []
    assert _new(r, doc_id, "extrude", [])["name"] not in {f.name for f in out.features}     # the next free name
    # a type with no schema, and picks the tab cannot read
    err = _new(r, doc_id, "sketch", expect=400)["error"]
    assert "sketch" in err
    assert _new(r, doc_id, "nope", expect=400)["error"]
    err = _new(r, doc_id, "extrude", [_pick("F1", "region", 99)], expect=400)["error"]
    assert err.startswith("picks[0].index")
    err = _new(r, doc_id, "extrude", [_pick("Fnone", "region", 1)], expect=400)["error"]
    assert err.startswith("picks[0].part")


def test_cancel_adds_nothing_and_leaves_the_revision(root):
    r, doc_id, _ = _open(root)
    _no_extrude(r, doc_id)
    before, features = _revision(r, doc_id), _types(_outputs(r, doc_id).features)
    did = _new(r, doc_id, "extrude", [_pick("F1", "region", 1)])["dialog"]
    r.ok("POST", _path(doc_id, did, "update"), {"values": {"depth": 3}})
    assert r.ok("POST", _path(doc_id, did, "cancel")) == {"cancelled": True}
    assert _revision(r, doc_id) == before and _types(_outputs(r, doc_id).features) == features
    assert not _outputs(r, doc_id).parts
    r.ok("POST", _path(doc_id, did, "update"), {"values": {"depth": 4}}, expect=404)


def test_a_read_only_document_opens_a_new_dialog_but_refuses_accept(root):
    r = root()
    doc_id, folder = r.copy(BRACKET, "Doc")
    doc_lock.acquire(folder, "desktop")
    assert r.ok("POST", f"/documents/{doc_id}/open")["read_only"] is True
    did = _new(r, doc_id, "extrude", [_pick("F1", "region", 1)])["dialog"]
    before = _revision(r, doc_id)
    status, body = r.request("POST", _path(doc_id, did, "accept"), {})
    assert status == 423 and body["holder"]["program"] == "desktop"
    assert _revision(r, doc_id) == before
    r.ok("POST", _path(doc_id, did, "update"), {"values": {"depth": 6}})        # still open
    assert r.ok("GET", _tab(doc_id, "features/types"))["types"]                 # the list is a query too


def test_the_update_previews_a_new_feature_and_a_second_client_cannot_use_the_dialog(root):
    r, doc_id, _ = _open(root)
    _no_extrude(r, doc_id)
    rev = _revision(r, doc_id)

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(f"{r.base}/ws?client={A}&document={doc_id}") as wa:
                await _next_of(wa, "hello")
                loop = asyncio.get_running_loop()
                did = (await loop.run_in_executor(None, _new, r, doc_id, "extrude", [_pick("F1", "region", 1)]))["dialog"]
                got = await loop.run_in_executor(
                    None, lambda: r.ok("POST", _path(doc_id, did, "update"), {"values": {"depth": 7}}))
                assert got["preview"] == 1
                msg = await _next_of(wa, "preview")
                assert msg == {"type": "preview", "document": doc_id, "tab": "T1", "dialog": did, "feature": None,
                               "preview": 1, "revision": rev, "ok": True, "outputs": True, "error": None}
                return did
    did = asyncio.run(asyncio.wait_for(go(), 120))
    status, body = r.request("GET", _path(doc_id, did, "preview"))
    assert status == 200
    out = wire.from_wire(body)
    assert len(out.parts) == 1 and out.parts[0].id.startswith("P")             # the previewed model: the new feature, a temporary id
    assert _revision(r, doc_id) == rev and not _outputs(r, doc_id).parts          # nothing was committed
    assert r.request("GET", _path(doc_id, did, "preview"), client=B)[0] == 404
    assert r.request("POST", _path(doc_id, did, "accept"), {}, client=B)[0] == 404


# ---------------------------------------------------------------------- the list of types


def test_types_marks_the_types_with_custom_fields_as_not_all_standard(root):
    r, doc_id, _ = _open(root)
    got = r.ok("GET", _tab(doc_id, "features/types"), client=None)               # no client id needed
    assert got["document"] == doc_id and got["tab"] == "T1" and got["revision"] == _revision(r, doc_id)
    rows = {t["type"]: t for t in got["types"]}
    assert [t["type"] for t in got["types"]] == sorted(rows) and set(rows) == set(DSC.startable_feature_types())
    custom = set(DSC.custom_feature_types())
    assert custom >= {"hole", "split"} and "mate_connector" not in custom       # served since I10.4
    for t, row in rows.items():
        assert set(row) == {"type", "label", "standard", "can_start"} and row["label"]
        assert row["standard"] is (t not in custom) and row["can_start"] is True        # no selection: all start
    assert rows["extrude"]["label"] == "Extrude" and rows["extrude"]["standard"] is True
    assert rows["hole"]["standard"] is False and "boundary_surface" not in rows


def test_python_feature_and_plugin_types_are_never_listed_whatever_the_process_has_loaded(root, monkeypatch):
    """The desktop's install() and the plugin loader add types to SCHEMAS and FEATURE_LABELS; the list must not move."""
    before = DSC.startable_feature_types()
    assert before and not any(t.startswith(("py:", "plugin:")) for t in before)
    for t in ("py:gears/gear", "plugin:demo/ring"):                                  # what install() and the loaders do
        monkeypatch.setitem(DSC.SCHEMAS, t, DSC.SCHEMAS["extrude"])
        monkeypatch.setitem(DSC.F.FEATURE_LABELS, t, "Loaded type")
    assert DSC.startable_feature_types() == before
    r, doc_id, _ = _open(root)
    got = r.ok("GET", _tab(doc_id, "features/types"), client=None)
    assert [t["type"] for t in got["types"]] == before


def test_types_says_which_types_can_start_with_a_selection(root):
    r, doc_id, _ = _open(root)
    pid = _outputs(r, doc_id).parts[0].id

    def can(picks):
        q = urllib.parse.quote(json.dumps(picks))
        got = r.ok("GET", _tab(doc_id, f"features/types?picks={q}"))
        return {t["type"] for t in got["types"] if t["can_start"]}
    edge, region = can([_pick(pid, "edge", EDGES[0])]), can([_pick("F1", "region", 1)])
    assert "fillet" in edge and "chamfer" in edge and "shell" not in edge and "delete_face" not in edge
    assert "extrude" in region and "fillet" not in region
    assert can([]) == set(DSC.startable_feature_types())
    assert can([_pick(pid, "edge", EDGES[0], want=["edge"])]) == edge              # `want` is optional


def test_types_refuses_hostile_queries(root):
    r, doc_id, _ = _open(root)
    base = _tab(doc_id, "features/types")
    for q in ("picks=", "picks=notjson", "picks=%7B%7D", "picks=%5B1%5D", "picks=null",
              "picks=" + urllib.parse.quote("[" * 1500), "picks=" + urllib.parse.quote("[{}]"),
              "picks=" + urllib.parse.quote('[{"part": "F1", "kind": "region", "index": 0}]'),
              "picks=" + urllib.parse.quote('[{"part": "F1", "kind": "region", "index": 1, "evil": 1}]'),
              "picks=" + "x" * (SV.MAX_PICKS_QUERY + 1), "other=1", "picks=%5B%5D&picks=%5B%5D&other=1"):
        status, body = r.request("GET", f"{base}?{q}")
        assert status == 400 and isinstance(body, dict) and body["error"], (q[:60], status, body)
    status, body = r.request("GET", base + "?picks=" + urllib.parse.quote('[{"part": "F1", "kind": "region", "index": 99}]'))
    assert status == 400 and body["error"].startswith("picks[0].index")
    assert r.request("POST", base, {})[0] == 405
    assert r.request("GET", f"/documents/nope/tabs/T1/features/types")[0] == 404
    assert r.request("GET", f"/documents/{doc_id}/tabs/T9/features/types")[0] == 404
    assert r.request("GET", base, headers={"Host": "evil.example"})[0] == 421
    assert r.request("GET", base, headers={"Origin": "http://evil.example"})[0] == 403
    tab = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "assembly"}, expect=201)["tab"]["id"]
    assert r.request("GET", f"/documents/{doc_id}/tabs/{tab}/features/types")[0] == 400


def test_hostile_bodies_to_the_new_dialog_route_are_a_400_never_a_500(root):
    r, doc_id, _ = _open(root)
    path = _tab(doc_id, "features/dialog")

    def post(body, expect=400, headers=None):
        status, payload = r.request("POST", path, body, headers=headers)
        assert status == expect, (str(body)[:100], status, payload)
        return payload["error"] if isinstance(payload, dict) and "error" in payload else payload
    assert post({}).startswith("type:")
    for bad in (None, 5, True, [], {}, "", "x" * 65):
        assert post({"type": bad}).startswith("type:"), bad
    for bad in ("x", 5, None, {}, True):
        assert post({"type": "extrude", "picks": bad}).startswith("picks"), bad
    assert post({"type": "extrude", "extra": 1}).startswith("extra:")
    assert post({"type": "extrude", "picks": [1]}).startswith("picks[0]")
    assert post({"type": "extrude", "picks": [{}]}).startswith("picks[0]")
    assert post({"type": "extrude", "picks": [{"part": "F1", "kind": "region", "index": 1, "x": 1}]}).startswith("picks[0].x")
    assert post({"type": "extrude", "picks": [{"part": "F1", "kind": "region", "index": "1"}]}).startswith("picks[0].index")
    assert post({"type": "extrude", "picks": [{"part": "F1", "kind": "region", "index": 1, "want": "region"}]}) \
        .startswith("picks[0].want")
    assert post({"type": "extrude", "picks": [_pick("F1", "region", 1)] * 1000}).startswith("picks:")
    assert post({"type": "extrude", "picks": [{"part": "F1", "kind": "region", "index": 1, "point": [float("nan")] * 3}]}) \
        is not None
    for depth in (200, 5000, 300000):
        status, body = r.request("POST", path, raw=('{"picks": ' + "[" * depth + "]" * depth + "}").encode())
        assert status == 400 and body["error"], depth
    status, body = r.request("POST", path, raw=b"not json")
    assert status == 400 and body["error"].startswith("body is not valid JSON")
    assert post({"type": "extrude"}, headers={"Content-Type": "text/plain"}, expect=415) is not None
    status, body = r.request("POST", path, {"type": "extrude"}, client=None)         # a dialog needs a client id
    assert status == 400 and "X-Client-Id" in body["error"]
    assert r.request("GET", path)[0] == 405
    assert r.request("POST", f"/documents/nope/tabs/T1/features/dialog", {"type": "extrude"})[0] == 404
    assert r.request("POST", f"/documents/{doc_id}/tabs/T9/features/dialog", {"type": "extrude"})[0] == 404
    assert r.request("POST", path + "/extra", {"type": "extrude"})[0] == 404
    assert r.request("POST", path, {"type": "extrude"}, headers={"Host": "evil.example"})[0] == 421
    assert r.request("POST", path, {"type": "extrude"}, headers={"Origin": "http://evil.example"})[0] == 403
    tab = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "assembly"}, expect=201)["tab"]["id"]
    assert r.request("POST", f"/documents/{doc_id}/tabs/{tab}/features/dialog", {"type": "extrude"})[0] == 400
    for _ in range(SV.MAX_DIALOGS):
        _new(r, doc_id, "extrude")
    assert _new(r, doc_id, "extrude", expect=409)["error"]                           # the per-client cap holds here too
    assert r.request("GET", "/health")[0] == 200


# ---------------------------------------------------------------------- the engine requests


def test_every_type_with_a_dialog_starts_with_its_defaults(root):
    r, doc_id, _ = _open(root)
    for t in DSC.startable_feature_types():
        got = _new(r, doc_id, t)
        assert got["type"] == t and got["description"]["type"] == t, t
        r.ok("POST", _path(doc_id, got["dialog"], "cancel"))


def test_the_start_requests_are_queries_on_both_executors(tmp_path):
    for eng in (InProcessEngine(), ProcessEngine()):
        try:
            tab = eng.open_tab(os.path.join(BRACKET, "bracket.fshape"))
            before = eng.outputs(tab).revision
            pid = eng.outputs(tab).parts[0].id
            got = eng.start_feature(tab, "fillet", [_pick(pid, "edge", EDGES[0]), _pick(pid, "edge", EDGES[1])])
            assert set(got) == {"feature", "variables", "defaults", "fields", "params"}
            assert got["feature"]["type"] == "fillet" and "id" not in got["feature"] and got["feature"]["name"]
            assert got["feature"]["params"] == got["defaults"] and got["feature"]["params"]["radius"] == 2.0
            assert [k for k, _ in got["fields"]["edges"]] == ["edge", "edge"]
            json.dumps(got)                                                    # plain data: it crosses the process
            assert eng.start_feature(tab, "fillet", [])["fields"]["edges"] == []
            can = eng.startable(tab, [_pick(pid, "edge", EDGES[0])])
            assert can["fillet"] is True and can["shell"] is False and set(can) == set(DSC.startable_feature_types())
            assert all(eng.startable(tab, []).values())
            assert eng.outputs(tab).revision == before                         # queries: nothing moved
            with pytest.raises(BadPick):
                eng.start_feature(tab, "fillet", [_pick(pid, "edge", 9999)])
            with pytest.raises(BadPick):
                eng.startable(tab, [{"part": pid, "kind": "bogus"}])
            with pytest.raises(EngineError):
                eng.start_feature(tab, "sketch", [])
        finally:
            eng.close()
            P.SPARES.clear()


async def _next_of(ws, kind):
    """The next message of type `kind` on `ws`, skipping the others (hello, tabs)."""
    while True:
        msg = json.loads((await asyncio.wait_for(ws.receive(), 60)).data)
        if msg["type"] == kind:
            return msg
