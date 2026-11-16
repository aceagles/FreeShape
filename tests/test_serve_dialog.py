"""I5.3: a feature dialog on the server (POST .../features/<fid>/dialog, .../dialog/<did>/update|accept|cancel).
The shapes are in the serve.py docstring ("Dialogs"). Each test builds its own root and server, so each passes alone."""
import asyncio
import http.client
import json
import os
import shutil
import threading

import aiohttp
import pytest

from freeshape import doc_lock
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


class _Root:
    def __init__(self, tmp_path, client_ttl=SV.CLIENT_TTL):
        self.root = str(tmp_path / "Documents")
        self.data = str(tmp_path / "data")
        os.makedirs(self.root)
        self.registry = R.DocRegistry(os.path.join(self.data, R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=self.registry, root=self.root, client_ttl=client_ttl)
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

    def make(**kw):
        r = _Root(tmp_path, **kw)
        made.append(r)
        return r
    try:
        yield make
    finally:
        for r in made:
            r.close()


def _open(root, **kw):
    r = root(**kw)
    doc_id, folder = r.copy(BRACKET, "Doc")
    r.ok("POST", f"/documents/{doc_id}/open")
    return r, doc_id, folder


def _dlg(r, doc_id, fid="F2", client=A, expect=201):
    return r.ok("POST", f"/documents/{doc_id}/tabs/T1/features/{fid}/dialog", {}, expect=expect, client=client)


def _path(doc_id, did, verb):
    return f"/documents/{doc_id}/tabs/T1/dialog/{did}/{verb}"


def _update(r, doc_id, did, values, client=A, expect=200):
    status, body = r.request("POST", _path(doc_id, did, "update"), {"values": values}, client=client)
    assert status == expect, (values, status, body)
    return body


def _volume(r, doc_id):
    out = wire.from_wire(r.request("GET", f"/documents/{doc_id}/tabs/T1/outputs")[1])
    return round(sum(K.volume(K.shape_from_brep(p.brep)) for p in out.parts), 3)


def _revision(r, doc_id):
    status, body = r.request("GET", f"/documents/{doc_id}/tabs")
    assert status == 200
    return body["tabs"][0]["revision"]


def _field(view, key):
    return next(f for f in view["description"]["fields"] if f["key"] == key)


# ---------------------------------------------------------------------- open and update


def test_open_returns_the_depth_field_with_the_stored_value(root):
    r, doc_id, _ = _open(root)
    got = _dlg(r, doc_id)
    assert got["feature"] == "F2" and got["tab"] == "T1" and got["document"] == doc_id and got["active"] is None
    assert got["description"]["type"] == "extrude"
    assert _field(got, "depth")["kind"] == "num" and _field(got, "depth")["visible"] is True
    assert got["values"]["depth"] == {"value": 40.0, "text": "40"}
    assert got["values"]["end"] == {"value": "symmetric"}
    items = got["fields"]["profiles"]                          # a stored reference, in the refs route's item shape
    assert len(items) == 1 and items[0]["ok"] is True and items[0]["kind"] == "region"
    assert set(items[0]) == {"ok", "kind", "ref", "name"} and items[0]["ref"]["sketch"] == "F1"
    assert len(got["dialog"]) == 16


def test_an_expression_is_evaluated_and_a_bad_one_keeps_the_last_good_value(root):
    r, doc_id, _ = _open(root)
    did = _dlg(r, doc_id)["dialog"]
    got = _update(r, doc_id, did, {"depth": "2*5 mm"})
    assert got["errors"] == {} and got["values"]["depth"] == {"value": 10.0, "text": "2*5 mm"}
    got = _update(r, doc_id, did, {"depth": "2*"})
    assert list(got["errors"]) == ["depth"] and got["errors"]["depth"]
    assert got["values"]["depth"]["value"] == 10.0 and got["values"]["depth"]["error"] == got["errors"]["depth"]
    assert got["values"]["depth"]["text"] == "2*"                # what was typed stays shown
    assert _update(r, doc_id, did, {"depth": "1 in"})["values"]["depth"]["value"] == pytest.approx(25.4)
    got = _update(r, doc_id, did, {"depth": "#nope"})
    assert "nope" in got["errors"]["depth"] and got["values"]["depth"]["value"] == pytest.approx(25.4)
    assert "error" not in _update(r, doc_id, did, {"depth": 12})["values"]["depth"]
    assert "zero" in _update(r, doc_id, did, {"depth": "0"})["errors"]["depth"]      # signed: only zero is refused
    assert _update(r, doc_id, did, {"depth": "0"})["values"]["depth"]["value"] == 12.0


def test_a_good_key_next_to_a_bad_one_is_still_applied(root):
    r, doc_id, _ = _open(root)
    did = _dlg(r, doc_id)["dialog"]
    got = _update(r, doc_id, did, {"depth": "2*", "flip": True})
    assert list(got["errors"]) == ["depth"] and got["values"]["flip"] == {"value": True}
    assert got["values"]["depth"]["value"] == 40.0


def test_accept_changes_the_feature_and_bumps_the_revision_and_cancel_does_not(root):
    r, doc_id, _ = _open(root)
    before_volume, before_rev = _volume(r, doc_id), _revision(r, doc_id)
    cancelled = _dlg(r, doc_id)["dialog"]
    _update(r, doc_id, cancelled, {"depth": 10})
    assert r.ok("POST", _path(doc_id, cancelled, "cancel")) == {"cancelled": True}
    assert _revision(r, doc_id) == before_rev and _volume(r, doc_id) == before_volume
    r.ok("POST", _path(doc_id, cancelled, "update"), {"values": {"depth": 11}}, expect=404)     # it is gone
    did = _dlg(r, doc_id)["dialog"]
    _update(r, doc_id, did, {"depth": "2*5 mm"})
    done = r.ok("POST", _path(doc_id, did, "accept"))
    assert done["rebuilt"][0] == "T1" and done["revision"] == _revision(r, doc_id) != before_rev
    assert _volume(r, doc_id) < before_volume
    assert _dlg(r, doc_id)["values"]["depth"]["value"] == 10.0          # the stored feature holds the number
    r.ok("POST", _path(doc_id, did, "accept"), expect=404)              # closed by accept


def test_a_choice_changes_which_fields_are_visible(root):
    r, doc_id, _ = _open(root)
    got = _dlg(r, doc_id)
    did = got["dialog"]
    assert _field(got, "depth")["visible"] is True and _field(got, "both")["visible"] is False
    got = _update(r, doc_id, did, {"end": "through"})
    assert _field(got, "depth")["visible"] is False and _field(got, "both")["visible"] is True
    assert got["values"]["end"] == {"value": "through"} and got["active"] is None
    got = _update(r, doc_id, did, {"end": "blind", "second": True})
    assert _field(got, "depth")["visible"] is True and _field(got, "depth2")["visible"] is True


def test_up_to_face_names_its_pick_field_as_the_one_to_activate(root):
    r, doc_id, _ = _open(root)
    did = _dlg(r, doc_id)["dialog"]
    got = _update(r, doc_id, did, {"end": "up_to_face"})
    assert got["active"] == "upto_face" and _field(got, "upto_face")["visible"] is True
    assert got["fields"]["upto_face"] == [] and _field(got, "depth")["visible"] is False
    assert _update(r, doc_id, did, {"flip": True})["active"] is None
    # the params on_field_set sets come back too: Thin seeds its wall thicknesses, Draft its angle
    got = _update(r, doc_id, did, {"end": "blind", "draft": True})
    assert got["values"]["draft_angle"]["value"] == 5.0


def test_a_selection_field_can_be_emptied_and_refilled_with_stored_items(root):
    r, doc_id, _ = _open(root)
    opened = _dlg(r, doc_id)
    did, item = opened["dialog"], opened["fields"]["profiles"][0]
    assert _update(r, doc_id, did, {"profiles": []})["fields"]["profiles"] == []
    got = _update(r, doc_id, did, {"profiles": [{"kind": item["kind"], "ref": item["ref"]}]})
    assert got["fields"]["profiles"] == [item]
    done_id = r.ok("POST", _path(doc_id, did, "accept"))
    assert done_id["rebuilt"]


# ---------------------------------------------------------------------- whose dialog, and for how long


def test_a_dialog_belongs_to_the_client_that_opened_it(root):
    r, doc_id, _ = _open(root)
    did = _dlg(r, doc_id, client=A)["dialog"]
    for verb, body in (("update", {"values": {"depth": 5}}), ("accept", {}), ("cancel", {})):
        status, payload = r.request("POST", _path(doc_id, did, verb), body, client=B)
        assert status == 404, (verb, payload)
    assert _update(r, doc_id, did, {"depth": 5}, client=A)["values"]["depth"]["value"] == 5.0
    status, payload = r.request("POST", _path(doc_id, did, "update"), {"values": {"depth": 5}}, client=None)
    assert status == 400 and "X-Client-Id" in payload["error"]
    status, _ = r.request("POST", f"/documents/{doc_id}/tabs/T1/features/F2/dialog", {}, client="bad id!")
    assert status == 400


def test_a_dialog_survives_the_socket_closing_and_reconnecting_with_the_same_client_id(root):
    r, doc_id, _ = _open(root)

    async def go():
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(f"{r.base}/ws?client={A}&document={doc_id}") as ws:
                await asyncio.wait_for(ws.receive(), 30)
                did = (await asyncio.get_running_loop().run_in_executor(None, _dlg, r, doc_id))["dialog"]
            async with s.ws_connect(f"{r.base}/ws?client={A}&document={doc_id}") as ws:
                await asyncio.wait_for(ws.receive(), 30)
                got = await asyncio.get_running_loop().run_in_executor(None, _update, r, doc_id, did, {"depth": 7})
                assert got["values"]["depth"]["value"] == 7.0
    asyncio.run(asyncio.wait_for(go(), 90))


def test_a_dialog_goes_when_its_registry_entry_expires(root):
    r, doc_id, _ = _open(root, client_ttl=0.2)

    def wait_gone():
        did = _dlg(r, doc_id)["dialog"]                    # no socket of A: its entry expires 0.2 s after the open
        for _ in range(600):
            status, _ = r.request("POST", _path(doc_id, did, "update"), {"values": {"depth": 5}})
            if status == 404:
                return did
            assert status == 200
            threading.Event().wait(0.1)
        pytest.fail("the dialog outlived its registry entry")

    async def go():
        async with aiohttp.ClientSession() as s:           # B holds the Document open (an unwatched one idle-closes)
            async with s.ws_connect(f"{r.base}/ws?client={B}&document={doc_id}") as ws:
                await asyncio.wait_for(ws.receive(), 30)
                did = await asyncio.get_running_loop().run_in_executor(None, wait_gone)
                assert (await asyncio.get_running_loop().run_in_executor(None, _dlg, r, doc_id))["dialog"] != did
    asyncio.run(asyncio.wait_for(go(), 120))


def test_closing_the_document_drops_its_dialogs_and_a_client_holds_a_few(root):
    r, doc_id, _ = _open(root)
    ids = [_dlg(r, doc_id)["dialog"] for _ in range(SV.MAX_DIALOGS)]
    assert "at most" in r.request("POST", f"/documents/{doc_id}/tabs/T1/features/F2/dialog", {})[1]["error"]
    assert _dlg(r, doc_id, client=B)["dialog"]             # another client has its own room
    r.ok("POST", _path(doc_id, ids[0], "cancel"))
    assert _dlg(r, doc_id)["dialog"]                       # a cancel frees one
    r.ok("POST", f"/documents/{doc_id}/close", {})
    r.ok("POST", f"/documents/{doc_id}/open")
    assert r.request("POST", _path(doc_id, ids[1], "update"), {"values": {"depth": 5}})[0] == 404


# ---------------------------------------------------------------------- read-only, unknown things


def test_a_read_only_document_opens_a_dialog_but_refuses_accept(root):
    r = root()
    doc_id, folder = r.copy(BRACKET, "Doc")
    doc_lock.acquire(folder, "desktop")
    assert r.ok("POST", f"/documents/{doc_id}/open")["read_only"] is True
    did = _dlg(r, doc_id)["dialog"]
    assert _update(r, doc_id, did, {"depth": 5})["values"]["depth"]["value"] == 5.0
    before = _revision(r, doc_id)
    status, body = r.request("POST", _path(doc_id, did, "accept"), {})
    assert status == 423 and body["holder"]["program"] == "desktop"
    assert _revision(r, doc_id) == before
    assert _update(r, doc_id, did, {"depth": 6})["values"]["depth"]["value"] == 6.0      # still open


def test_unknown_document_tab_feature_and_method(root):
    r, doc_id, _ = _open(root)
    good = f"/documents/{doc_id}/tabs/T1/features/F2/dialog"
    assert r.request("GET", good)[0] == 405
    assert r.request("POST", f"/documents/nope/tabs/T1/features/F2/dialog", {})[0] == 404
    assert r.request("POST", f"/documents/{doc_id}/tabs/T9/features/F2/dialog", {})[0] == 404
    status, body = r.request("POST", f"/documents/{doc_id}/tabs/T1/features/F99/dialog", {})
    assert status == 400 and "F99" in body["error"]
    assert r.request("POST", f"/documents/{doc_id}/tabs/T1/features/F2/dialog/extra", {})[0] == 404
    assert r.request("POST", f"/documents/{doc_id}/tabs/T1/dialog/{'0' * 16}/update", {"values": {"depth": 1}})[0] == 404
    assert r.request("POST", f"/documents/{doc_id}/tabs/T1/dialog/..%2f/update", {})[0] == 404
    assert r.request("POST", f"/documents/{doc_id}/tabs/T1/dialog/{'0' * 15}/update", {})[0] == 404
    # a tab that is not a Part Studio has no features
    tab = r.ok("POST", f"/documents/{doc_id}/tabs", {"kind": "assembly"}, expect=201)["tab"]["id"]
    status, body = r.request("POST", f"/documents/{doc_id}/tabs/{tab}/features/F1/dialog", {})
    assert status == 400, body


def test_host_origin_and_body_rules_hold(root):
    r, doc_id, _ = _open(root)
    path = f"/documents/{doc_id}/tabs/T1/features/F2/dialog"
    assert r.request("POST", path, {}, headers={"Host": "evil.example"})[0] == 421
    assert r.request("POST", path, {}, headers={"Origin": "http://evil.example"})[0] == 403
    assert r.request("POST", path, {}, headers={"Content-Type": "text/plain"})[0] == 415


def test_hostile_bodies_are_a_400_never_a_500(root):
    r, doc_id, _ = _open(root)
    did = _dlg(r, doc_id)["dialog"]
    path = _path(doc_id, did, "update")

    def raw(text, expect=400):
        status, body = r.request("POST", path, raw=text.encode() if isinstance(text, str) else text)
        assert status == expect, (text[:80], status, body)
        assert isinstance(body, dict) and body["error"], body
        return body["error"]

    def post(body, expect=400):
        status, payload = r.request("POST", path, body)
        assert status == expect, (str(body)[:100], status, payload)
        return payload["error"]
    assert post({}).startswith("values:") and post({"values": None}).startswith("values:")
    assert post({"values": {}}).startswith("values:") and post({"values": []}).startswith("values:")
    assert post({"values": "depth"}).startswith("values:")
    assert post({"values": {f"k{i}": 1 for i in range(65)}}).startswith("values:")
    assert post({"values": {"depth": 5}, "extra": 1}).startswith("extra:")
    assert post({"values": {"nope": 5}}).startswith("values.nope")
    assert post({"values": {"x" * 5000: 5}}).startswith("values.xxx")
    assert post({"values": {"depth": 5, "nope": 5}}).startswith("values.nope")
    for bad in (None, True, [1], {"a": 1}, "9" * 600):
        assert post({"values": {"depth": bad}}).startswith("values.depth"), bad
    for key, bad in (("flip", 1), ("flip", "true"), ("flip", None), ("end", "nope"), ("end", ["blind"]), ("end", {}),
                     ("end", 1), ("op", True), ("profiles", "x"), ("profiles", [1]), ("profiles", [{"kind": "x"}]),
                     ("profiles", [{"kind": 5, "ref": 1}]), ("profiles", [{"kind": "x", "ref": 1, "evil": 1}]),
                     ("profiles", [{"kind": "k" * 50, "ref": 1}]), ("profiles", [{"kind": "face", "ref": 1}] * 201)):
        assert post({"values": {key: bad}}).startswith(f"values.{key}"), (key, bad)
    assert post({"values": {"info": "x"}}) is not None          # no row of that name, or one that is read only
    # none of that changed the dialog
    assert _update(r, doc_id, did, {"flip": False})["values"]["depth"]["value"] == 40.0
    # nested far past the parser's depth, not JSON, not an object, not UTF-8, empty
    for depth in (200, 5000, 300000):
        assert raw('{"values": ' + "[" * depth + "]" * depth + "}") is not None
        assert raw('{"values": {"depth": ' + "[" * depth + "1" + "]" * depth + "}}") is not None
    assert raw('{"values": ' + "{" * 200000).startswith("body is not valid JSON")
    assert raw("[1, 2]").startswith("body must be") and raw("not json").startswith("body is not valid JSON")
    assert raw(b'{"values": [\xff\xfe]}').startswith("body is not valid JSON") and raw("").startswith("body")
    big = json.dumps({"values": {"depth": "1" * (SV.MAX_BODY + 10)}})
    assert raw(big, expect=413)
    # numbers JSON allows and a finite check refuses
    for lit in ("NaN", "Infinity", "-Infinity", "1e999"):
        got = r.request("POST", path, raw=('{"values": {"depth": %s}}' % lit).encode())
        assert got[0] in (200, 400), got
        if got[0] == 200:
            assert got[1]["errors"]["depth"]
    # the open and the verbs take no body fields either
    for verb in ("accept", "cancel"):
        status, body = r.request("POST", _path(doc_id, did, verb), {"x": 1})
        assert status == 400 and body["error"].startswith("x:")
    status, body = r.request("POST", f"/documents/{doc_id}/tabs/T1/features/F2/dialog", {"x": 1})
    assert status == 400 and body["error"].startswith("x:")
    assert r.request("GET", "/health")[0] == 200


# ---------------------------------------------------------------------- the engine request


def _engines(tmp_path):
    yield InProcessEngine()
    yield ProcessEngine()


def test_the_feature_request_is_a_query_on_both_executors(tmp_path):
    for eng in _engines(tmp_path):
        try:
            tab = eng.open_tab(BRACKET_FILE)
            before = eng.outputs(tab).revision
            got = eng.feature(tab, "F2")
            assert got["feature"]["id"] == "F2" and got["feature"]["params"]["depth"] == 40.0
            assert set(got) == {"feature", "variables", "defaults"} and got["defaults"]["depth"] == 25.0
            got["feature"]["params"]["depth"] = 1                  # a copy: the tab is not changed by it
            assert eng.feature(tab, "F2")["feature"]["params"]["depth"] == 40.0
            assert eng.outputs(tab).revision == before
            with pytest.raises(EngineError):
                eng.feature(tab, "nope")
        finally:
            eng.close()
            P.SPARES.clear()
