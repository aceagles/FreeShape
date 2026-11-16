"""F17: a move_to over `freeshape serve` answers with the placements it changed, so the Browser client can draw each
solved step of a drag from the edit's reply instead of fetching and rebuilding the whole Assembly. Each test builds
its own root, registry and server, so each passes alone."""
import http.client
import json
import os
import shutil
import threading

import numpy as np

from freeshape import doc_registry as R
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.engine import wire

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HINGE = os.path.join(ROOT, "examples", "documents", "hinge")


class _Hinge:
    """A server over a root holding one copy of the example hinge, opened for editing."""

    def __init__(self, tmp_path):
        root = str(tmp_path / "Documents")
        os.makedirs(root)
        registry = R.DocRegistry(os.path.join(str(tmp_path / "data"), R.REGISTRY_NAME))
        path = os.path.join(root, "Hinge")
        shutil.copytree(HINGE, path)
        with open(os.path.join(path, "document.json")) as fh:
            self.doc = json.load(fh)["id"]
        registry.register_created(self.doc, path)
        self.server = SV.make_server(None, 0, registry=registry, root=root)
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)
        self.ok("POST", f"/documents/{self.doc}/open")
        self.tab = f"/documents/{self.doc}/tabs/T2"

    def request(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=90)
        try:
            data = json.dumps(body if body is not None else {}).encode() if method == "POST" else None
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            c.putheader("Host", f"127.0.0.1:{self.port}")
            if data is not None:
                c.putheader("Content-Type", "application/json")
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            payload = r.read()
            if r.getheader("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return r.status, payload
        finally:
            c.close()

    def ok(self, method, path, body=None):
        status, payload = self.request(method, path, body)
        assert status == 200, (method, path, status, payload)
        return payload

    def edit(self, body):
        return self.ok("POST", f"{self.tab}/edit", body)

    def outputs(self):
        status, body = self.request("GET", f"{self.tab}/outputs")
        assert status == 200
        return wire.from_wire(body)

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()


def _turn_about_pin(out, deg):
    m = next(m for m in out.mates if m.type == "revolute")
    o, z = np.array(m.a.world["o"], float), np.array(m.a.world["z"], float)
    a = z / np.linalg.norm(z)
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    t = np.radians(deg)
    r = np.eye(3) + np.sin(t) * k + (1 - np.cos(t)) * (k @ k)
    T = np.eye(4)
    T[:3, :3] = r
    T[:3, 3] = o - r @ o
    return T.tolist()


def test_move_to_replies_with_the_placements_it_changed_and_no_others(tmp_path):
    h = _Hinge(tmp_path)
    try:
        out = h.outputs()
        h.edit({"op": "move_begin", "instance": "I2"})
        got = h.edit({"op": "move_to", "T": _turn_about_pin(out, 20)})
        # the arm turns about the pin; the fixed base and the pin stay where they are, so only the arm is in the reply
        assert sorted(got["placements"]) == ["I2"], got
        served = {i.id: np.array(i.T, float) for i in h.outputs().instances}
        assert np.allclose(np.array(got["placements"]["I2"], float), served["I2"], atol=1e-12)
        # the same motion again changes nothing: nothing to send
        again = h.edit({"op": "move_to", "T": _turn_about_pin(out, 20)})
        assert again["placements"] == {}
        h.edit({"op": "move_end"})
    finally:
        h.close()


def test_other_edits_reply_with_no_placements(tmp_path):
    h = _Hinge(tmp_path)
    try:
        got = h.edit({"op": "move_begin", "instance": "I2"})
        assert "placements" not in got and got["rebuilt"][0] == "T2"
        assert "placements" not in h.edit({"op": "move_end"})
        assert "placements" not in h.edit({"op": "set_hidden", "instance": "I3", "value": True})
    finally:
        h.close()
