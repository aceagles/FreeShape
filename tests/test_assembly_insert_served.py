"""I10.1: `insert` is an Assembly edit: parts of a Part Studio tab of this Document become instances in one undo
step, placed as the desktop's Insert panel places them (AssemblyDoc.insert_parts). Each test builds its own root,
registry and server, so each passes alone."""
import http.client
import json
import os
import shutil
import threading

import numpy as np

from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.engine import wire

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HINGE = os.path.join(ROOT, "examples", "documents", "hinge")


class _Served:
    """A server over a root holding one copy of the example hinge Document (opened), plus a new empty Assembly tab."""

    def __init__(self, tmp_path):
        self.root = str(tmp_path / "Documents")
        os.makedirs(self.root)
        registry = R.DocRegistry(os.path.join(str(tmp_path / "data"), R.REGISTRY_NAME))
        self.path = os.path.join(self.root, "Hinge")
        shutil.copytree(HINGE, self.path)
        with open(os.path.join(self.path, "document.json")) as fh:
            self.doc = json.load(fh)["id"]
        registry.register_created(self.doc, self.path)
        self.server = SV.make_server(None, 0, registry=registry, root=self.root)
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)
        self.ok("POST", f"/documents/{self.doc}/open")
        made = self.ok("POST", f"/documents/{self.doc}/tabs", {"kind": "assembly", "name": "New"}, expect=201)
        self.asm = made["tab"]["id"]
        self.tab = f"/documents/{self.doc}/tabs/{self.asm}"

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

    def ok(self, method, path, body=None, expect=200):
        status, payload = self.request(method, path, body)
        assert status == expect, (method, path, status, payload)
        return payload

    def edit(self, body, tab=None):
        return self.ok("POST", f"{tab or self.tab}/edit", body)

    def refused(self, body):
        status, payload = self.request("POST", f"{self.tab}/edit", body)
        assert 400 <= status < 500, (status, payload)
        return payload

    def outputs(self, tab=None):
        status, body = self.request("GET", f"{tab or self.tab}/outputs")
        assert status == 200, body
        return wire.from_wire(body)

    def parts(self):
        """{part name: part id} of the Part Studio tab T1."""
        out = self.outputs(f"/documents/{self.doc}/tabs/T1")
        return {p.name: p.id for p in out.parts}

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()


def _box(out, iid):
    """(lo, hi) of an instance's world bounding box, from the vertices of its mesh."""
    inst = next(i for i in out.instances if i.id == iid)
    pts = np.asarray(out.parts[inst.part].mesh.verts, float)
    T = np.asarray(inst.T, float)
    w = pts @ T[:3, :3].T + T[:3, 3]
    return w.min(axis=0), w.max(axis=0)


def _overlap(a, b):
    return bool(np.all(a[0] < b[1] - 1e-6) and np.all(b[0] < a[1] - 1e-6))


def test_insert_a_part_makes_a_fixed_first_instance_and_a_free_second_one_beside_it(tmp_path):
    s = _Served(tmp_path)
    try:
        ids = s.parts()
        s.edit({"op": "insert", "tab": "T1", "part": ids["Base plate"]})
        out = s.outputs()
        assert len(out.instances) == 1 and out.instances[0].fixed
        assert np.allclose(out.instances[0].T, np.eye(4))
        assert out.instances[0].source == {"kind": "studio", "tab": "T1", "part": ids["Base plate"],
                                           "studio_name": "hinge_parts"}
        s.edit({"op": "insert", "tab": "T1", "part": ids["Arm"]})
        out = s.outputs()
        base, arm = out.instances
        assert base.fixed and not arm.fixed
        assert not _overlap(_box(out, base.id), _box(out, arm.id))
        s.edit({"op": "undo"})
        out = s.outputs()
        assert [i.id for i in out.instances] == [base.id]
    finally:
        s.close()


