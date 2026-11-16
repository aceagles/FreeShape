"""I9.5: GET .../tabs/<tab>/assembly/types answers the mate and relation types of freeshape/assembly.py.
Each test builds its own root and server, so each passes alone."""
import http.client
import json
import os
import shutil
import threading

import pytest

from freeshape import assembly as ASM
from freeshape import doc_registry as R
from freeshape import serve as SV
from freeshape.engine import process as P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HINGE = os.path.join(ROOT, "examples", "documents", "hinge")


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

    def request(self, method, path, body=None):
        h = {"Host": f"127.0.0.1:{self.port}"}
        if method == "POST":
            h["Content-Type"] = "application/json"
        data = json.dumps(body or {}).encode() if method == "POST" else None
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=90)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if data is not None:
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            payload = json.loads(r.read())
            return r.status, payload
        finally:
            c.close()

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()


@pytest.fixture
def served(tmp_path):
    r = _Root(tmp_path)
    try:
        doc_id = r.copy(HINGE, "Hinge")
        status, _ = r.request("POST", f"/documents/{doc_id}/open")
        assert status == 200
        status, tabs = r.request("GET", f"/documents/{doc_id}/tabs")
        assert status == 200
        yield r, doc_id, tabs["tabs"]
    finally:
        r.close()


def _tab_of(tabs, kind):
    return next(t["id"] for t in tabs if t["kind"] == kind)


def test_types_lists_mates_and_relations_in_module_order(served):
    r, doc_id, tabs = served
    tab = _tab_of(tabs, "assembly")
    status, body = r.request("GET", f"/documents/{doc_id}/tabs/{tab}/assembly/types")
    assert status == 200
    assert body["document"] == doc_id and body["tab"] == tab
    assert [m["id"] for m in body["mates"]] == ["fastened", "revolute", "slider", "cylindrical", "planar", "pin_slot", "parallel", "ball"]
    assert [m["id"] for m in body["mates"]] == list(ASM.MATE_TYPES)
    for m in body["mates"]:
        label, tip = ASM.MATE_TYPES[m["id"]]
        assert (m["label"], m["help"]) == (label, tip)
        assert m["freedoms"] == list(ASM.MATE_VALUE_DOFS[m["id"]])
    by_id = {m["id"]: m for m in body["mates"]}
    assert by_id["fastened"]["freedoms"] == [] and by_id["planar"]["freedoms"] == ["tx", "ty", "rz"]
    assert [x["id"] for x in body["relations"]] == ["linear", "gear", "rack_pinion", "screw"]
    for x in body["relations"]:
        assert x == {"id": x["id"], "label": ASM.RELATION_TYPES[x["id"]]["label"], "help": ASM.RELATION_TYPES[x["id"]]["help"]}


def test_part_studio_tab_is_a_400_naming_its_kind(served):
    r, doc_id, tabs = served
    tab = _tab_of(tabs, "studio")
    status, body = r.request("GET", f"/documents/{doc_id}/tabs/{tab}/assembly/types")
    assert status == 400
    assert "studio" in body["error"]


def test_unknown_tab_is_404_and_post_is_refused(served):
    r, doc_id, tabs = served
    status, _ = r.request("GET", f"/documents/{doc_id}/tabs/nope/assembly/types")
    assert status == 404
    tab = _tab_of(tabs, "assembly")
    status, _ = r.request("POST", f"/documents/{doc_id}/tabs/{tab}/assembly/types")
    assert status in (404, 405)
