"""I10.5: the mate dialog routes (POST .../tabs/<tab>/mates/dialog, .../mates/dialog/<did>/update|accept|cancel).
The shapes are in the serve.py docstring ("Mate dialogs"). Each test builds its own root and server on its own copy of
the hinge example, so each passes alone by node id."""
import http.client
import json
import os
import shutil
import threading

import numpy as np
import pytest

from freeshape import doc_registry as R
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.engine import wire

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HINGE = os.path.join(ROOT, "examples", "documents", "hinge")
A = "client-aaaa1111"
B = "client-bbbb2222"
ASM = "T2"
PLATE_EDGE = {"instance": "I1", "kind": "edge", "index": 15}     # 1-based: the circular pin edges of the hinge
ARM_EDGE = {"instance": "I2", "kind": "edge", "index": 14}


class _Root:
    def __init__(self, tmp_path):
        self.root = str(tmp_path / "Documents")
        self.data = str(tmp_path / "data")
        os.makedirs(self.root)
        self.registry = R.DocRegistry(os.path.join(self.data, R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=self.registry, root=self.root)
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)

    def copy(self, source, name):
        path = os.path.join(self.root, name)
        shutil.copytree(source, path)
        with open(os.path.join(path, "document.json")) as fh:
            doc_id = json.load(fh)["id"]
        self.registry.register_created(doc_id, path)
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
def served(tmp_path):
    """A server with a copy of the hinge open, its mates deleted: (root, Document id)."""
    r = _Root(tmp_path)
    try:
        doc_id = r.copy(HINGE, "Hinge")
        r.ok("POST", f"/documents/{doc_id}/open")
        mates = [m.id for m in _outputs(r, doc_id).mates]
        r.ok("POST", f"/documents/{doc_id}/tabs/{ASM}/edit", {"op": "delete_mates", "ids": mates})
        yield r, doc_id
    finally:
        r.close()


def _outputs(r, doc_id):
    status, data = r.request("GET", f"/documents/{doc_id}/tabs/{ASM}/outputs")
    assert status == 200, data
    return wire.from_wire(data)


def _mates(doc_id, did=None, verb=None):
    base = f"/documents/{doc_id}/tabs/{ASM}/mates/dialog"
    return base if did is None else f"{base}/{did}/{verb}"


def test_open_update_and_accept_over_http_put_the_mate_in_the_outputs(served):
    r, doc_id = served
    got = r.ok("POST", _mates(doc_id), {"type": "revolute", "picks": [PLATE_EDGE]}, expect=201)
    did = got["dialog"]
    assert got["document"] == doc_id and got["tab"] == ASM and got["new"] and got["type"] == "revolute"
    assert got["a"]["instance"] == "I1" and got["a"]["index"] == 15 and got["b"] is None
    assert set(got["placements"]) == {"I1", "I2", "I3"}
    got = r.ok("POST", _mates(doc_id, did, "update"), {"b": ARM_EDGE, "offset": 2.0})
    assert got["error"] is None and got["b"]["instance"] == "I2" and got["offset"] == 2.0
    assert [v["dof"] for v in got["values"]] == ["rz"]
    preview_arm = np.array(got["placements"]["I2"])
    assert _outputs(r, doc_id).mates == []                 # an update is a preview: nothing is stored
    acc = r.ok("POST", _mates(doc_id, did, "accept"))
    assert acc["mate"] == got["mate"] and ASM in acc["rebuilt"]
    out = _outputs(r, doc_id)
    m = next(m for m in out.mates if m.id == acc["mate"])
    assert m.type == "revolute" and m.params["offset"] == 2.0 and m.a.instance == "I1" and m.b.instance == "I2"
    assert np.allclose(np.array(next(i.T for i in out.instances if i.id == "I2")), preview_arm, atol=1e-6)
    # the dialog closed with the accept
    assert r.request("POST", _mates(doc_id, did, "update"), {})[0] == 404


def test_another_client_cannot_update_or_accept_the_session(served):
    r, doc_id = served
    did = r.ok("POST", _mates(doc_id), {"type": "revolute", "picks": [PLATE_EDGE, ARM_EDGE]}, expect=201)["dialog"]
    for verb in ("update", "accept", "cancel"):
        assert r.request("POST", _mates(doc_id, did, verb), {}, client=B)[0] == 404, verb
    assert r.request("POST", _mates(doc_id, did, "update"), {}, client=None)[0] == 400
    assert r.ok("POST", _mates(doc_id, did, "update"), {"flip": True})["flip"] is True


def test_cancel_changes_nothing_and_closes_the_dialog(served):
    r, doc_id = served
    before = _outputs(r, doc_id)
    did = r.ok("POST", _mates(doc_id), {"type": "fastened", "picks": [PLATE_EDGE, ARM_EDGE]}, expect=201)["dialog"]
    r.ok("POST", _mates(doc_id, did, "update"), {"turn90": True})
    assert r.ok("POST", _mates(doc_id, did, "cancel")) == {"cancelled": True}
    after = _outputs(r, doc_id)
    assert after.mates == [] and after.revision == before.revision
    for a, b in zip(before.instances, after.instances):
        assert a.id == b.id and np.allclose(np.array(a.T), np.array(b.T))
    assert r.request("POST", _mates(doc_id, did, "update"), {})[0] == 404


def test_refusals_carry_the_desktops_messages(served):
    r, doc_id = served
    did = r.ok("POST", _mates(doc_id), {"type": "revolute", "picks": [PLATE_EDGE]}, expect=201)["dialog"]
    got = r.ok("POST", _mates(doc_id, did, "update"), {"b": dict(PLATE_EDGE, index=14)})
    assert got["error"] == "Pick the second connector on a different part." and got["b"] is None
    status, body = r.request("POST", _mates(doc_id, did, "accept"))
    assert status == 400 and "Pick a mate connector on each of the two parts first." in body["error"]
    status, body = r.request("POST", _mates(doc_id, did, "update"), {"colour": "red"})
    assert status == 400 and body["error"] == "colour: unknown field"
    status, body = r.request("POST", _mates(doc_id), {"type": "glue"})
    assert status == 400 and "type" in body["error"]
    status, body = r.request("POST", f"/documents/{doc_id}/tabs/T1/mates/dialog", {"type": "revolute"})
    assert status == 400 and "Assembly" in body["error"]


def test_an_existing_mate_is_edited_in_place(tmp_path):
    r = _Root(tmp_path)
    try:
        doc_id = r.copy(HINGE, "Hinge")
        r.ok("POST", f"/documents/{doc_id}/open")
        before = _outputs(r, doc_id)
        rev = next(m for m in before.mates if m.type == "revolute")
        got = r.ok("POST", _mates(doc_id), {"mate": rev.id}, expect=201)
        assert got["mate"] == rev.id and not got["new"]
        r.ok("POST", _mates(doc_id, got["dialog"], "update"), {"offset": 3.0})
        assert r.ok("POST", _mates(doc_id, got["dialog"], "accept"))["mate"] == rev.id
        after = _outputs(r, doc_id)
        assert [m.id for m in after.mates] == [m.id for m in before.mates]
        assert next(m for m in after.mates if m.id == rev.id).params["offset"] == 3.0
    finally:
        r.close()
