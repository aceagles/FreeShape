"""I11.6: a STEP file into an Assembly, served. POST .../tabs/<assembly>/import (the bytes, or {root, entry}) makes one
`insert_step` edit: one instance per solid, in one undo step, placed as the desktop's Insert from file places them
(AssemblyDoc.insert_step_parts). Each test builds its own root, registry and server, so each passes alone."""
import base64
import hashlib
import http.client
import json
import os
import shutil
import threading
import urllib.parse

import numpy as np
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Pnt

from freeshape import assembly as A
from freeshape import doc_folder as DF
from freeshape import doc_lock
from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.engine import wire

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HINGE = os.path.join(ROOT, "examples", "documents", "hinge")
OCTET = "application/octet-stream"


def _box(x, y, z, at=(0, 0, 0)):
    return BRepPrimAPI_MakeBox(gp_Pnt(*at), float(x), float(y), float(z)).Shape()


def _step(tmp_path, shapes, name="two.step"):
    path = str(tmp_path / name)
    K.export_step(shapes, path)
    with open(path, "rb") as fh:
        return fh.read()


def two_boxes(tmp_path):
    """A STEP file of two solids that do not touch: a 10 mm box and a 5 mm box 20 mm to its right."""
    return _step(tmp_path, [_box(10, 10, 10, (0, 40, 0)), _box(5, 5, 5, (20, 40, 0))])


