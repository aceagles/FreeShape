"""I11.5: GET /documents/<id>/archive and POST /documents/archive (a Document as one .fsdoc, served;
freeshape/doc_archive.py under it). Each test builds its own root and server and stops it in a finaliser, so each
passes alone."""
import http.client
import io
import json
import os
import shutil
import threading
import urllib.parse
import zipfile

import pytest
from conftest import state_fingerprint

from freeshape import doc_archive as A
from freeshape import doc_folder as DF
from freeshape import doc_lock
from freeshape import doc_registry as R
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.session import DocumentSession

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
EDIT = {"op": "set_param", "feature": "F5", "key": "diameter", "value": 7.0}
OCTET = "application/octet-stream"
CLIENT = "client-aaaa1111"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


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

    def bracket(self, leaf="Doc"):
        path = os.path.join(self.root, leaf)
        shutil.copytree(BRACKET, path)
        with open(os.path.join(path, "document.json")) as fh:
            doc_id = json.load(fh)["id"]
        self.registry.register_created(doc_id, path)
        return doc_id, os.path.realpath(path)

    def request(self, method, path, data=None, ctype=None, length=None, client=CLIENT):
        """(status, reply as JSON or bytes, response)"""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=300)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            c.putheader("Host", f"127.0.0.1:{self.port}")
            if client:
                c.putheader("X-Client-Id", client)
            if method == "POST":
                if ctype is None:
                    ctype = "application/json"
                    data = json.dumps(data if data is not None else {}).encode()
                c.putheader("Content-Type", ctype)
                c.putheader("Content-Length", str(len(data) if length is None else length))
            c.endheaders()
            if method == "POST" and data:
                try:
                    c.send(data)
                except OSError:
                    pass                                         # the server answered before reading it all
            r = c.getresponse()
            payload = r.read()
            if r.getheader("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return r.status, payload, r
        finally:
            c.close()

    def ok(self, method, path, body=None):
        status, reply, _ = self.request(method, path, body)
        assert status == 200, (method, path, status, reply)
        return reply

    def upload(self, data, name="Received", **kw):
        return self.request("POST", "/documents/archive?" + urllib.parse.urlencode({"name": name}), data, OCTET, **kw)

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


def _folders(path):
    return sorted(os.listdir(path))


def _volumes(folder, registry):
    fd = DF.FolderDocument.open(folder, registry=registry)
    try:
        assert fd.errors == {}
        return [(t.id, t.kind, t.name, {p: f["volume"] for p, f in state_fingerprint(t.doc).items()})
                for t in fd.tabs if t.kind == "studio"]
    finally:
        fd.close()


def _diameter(archive):
    """The F5 diameter stored in the first tab file of the archive bytes."""
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        manifest = json.loads(z.read("document.json"))
        text = z.read(manifest["tabs"][0]["file"]).decode()
    return text


# ----------------------------------------------------------------------------------------------- download
def test_download_of_the_bracket_unpacks_to_a_document_with_the_same_part(root, tmp_path):
    r = root()
    doc_id, folder = r.bracket()
    status, data, resp = r.request("GET", f"/documents/{doc_id}/archive")      # a closed Document downloads too
    assert status == 200 and isinstance(data, bytes)
    assert resp.getheader("Content-Type") == OCTET
    assert resp.getheader("Content-Disposition") == (
        "attachment; filename=\"Bracket.fsdoc\"; filename*=UTF-8''Bracket.fsdoc")
    assert data == A.pack(folder)
    registry = R.DocRegistry(str(tmp_path / "other.json"))
    parent = str(tmp_path / "parent")
    out = A.unpack(data, parent, "copy", registry=registry)
    assert _volumes(out, registry) == _volumes(folder, r.registry)


def test_the_file_name_comes_from_the_registry_and_handles_a_non_ascii_name(root):
    r = root()
    doc_id, folder = r.bracket()
    with open(os.path.join(folder, "document.json")) as fh:
        manifest = json.load(fh)
    manifest["name"] = "Bräcket/..\\x"
    with open(os.path.join(folder, "document.json"), "w") as fh:
        json.dump(manifest, fh)
    r.ok("POST", f"/documents/{doc_id}/open")
    status, _, resp = r.request("GET", f"/documents/{doc_id}/archive")
    assert status == 200
    disposition = resp.getheader("Content-Disposition")
    assert disposition == "attachment; filename=\"x.fsdoc\"; filename*=UTF-8''x.fsdoc"
    manifest["name"] = "Bräcket é"
    with open(os.path.join(folder, "document.json"), "w") as fh:
        json.dump(manifest, fh)
    r.ok("POST", f"/documents/{doc_id}/close")
    _, _, resp = r.request("GET", f"/documents/{doc_id}/archive")
    assert resp.getheader("Content-Disposition") == (
        "attachment; filename=\"Br_cket _.fsdoc\"; filename*=UTF-8''Br%C3%A4cket%20%C3%A9.fsdoc")


def test_an_open_document_without_edits_downloads_and_an_unknown_id_is_404(root):
    r = root()
    doc_id, folder = r.bracket()
    r.ok("POST", f"/documents/{doc_id}/open")
    status, data, _ = r.request("GET", f"/documents/{doc_id}/archive")
    assert status == 200 and data == A.pack(folder)
    assert r.request("GET", "/documents/nope1234/archive")[0] == 404
    assert r.request("POST", f"/documents/{doc_id}/archive", {})[0] == 405
    assert r.request("GET", f"/documents/{doc_id}/archive?save=2")[0] == 400
    assert r.request("GET", f"/documents/{doc_id}/archive?x=1")[0] == 400


def test_an_unsaved_edit_is_409_and_nothing_is_saved(root):
    r = root()
    doc_id, folder = r.bracket()
    r.ok("POST", f"/documents/{doc_id}/open")
    before = A.pack(folder)
    r.ok("POST", f"/documents/{doc_id}/tabs/T1/edit", EDIT)
    status, body, _ = r.request("GET", f"/documents/{doc_id}/archive")
    assert status == 409 and body["error"] == "unsaved" and body["document"] == doc_id
    status, body, _ = r.request("GET", f"/documents/{doc_id}/archive?save=0")
    assert status == 409
    assert A.pack(folder) == before                                         # the folder is as it was


def test_save_1_from_the_lock_holder_saves_first_and_the_archive_has_the_edit(root):
    r = root()
    doc_id, folder = r.bracket()
    r.ok("POST", f"/documents/{doc_id}/open")
    before = A.pack(folder)
    r.ok("POST", f"/documents/{doc_id}/tabs/T1/edit", EDIT)
    status, data, _ = r.request("GET", f"/documents/{doc_id}/archive?save=1")
    assert status == 200
    assert data != before and data == A.pack(folder)
    assert '"value": 7.0' in _diameter(data) or "7.0" in _diameter(data)
    assert "7.0" not in _diameter(before)
    assert r.request("GET", f"/documents/{doc_id}/archive")[0] == 200       # nothing unsaved any more


def test_save_1_from_a_client_that_does_not_hold_the_lock_is_423(root):
    r = root()
    doc_id, folder = r.bracket()
    desktop = doc_lock.acquire(folder, "desktop")
    assert r.ok("POST", f"/documents/{doc_id}/open")["read_only"] is True
    status, body, _ = r.request("GET", f"/documents/{doc_id}/archive?save=1")
    assert status == 423 and body["holder"]["program"] == "desktop" and body["document"] == doc_id
    assert r.request("GET", f"/documents/{doc_id}/archive")[0] == 200       # a plain download still works
    assert doc_lock.holder(folder).instance == desktop.instance


def test_a_lock_taken_while_open_makes_save_1_423_with_edits_unsaved(root):
    r = root()
    doc_id, folder = r.bracket()
    r.ok("POST", f"/documents/{doc_id}/open")
    r.ok("POST", f"/documents/{doc_id}/tabs/T1/edit", EDIT)
    doc_lock.take_over(folder, "desktop")
    status, body, _ = r.request("GET", f"/documents/{doc_id}/archive?save=1")
    assert status == 423 and body["holder"]["program"] == "desktop"
    assert r.request("GET", f"/documents/{doc_id}/archive")[0] == 409


def test_a_folder_pack_refuses_is_422_with_the_reason(root):
    r = root()
    doc_id, folder = r.bracket()
    with open(os.path.join(folder, "document.json")) as fh:
        manifest = json.load(fh)
    old = manifest["tabs"][0]["file"]
    manifest["tabs"][0]["file"] = "a:" + old
    with open(os.path.join(folder, "document.json"), "w") as fh:
        json.dump(manifest, fh)
    status, body, _ = r.request("GET", f"/documents/{doc_id}/archive")
    assert status == 422 and body["error"]


def test_a_single_document_server_downloads_the_archive_and_refuses_an_upload(tmp_path):
    folder = str(tmp_path / "Doc")
    shutil.copytree(BRACKET, folder)
    session = DocumentSession(folder, spares=0)
    server = SV.make_server(session, 0, folder=folder, lock_program="browser")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert server.wait_started(30)
        c = http.client.HTTPConnection("127.0.0.1", server.port, timeout=300)
        c.request("GET", f"/documents/{session.id}/archive")
        resp = c.getresponse()
        data = resp.read()
        assert resp.status == 200 and data == A.pack(folder)
        c.close()
        body = A.pack(folder)
        c = http.client.HTTPConnection("127.0.0.1", server.port, timeout=300)
        c.request("POST", "/documents/archive?name=x", body=body,
                  headers={"Content-Type": OCTET, "Content-Length": str(len(body))})
        resp = c.getresponse()
        resp.read()
        assert resp.status == 404
        c.close()
    finally:
        SV.close_all(server, session)
        P.SPARES.clear()
    assert _folders(str(tmp_path)) == ["Doc"]


# ----------------------------------------------------------------------------------------------- upload
def test_posting_the_archive_makes_a_second_document_listed_by_get_documents(root, tmp_path):
    r = root()
    doc_id, folder = r.bracket()
    archive = A.pack(folder)
    status, body, _ = r.upload(archive, "Copy of Bracket.fsdoc")
    assert status == 201 and set(body) == {"id", "name"}
    assert body["id"] != doc_id and body["name"] == "Bracket"
    rows = {d["id"]: d for d in r.ok("GET", "/documents")["documents"]}
    assert set(rows) == {doc_id, body["id"]} and rows[body["id"]]["missing"] is False
    assert _folders(r.root) == ["Copy of Bracket", "Doc"]
    assert _volumes(os.path.join(r.root, "Copy of Bracket"), r.registry) == _volumes(folder, r.registry)
    assert r.ok("POST", f"/documents/{body['id']}/open")["document"]["id"] == body["id"]     # it opens
    r.ok("POST", f"/documents/{body['id']}/close")
    again = r.upload(archive, "Copy of Bracket")[1]                          # a clash gets the next free name
    assert _folders(r.root) == ["Copy of Bracket", "Copy of Bracket 2", "Doc"] and again["id"] != body["id"]


def _zip_slip(archive):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as z, zipfile.ZipFile(out, "w") as w:
        for n in z.namelist():
            w.writestr(n, z.read(n))
        w.writestr("../evil.fshape", b"x")
    return out.getvalue()


def test_a_zip_slip_archive_is_422_and_creates_nothing(root, tmp_path):
    r = root()
    doc_id, folder = r.bracket()
    status, body, _ = r.upload(_zip_slip(A.pack(folder)))
    assert status == 422 and ".." in body["error"]
    assert _folders(r.root) == ["Doc"] and _folders(str(tmp_path)) == ["Documents", "data"]
    assert [d["id"] for d in r.ok("GET", "/documents")["documents"]] == [doc_id]
    assert list(r.registry.documents) == [doc_id]


@pytest.mark.parametrize("data", [b"", b"not a zip at all"])
def test_a_body_that_is_not_an_archive_is_422_and_creates_nothing(root, data):
    r = root()
    status, body, _ = r.upload(data)
    assert status == 422 and "zip" in body["error"]
    assert _folders(r.root) == [] and r.registry.documents == {}


def test_a_body_over_max_archive_is_413_before_it_is_read(root, monkeypatch):
    monkeypatch.setattr(SV, "MAX_ARCHIVE", 1000)
    r = root()
    status, body, _ = r.upload(b"x" * 1001)
    assert status == 413 and "1000" in body["error"]
    status, body, _ = r.upload(b"x", length=10 ** 9)                         # the header alone decides
    assert status == 413
    assert _folders(r.root) == [] and r.registry.documents == {}


def test_the_limit_and_the_deadline_are_the_cards_numbers():
    assert SV.MAX_ARCHIVE == 256 * 1024 * 1024 and SV.ARCHIVE_DEADLINE == 300.0 and SV.MAX_BODY == 1024 * 1024


def test_a_slow_body_is_408_and_creates_nothing(root, monkeypatch):
    monkeypatch.setattr(SV, "ARCHIVE_DEADLINE", 0.5)
    r = root()
    status, body, _ = r.upload(b"x", length=100)                             # 99 bytes never arrive
    assert status in (408, 400) and "error" in body
    assert _folders(r.root) == [] and r.registry.documents == {}


def test_the_upload_checks_its_query_content_type_and_length(root):
    r = root()
    doc_id, folder = r.bracket()
    archive = A.pack(folder)
    post = lambda path, **kw: r.request("POST", path, archive, **{"ctype": OCTET, **kw})        # noqa: E731
    assert post("/documents/archive")[0] == 400                              # no name
    assert post("/documents/archive?name=a&name=b")[0] == 400
    assert post("/documents/archive?name=a&x=1")[0] == 400
    assert post("/documents/archive?name=..")[0] == 400                      # folder_name_for refuses it
    assert post("/documents/archive?name=" + urllib.parse.quote("../x"))[0] == 400
    assert post("/documents/archive?name=a", ctype="application/json")[0] == 415
    assert r.request("GET", "/documents/archive?name=a")[0] == 405
    assert _folders(r.root) == ["Doc"]


def test_the_serve_path_still_loads_no_qt_with_the_archive_routes():
    import subprocess
    import sys
    code = ("import sys; import freeshape.serve as S; import freeshape.doc_archive; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'shiboken6')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                         cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
