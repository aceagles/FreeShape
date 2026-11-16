"""I11.1: POST /documents/<id>/tabs/<tab>/import (STEP or BREP into a Part Studio, served) and
freeshape/step_import.py under it. Each test builds its own root and server, so each passes alone."""
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import threading
import urllib.parse

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools

from freeshape import doc_folder as DF
from freeshape import doc_lock
from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape import serve as SV
from freeshape import step_import as SI
from freeshape.engine import process as P
from freeshape.engine import wire

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
OCTET = "application/octet-stream"


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

    def bracket(self):
        path = os.path.join(self.root, "Doc")
        shutil.copytree(BRACKET, path)
        with open(os.path.join(path, "document.json")) as fh:
            doc_id = json.load(fh)["id"]
        self.registry.register_created(doc_id, path)
        return doc_id, os.path.realpath(path)

    def request(self, method, path, data=None, ctype=None, length=None):
        h = {"Host": f"127.0.0.1:{self.port}", "X-Client-Id": "client-aaaa1111"}
        if method == "POST":
            h["Content-Type"] = ctype or "application/json"
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if data is not None:
                c.putheader("Content-Length", str(len(data) if length is None else length))
            c.endheaders(data)
            r = c.getresponse()
            payload = r.read()
            if r.getheader("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return r.status, payload
        finally:
            c.close()

    def ok(self, method, path, body=None, expect=200):
        data = json.dumps(body if body is not None else {}).encode() if method == "POST" else None
        status, payload = self.request(method, path, data)
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


def _box_bytes(tmp_path, size=20.0, suffix=".step"):
    path = str(tmp_path / ("box" + suffix))
    shape = BRepPrimAPI_MakeBox(size, size, size).Shape()
    if suffix == ".step":
        K.export_step([shape], path)
    else:
        BRepTools.Write_s(shape, path)
    with open(path, "rb") as fh:
        return fh.read()


def _open(root):
    r = root()
    doc_id, folder = r.bracket()
    r.ok("POST", f"/documents/{doc_id}/open")
    return r, doc_id, folder


def _import_path(doc_id, tab="T1", **query):
    q = "?" + urllib.parse.urlencode(query) if query else ""
    return f"/documents/{doc_id}/tabs/{tab}/import{q}"


def _post(r, doc_id, data, tab="T1", ctype=OCTET, **query):
    return r.request("POST", _import_path(doc_id, tab, **query), data, ctype)


def _outputs(r, doc_id, tab="T1"):
    status, raw = r.request("GET", f"/documents/{doc_id}/tabs/{tab}/outputs")
    assert status == 200
    return wire.from_wire(raw)


def _files(path):
    return sorted(os.path.join(d, n) for d, ds, fs in os.walk(path) for n in ds + fs if n != doc_lock.LOCK_NAME)


def _volume(part):
    return K.volume(K.shape_from_brep(part.brep))


def test_a_step_box_adds_one_import_feature_and_one_part_and_undo_removes_it(root, tmp_path):
    r, doc_id, _ = _open(root)
    before = _outputs(r, doc_id)
    status, reply = _post(r, doc_id, _box_bytes(tmp_path), name="box.step")
    assert status == 200, reply
    assert reply["feature"]["name"] == "Import box.step" and reply["feature"]["error"] is None
    assert reply["tab"]["id"] == "T1" and reply["rebuilt"][0] == "T1"
    assert len(reply["parts"]) == 1
    after = _outputs(r, doc_id)
    added = [f for f in after.features if f.id not in {x.id for x in before.features}]
    assert [(f.type, f.name) for f in added] == [("import", "Import box.step")]
    assert reply["feature"]["id"] == added[0].id
    new = [p for p in after.parts if p.id not in {x.id for x in before.parts}]
    assert [p.id for p in new] == [reply["parts"][0]["id"]] and abs(_volume(new[0]) - 8000) < 1e-3
    r.ok("POST", f"/documents/{doc_id}/tabs/T1/edit", {"op": "undo"})      # one undo step
    back = _outputs(r, doc_id)
    assert [f.id for f in back.features] == [f.id for f in before.features]
    assert [p.id for p in back.parts] == [p.id for p in before.parts]


def test_a_brep_is_imported_too(root, tmp_path):
    r, doc_id, _ = _open(root)
    status, reply = _post(r, doc_id, _box_bytes(tmp_path, suffix=".brep"), name="box.brep")
    assert status == 200, reply
    assert reply["feature"]["name"] == "Import box.brep" and reply["feature"]["error"] is None
    assert len(reply["parts"]) == 1


def test_after_save_the_folder_holds_the_bytes_as_a_blob_and_the_desktop_class_rebuilds_the_part(root, tmp_path):
    r, doc_id, folder = _open(root)
    raw = _box_bytes(tmp_path)
    status, reply = _post(r, doc_id, raw, name="box.step")
    assert status == 200, reply
    r.ok("POST", f"/documents/{doc_id}/save")
    key = hashlib.sha256(raw).hexdigest()
    with open(os.path.join(folder, "blobs", key), "rb") as fh:
        assert fh.read() == raw
    r.ok("POST", f"/documents/{doc_id}/close")
    doc = DF.FolderDocument.open(folder)
    studio = doc.tab("T1").doc
    f = next(x for x in studio.features if x["type"] == "import")
    assert f["name"] == "Import box.step" and f["params"]["blob"] == key and "data" not in f["params"]
    assert not studio.errors, studio.errors
    assert any(abs(K.volume(p.shape) - 8000) < 1e-3 for p in studio.state.parts.values())


def test_new_tab_adds_a_part_studio_named_after_the_file(root, tmp_path):
    r, doc_id, _ = _open(root)
    status, reply = _post(r, doc_id, _box_bytes(tmp_path), name="box.step", new_tab="1")
    assert status == 200, reply
    assert reply["tab"]["kind"] == "studio" and reply["tab"]["name"] == "box" and reply["tab"]["id"] != "T1"
    assert [t["id"] for t in reply["tabs"]] == ["T1", reply["tab"]["id"]]
    out = _outputs(r, doc_id, reply["tab"]["id"])
    assert [(f.type, f.name) for f in out.features] == [("import", "Import box.step")]
    assert len(out.parts) == 1 and abs(_volume(out.parts[0]) - 8000) < 1e-3
    assert [f.type for f in _outputs(r, doc_id).features if f.type == "import"] == []     # T1 untouched


def test_a_body_that_is_not_step_or_brep_is_415_and_nothing_changes(root, tmp_path):
    r, doc_id, _ = _open(root)
    before = r.ok("GET", f"/documents/{doc_id}/tabs")
    for body in (b"hello", b"", b"{\"op\": \"undo\"}", b"\x00" * 100, b"PK\x03\x04" + b"x" * 50):
        status, reply = _post(r, doc_id, body, name="x.step")
        assert status == 415, (body, status, reply)
    status, reply = _post(r, doc_id, _box_bytes(tmp_path), ctype="text/plain", name="x.step")    # I11.2: JSON is {root, entry}
    assert status == 415
    assert r.ok("GET", f"/documents/{doc_id}/tabs") == before


def test_the_sniff_allows_a_bom_and_leading_whitespace():
    assert SI.sniff(b"\xef\xbb\xbf \r\n\tISO-10303-21;\nHEADER;") == "step"
    assert SI.sniff(b"  DBRep_DrawableShape\n") == "brep"
    assert SI.sniff(b"x ISO-10303-21;") is None


def test_a_body_over_the_upload_limit_is_413_without_being_read(root, tmp_path):
    r, doc_id, _ = _open(root)
    status, reply = r.request("POST", _import_path(doc_id, name="big.step"), b"ISO-10303-21;",
                              OCTET, length=SV.MAX_UPLOAD + 1)
    assert status == 413 and "error" in reply
    assert r.ok("GET", "/health")["ok"] is True


def test_a_json_route_still_refuses_a_2_mb_body_and_a_big_body_is_only_for_import(root):
    r, doc_id, _ = _open(root)
    big = json.dumps({"name": "x" * (2 * 1024 * 1024)}).encode()
    status, _ = r.request("POST", f"/documents/{doc_id}/tabs/T1/rename", big)
    assert status == 413
    status, _ = r.request("POST", f"/documents/{doc_id}/tabs/T1/rename", b"{}", length=SV.MAX_BODY + 1)
    assert status == 413
    assert SV.MAX_UPLOAD == 64 * 1024 * 1024 and SV.MAX_BODY == 1024 * 1024


def test_a_hostile_name_is_shown_as_its_last_part_and_nothing_is_written_outside_the_document(root, tmp_path):
    r, doc_id, folder = _open(root)
    outside = set(os.listdir(os.path.dirname(folder)))
    status, reply = _post(r, doc_id, _box_bytes(tmp_path), name="../../x")
    assert status == 200, reply
    assert reply["feature"]["name"] == "Import x.step"
    assert set(os.listdir(os.path.dirname(folder))) == outside
    assert not os.path.exists(os.path.join(os.path.dirname(os.path.dirname(folder)), "x"))
    r.ok("POST", f"/documents/{doc_id}/save")
    assert set(os.listdir(os.path.dirname(folder))) == outside
    assert not [p for p in _files(os.path.dirname(os.path.dirname(folder))) if os.path.basename(p) == "x"]


def test_names_are_cut_cleaned_and_made_to_match_the_format():
    assert SI.clean_file_name("a\\b\\c.step") == "c.step"
    assert SI.clean_file_name("a\x00b\n.step") == "ab.step"
    long = SI.clean_file_name("n" * 300 + ".step")
    assert len(long) == 100 and long.endswith(".step")
    assert SI.clean_file_name("") == "import.step" and SI.clean_file_name(None, "brep") == "import.brep"
    assert SI.clean_file_name("..") == "import.step"
    assert SI.clean_file_name("part") == "part.step" and SI.clean_file_name("part.stp") == "part.stp"
    assert SI.clean_file_name("a.brep", "step") == "a.brep.step"       # features.is_brep_name decides the reader
    assert SI.clean_file_name("a.step", "brep") == "a.step.brep"


def test_a_read_only_document_is_423_and_nothing_changes(root, tmp_path):
    r = root()
    doc_id, folder = r.bracket()
    lock = doc_lock.acquire(folder, "desktop")
    try:
        assert r.ok("POST", f"/documents/{doc_id}/open")["read_only"] is True
        before = _files(folder)
        outputs = r.request("GET", f"/documents/{doc_id}/tabs/T1/outputs")[1]
        for query in ({"name": "box.step"}, {"name": "box.step", "new_tab": "1"}):
            status, reply = _post(r, doc_id, _box_bytes(tmp_path), **query)
            assert status == 423 and reply["holder"]["program"] == "desktop", (status, reply)
        assert r.request("GET", f"/documents/{doc_id}/tabs/T1/outputs")[1] == outputs
        assert [t["id"] for t in r.ok("GET", f"/documents/{doc_id}/tabs")["tabs"]] == ["T1"]
        assert _files(folder) == before
    finally:
        lock.release()


def test_a_failing_file_adds_the_feature_with_its_error(root):
    r, doc_id, _ = _open(root)
    status, reply = _post(r, doc_id, b"ISO-10303-21;\nthis is not a STEP file\n",
                          name="broken.step")
    assert status == 200, reply
    assert reply["feature"]["name"] == "Import broken.step" and reply["feature"]["error"]
    assert reply["parts"] == []


def test_the_route_is_post_only_and_the_tab_must_exist_and_be_a_part_studio(root, tmp_path):
    r, doc_id, _ = _open(root)
    assert r.request("GET", _import_path(doc_id))[0] == 405
    assert _post(r, doc_id, _box_bytes(tmp_path), tab="T99")[0] == 404
    assert _post(r, doc_id, _box_bytes(tmp_path), new_tab="2")[0] == 400


def test_step_import_loads_no_qt():
    code = ("import sys\nimport freeshape.step_import\n"
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'PyQt5', 'PyQt6', 'shiboken6'))\n"
            "print('QT', bad)\nassert not bad")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