class _Served:
    """A server over a root holding one copy of the example hinge Document (opened) with a new empty Assembly tab,
    and a file root `lib` holding `lib/pair.step` (two boxes)."""

    def __init__(self, tmp_path, lock_by=None):
        self.tmp = tmp_path
        self.root = str(tmp_path / "Documents")
        os.makedirs(self.root)
        lib = tmp_path / "lib"
        os.makedirs(lib)
        self.pair = two_boxes(tmp_path)
        (lib / "pair.step").write_bytes(self.pair)
        registry = R.DocRegistry(os.path.join(str(tmp_path / "data"), R.REGISTRY_NAME))
        self.path = os.path.join(self.root, "Hinge")
        shutil.copytree(HINGE, self.path)
        with open(os.path.join(self.path, "document.json")) as fh:
            self.doc = json.load(fh)["id"]
        registry.register_created(self.doc, self.path)
        self.server = SV.make_server(None, 0, registry=registry, root=self.root, file_roots=[("lib", str(lib))])
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)
        self.lock = doc_lock.acquire(self.path, lock_by) if lock_by else None     # another program holds the Document
        opened = self.ok("POST", f"/documents/{self.doc}/open")
        self.tabs_before = [t["id"] for t in self.ok("GET", f"/documents/{self.doc}/tabs")["tabs"]]
        if lock_by:
            assert opened["read_only"] is True
            self.asm = next(t["id"] for t in self.ok("GET", f"/documents/{self.doc}/tabs")["tabs"]
                            if t["kind"] == "assembly")                               # the hinge's own Assembly
        else:
            made = self.ok("POST", f"/documents/{self.doc}/tabs", {"kind": "assembly", "name": "New"}, expect=201)
            self.asm = made["tab"]["id"]
        self.tab = f"/documents/{self.doc}/tabs/{self.asm}"

    def request(self, method, path, body=None, ctype=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        try:
            data = raw if raw is not None else (json.dumps(body if body is not None else {}).encode()
                                                if method == "POST" else None)
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            c.putheader("Host", f"127.0.0.1:{self.port}")
            c.putheader("X-Client-Id", "client-aaaa1111")
            if method == "POST":
                c.putheader("Content-Type", ctype or "application/json")
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

    def ok(self, method, path, body=None, expect=200):
        status, payload = self.request(method, path, body)
        assert status == expect, (method, path, status, payload)
        return payload

    def edit(self, body, tab=None):
        return self.ok("POST", f"{tab or self.tab}/edit", body)

    def post(self, data, tab=None, **query):
        q = "?" + urllib.parse.urlencode(query) if query else ""
        return self.request("POST", f"{tab or self.tab}/import{q}", ctype=OCTET, raw=data)

    def post_ok(self, data, **query):
        status, reply = self.post(data, **query)
        assert status == 200, reply
        return reply

    def outputs(self, tab=None):
        status, body = self.request("GET", f"{tab or self.tab}/outputs")
        assert status == 200, body
        return wire.from_wire(body)

    def blob_files(self):
        out = []
        for d, _, files in os.walk(self.path):
            out += [os.path.join(d, f) for f in files if os.path.basename(d) == "blobs" or "blobs" in d.split(os.sep)]
        return sorted(out)

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()
            if self.lock is not None:
                self.lock.release()


def _bbox(out, iid):
    """(lo, hi) of an instance's world bounding box, from the vertices of its mesh."""
    inst = next(i for i in out.instances if i.id == iid)
    pts = np.asarray(out.parts[inst.part].mesh.verts, float)
    T = np.asarray(inst.T, float)
    w = pts @ T[:3, :3].T + T[:3, 3]
    return w.min(axis=0), w.max(axis=0)


def test_a_step_of_two_boxes_makes_two_instances_the_first_fixed_at_the_origin_and_one_undo_removes_both(tmp_path):
    s = _Served(tmp_path)
    try:
        reply = s.post_ok(s.pair, name="two.step")
        out = s.outputs()
        assert [i.id for i in out.instances] == [i["id"] for i in reply["instances"]] and len(out.instances) == 2
        assert reply["tab"]["id"] == s.asm and reply["rebuilt"][0] == s.asm
        assert [i.fixed for i in out.instances] == [True, False]
        assert all(np.allclose(i.T, np.eye(4)) for i in out.instances)            # in the file's own places
        key = hashlib.sha256(s.pair).hexdigest()
        assert [i.source for i in out.instances] == [
            {"kind": "step", "blob": key, "index": k, "filename": "two.step"} for k in (0, 1)]
        lo, hi = _bbox(out, out.instances[1].id)
        assert np.allclose(lo, (20, 40, 0), atol=0.05) and np.allclose(hi, (25, 45, 5), atol=0.05)
        s.edit({"op": "undo"})
        assert s.outputs().instances == []
        s.edit({"op": "redo"})
        assert len(s.outputs().instances) == 2
    finally:
        s.close()


def test_a_second_step_goes_beside_what_is_there_all_by_one_shift(tmp_path):
    s = _Served(tmp_path)
    try:
        s.post_ok(s.pair, name="two.step")
        first = s.outputs()
        s.post_ok(s.pair, name="again.step")
        out = s.outputs()
        assert len(out.instances) == 4 and [i.fixed for i in out.instances] == [True, False, False, False]
        shifts = [np.asarray(i.T, float)[:3, 3] for i in out.instances[2:]]
        assert np.allclose(shifts[0], shifts[1]) and shifts[0][0] > 0
        lo, hi = _bbox(first, first.instances[0].id)
        new_lo, _ = _bbox(out, out.instances[2].id)
        assert new_lo[0] > hi[0]                                                   # to the right of the first two
        s.edit({"op": "undo"})
        assert len(s.outputs().instances) == 2
    finally:
        s.close()


def test_a_library_entry_posted_as_root_and_entry_inserts_its_solids(tmp_path):
    s = _Served(tmp_path)
    try:
        listing = s.ok("GET", "/files/lib?" + urllib.parse.urlencode({"q": "pair"}))["entries"]
        eid = next(e["id"] for e in listing if e["path"] == "pair.step")
        status, reply = s.request("POST", f"{s.tab}/import", {"root": "lib", "entry": eid})
        assert status == 200, reply
        out = s.outputs()
        assert len(out.instances) == 2 and len(reply["instances"]) == 2
        assert {i.source["filename"] for i in out.instances} == {"pair.step"}
        assert out.instances[0].fixed
    finally:
        s.close()


def _instances(doc):
    return [{k: v for k, v in i.items() if k in ("id", "name", "source", "T", "fixed", "hidden", "part_name")}
            for i in doc.instances]


def test_a_saved_document_opens_in_the_desktop_class_and_saves_what_the_desktop_saves(tmp_path):
    s = _Served(tmp_path)
    try:
        before = set(s.blob_files())
        s.post_ok(s.pair, name="two.step")
        served = s.outputs()
        s.ok("POST", f"/documents/{s.doc}/save")
    finally:
        s.close()
    key = hashlib.sha256(s.pair).hexdigest()
    folder = DF.FolderDocument.open(s.path)
    try:
        assert not folder.errors, folder.errors
        asm = folder.tab(s.asm).doc
        asm.refresh(solve=False, notify=False)
        assert len(asm.instances) == 2 and not asm.errors, asm.errors
        assert [i["source"]["blob"] for i in asm.instances] == [key, key]
        assert asm.parts[asm.instances[1]["id"]].name and len(asm.parts) == 2
        for inst, got in zip(asm.instances, served.instances):
            lo, hi = K.bbox(asm.world_shape(inst["id"]))
            glo, ghi = _bbox(served, got.id)
            assert np.allclose(lo, glo, atol=0.05) and np.allclose(hi, ghi, atol=0.05)
        assert folder.store.has_blob(key, s.asm)
        assert folder.store.read_blob(key, s.asm) == s.pair
        # what the desktop saves for the same file: the same instance rows and the same blob
        desk = A.AssemblyDoc()
        dkey = desk.add_step_blob(s.pair)
        desk.insert_step_parts(dkey, "two.step")
        desk.refresh(solve=False, notify=False)
        assert dkey == key and _instances(desk) == _instances(asm)
        assert desk.blobs[key] == s.pair
    finally:
        folder.close()
    assert len(set(s.blob_files()) - before) == 1                                  # the STEP, once


def _assert_refused(s, data, status_want, text, **query):
    s.ok("POST", f"/documents/{s.doc}/save")
    before, files = s.outputs(), s.blob_files()
    status, reply = s.post(data, **query)
    assert status == status_want and text in json.dumps(reply), (status, reply)
    after = s.outputs()
    assert [i.id for i in after.instances] == [i.id for i in before.instances] and after.revision == before.revision
    s.ok("POST", f"/documents/{s.doc}/save")
    assert s.blob_files() == files                                                 # no blob left behind


def test_a_body_that_is_not_a_step_a_step_with_no_solid_and_a_brep_are_refused_changing_nothing(tmp_path):
    s = _Served(tmp_path)
    try:
        s.post_ok(_step(tmp_path, [_box(7, 7, 7)], "one.step"), name="one.step")     # something to undo afterwards
        _assert_refused(s, b"not a step at all", 415, "not a STEP")
        _assert_refused(s, b"ISO-10303-21;\nthis is not a STEP file\n", 400, "insert_step")
        brep = str(tmp_path / "b.brep")
        BRepTools.Write_s(_box(5, 5, 5), brep)
        with open(brep, "rb") as fh:
            _assert_refused(s, fh.read(), 415, "STEP file")
        _assert_refused(s, s.pair, 400, "new_tab", new_tab="1")
        assert [t["id"] for t in s.ok("GET", f"/documents/{s.doc}/tabs")["tabs"]] == s.tabs_before + [s.asm]
        assert len(s.outputs().instances) == 1
        s.edit({"op": "undo"})                                                       # nothing was pushed by the refusals
        assert s.outputs().instances == []
    finally:
        s.close()


def test_the_insert_step_edit_refuses_bad_data_and_a_tab_that_is_not_an_assembly(tmp_path):
    s = _Served(tmp_path)
    try:
        studio = f"/documents/{s.doc}/tabs/T1"
        for body in ({"op": "insert_step"}, {"op": "insert_step", "data": 5},
                     {"op": "insert_step", "data": "!!not base64!!"},
                     {"op": "insert_step", "data": base64.b64encode(b"hello").decode()}):
            status, reply = s.request("POST", f"{s.tab}/edit", body)
            assert status == 400 and "error" in reply, (body, status, reply)
        status, reply = s.request("POST", f"{studio}/edit", {"op": "insert_step", "data": "", "filename": "x.step"})
        assert status == 400 and "no edit" in json.dumps(reply)
        assert s.outputs().instances == []
        # a small file may also go through the JSON edit; a path in the name is cut to its last part
        s.edit({"op": "insert_step", "data": base64.b64encode(s.pair).decode(), "filename": "..\\x/y\\z.step"})
        assert {i.source["filename"] for i in s.outputs().instances} == {"z.step"}
    finally:
        s.close()


def test_a_read_only_document_is_423_and_nothing_changes(tmp_path):
    s = _Served(tmp_path, lock_by="desktop")
    try:
        before, files = s.outputs(), s.blob_files()
        status, reply = s.post(s.pair, name="two.step")
        assert status == 423 and reply["holder"]["program"] == "desktop", (status, reply)
        status, reply = s.request("POST", f"{s.tab}/edit", {"op": "insert_step", "data": base64.b64encode(s.pair).decode()})
        assert status == 423, (status, reply)
        after = s.outputs()
        assert [i.id for i in after.instances] == [i.id for i in before.instances] and after.revision == before.revision
        assert s.blob_files() == files
    finally:
        s.close()


def test_the_desktop_places_a_two_solid_step_as_before(tmp_path):
    """Pins `AssemblyController.insert_step` (and so insert_step_parts): at the identity for every solid in an empty
    Assembly, else all by the one shift the code had before it moved (right of what is there, on its lowest side)."""
    raw = two_boxes(tmp_path)
    asm = A.AssemblyDoc()
    key = asm.add_step_blob(raw)
    ids, errors = asm.insert_step_parts(key, "two.step")
    assert len(ids) == 2 and not errors
    assert all(np.allclose(i["T"], np.eye(4)) for i in asm.instances)
    assert [i["fixed"] for i in asm.instances] == [True, False]
    # the earlier code, written out: the shift from the existing bounds and the file's solids' bounds
    lo, hi = asm.bounds()
    sols = asm._step_parts({"blob": key})
    slo, shi = np.full(3, np.inf), np.full(3, -np.inf)
    for shape, *_ in sols:
        a, b = K.bbox(shape)
        slo, shi = np.minimum(slo, a), np.maximum(shi, b)
    gap = max(0.15 * float(np.linalg.norm(shi - slo)), 5.0)
    want = A.translation([hi[0] + gap - slo[0], 0, lo[2] - slo[2]])
    ids, errors = asm.insert_step_parts(key, "two.step")
    assert len(ids) == 2 and not errors
    for iid in ids:
        assert np.allclose(asm.instance(iid)["T"], want)
    assert not asm.instance(ids[0])["fixed"]
    asm.undo()
    assert len(asm.instances) == 2                                                  # one undo step for both


def test_the_desktop_controller_inserts_a_step_through_the_same_code(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    path = str(tmp_path / "two.step")
    with open(path, "wb") as fh:
        fh.write(two_boxes(tmp_path))
    from PySide6.QtWidgets import QMessageBox
    shown = []                                           # a warning would block an offscreen test
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[2])))
    w = MainWindow()
    try:
        w.new_assembly()
        app.processEvents()
        assert w.asm.insert_step(path) == 2
        doc = w.doc
        assert len(doc.instances) == 2 and all(np.allclose(i["T"], np.eye(4)) for i in doc.instances)
        assert [i["source"]["filename"] for i in doc.instances] == ["two.step", "two.step"]
        assert w.asm.insert_step(path) == 2
        assert len(doc.instances) == 4 and np.asarray(doc.instances[2]["T"])[0, 3] > 0
        doc.undo()
        assert len(doc.instances) == 2
        assert not shown, shown
    finally:
        w.deleteLater()                                  # close() would ask about the unsaved Assembly