def test_insert_with_no_part_inserts_every_part_in_one_undo_step_keeping_their_places(tmp_path):
    s = _Served(tmp_path)
    try:
        ids = s.parts()
        s.edit({"op": "insert", "tab": "T1"})
        out = s.outputs()
        assert len(out.instances) == len(ids) == 3
        assert [i.fixed for i in out.instances] == [True, False, False]
        assert all(np.allclose(i.T, np.eye(4)) for i in out.instances)       # in the studio's own places
        first = _box(out, out.instances[0].id)
        s.edit({"op": "undo"})
        assert s.outputs().instances == []
        s.edit({"op": "redo"})
        # a second whole insert goes beside, all three by one shift, so they still sit as they did in the studio
        s.edit({"op": "insert", "tab": "T1"})
        out = s.outputs()
        assert len(out.instances) == 6
        shifts = [np.asarray(i.T, float)[:3, 3] for i in out.instances[3:]]
        assert all(np.allclose(sh, shifts[0]) for sh in shifts) and shifts[0][0] > 0
        assert not _overlap(first, _box(out, out.instances[3].id))
        s.edit({"op": "undo"})
        assert len(s.outputs().instances) == 3
    finally:
        s.close()


def test_insert_at_a_given_placement(tmp_path):
    s = _Served(tmp_path)
    try:
        ids = s.parts()
        T = np.eye(4)
        T[:3, 3] = (30, 0, 0)
        s.edit({"op": "insert", "tab": "T1", "part": ids["Arm"], "T": T.tolist()})
        out = s.outputs()
        assert np.allclose(out.instances[0].T, T)
        assert "4x4" in json.dumps(s.refused({"op": "insert", "tab": "T1", "part": ids["Arm"], "T": [[1, 0, 0]]}))
        assert len(s.outputs().instances) == 1
    finally:
        s.close()


def _assert_refused(s, body, text):
    before = s.outputs()
    got = s.refused(body)
    assert text in json.dumps(got), got
    after = s.outputs()
    assert [i.id for i in after.instances] == [i.id for i in before.instances] and after.revision == before.revision
    s.edit({"op": "insert", "tab": "T1", "part": next(iter(s.parts().values()))})   # nothing was left half done
    s.edit({"op": "undo"})
    assert [i.id for i in s.outputs().instances] == [i.id for i in before.instances]


def test_an_assembly_tab_is_refused_with_a_message_and_changes_nothing(tmp_path):
    s = _Served(tmp_path)
    try:
        _assert_refused(s, {"op": "insert", "tab": "T2"}, "not a Part Studio")
        _assert_refused(s, {"op": "insert", "tab": s.asm}, "not a Part Studio")
    finally:
        s.close()


def test_a_tab_of_another_document_is_refused_with_a_message_and_changes_nothing(tmp_path):
    s = _Served(tmp_path)
    try:
        _assert_refused(s, {"op": "insert", "tab": "T99"}, "not a Part Studio")
        _assert_refused(s, {"op": "insert", "tab": None}, "tab")
    finally:
        s.close()


def test_an_unknown_part_is_refused_with_a_message_and_changes_nothing(tmp_path):
    s = _Served(tmp_path)
    try:
        _assert_refused(s, {"op": "insert", "tab": "T1", "part": "F99/1"}, "no part")
        _assert_refused(s, {"op": "insert", "tab": "T1", "part": 7}, "no part")
    finally:
        s.close()


def test_a_saved_document_opens_in_the_desktop_class_and_the_instances_resolve_to_the_same_parts(tmp_path):
    s = _Served(tmp_path)
    try:
        ids = s.parts()
        s.edit({"op": "insert", "tab": "T1", "part": ids["Base plate"]})
        s.edit({"op": "insert", "tab": "T1", "part": ids["Arm"]})
        served = s.outputs()
        s.ok("POST", f"/documents/{s.doc}/save")
    finally:
        s.close()
    folder = DF.FolderDocument.open(s.path)
    try:
        assert not folder.errors, folder.errors
        asm = folder.tab(s.asm).doc
        asm.refresh(solve=False, notify=False)
        assert [i["part_name"] for i in asm.instances] == ["Base plate", "Arm"]
        assert [i["source"] for i in asm.instances] == [
            {"kind": "studio", "tab": "T1", "part": ids["Base plate"], "studio_name": "hinge_parts"},
            {"kind": "studio", "tab": "T1", "part": ids["Arm"], "studio_name": "hinge_parts"}]
        assert not asm.errors, asm.errors
        assert [i["fixed"] for i in asm.instances] == [True, False]
        for inst, got in zip(asm.instances, served.instances):
            assert np.allclose(inst["T"], got.T)
            lo, hi = K.bbox(asm.world_shape(inst["id"]))
            glo, ghi = _box(served, got.id)
            assert np.allclose(lo, glo, atol=0.05) and np.allclose(hi, ghi, atol=0.05)
    finally:
        folder.close()
