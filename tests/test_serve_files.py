"""I11.2: GET /files, GET /files/<root>, POST /files/<root>/rescan, the {root, entry} body of POST .../import, and
`freeshape serve --file-root`. Each test builds its own small root and server (never the real component library),
so each passes alone."""
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

from freeshape import doc_registry as R
from freeshape import file_roots as FR
from freeshape import kernel as K
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.engine import wire

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
MANIFEST = ("source,licence,path,bytes,authors,url\n"
            'lib1,CC-BY-3.0,a/b608.step,10,"Ann Author, Bo Writer",https://example.org/b608.step\n')


def _w(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def _box_bytes(tmp_path, size=20.0):
    path = str(tmp_path / "box.step")
    K.export_step([BRepPrimAPI_MakeBox(size, size, size).Shape()], path)
    with open(path, "rb") as fh:
        return fh.read()


def make_lib(tmp_path):
    """tmp/lib: lib1/a/{b608,plain_part}.step (b608 a real box; parts.json names it Skate Bearing 608), lib1/c/d.stp,
    top.step, manifest.csv, and links that lead out of it. Returns (lib folder, outside folder)."""
    lib, out = tmp_path / "lib", tmp_path / "outside"
    box = _box_bytes(tmp_path)
    _w(str(lib / "lib1" / "a" / "b608.step"), box)
    _w(str(lib / "lib1" / "a" / "plain_part.step"), b"ISO-10303-21;\nnot a model\n")
    _w(str(lib / "lib1" / "c" / "d.stp"), box)
    _w(str(lib / "top.step"), box)
    _w(str(out / "secret.step"), box)
    (lib / "lib1" / "a" / "parts.json").write_text(json.dumps(
        [{"id": "b608", "name": "Skate Bearing 608", "tags": ["bearing"]}]))
    (lib / "manifest.csv").write_text(MANIFEST)
    os.symlink(str(out / "secret.step"), str(lib / "link.step"))
    return str(lib), str(out)


def tree(path):
    """Every file and folder under `path` with its size and mtime, to prove nothing was written there."""
    rows = []
    for d, ds, fs in os.walk(path):
        for n in sorted(ds + fs):
            p = os.path.join(d, n)
            st = os.lstat(p)
            rows.append((p, st.st_size, st.st_mtime_ns))
    return sorted(rows)


class _Srv:
    def __init__(self, tmp_path, roots=None, documents=False):
        self.tmp = tmp_path
        self.lib, self.out = make_lib(tmp_path)
        self.root = str(tmp_path / "Documents")
        os.makedirs(self.root)
        self.registry = R.DocRegistry(os.path.join(str(tmp_path / "data"), R.REGISTRY_NAME))
        pairs = [("lib", self.lib)] if roots is None else roots
        self.server = SV.make_server(None, 0, registry=self.registry, root=self.root, file_roots=pairs)
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)

    def bracket(self):
        path = os.path.join(self.root, "Doc")
        shutil.copytree(BRACKET, path)
        with open(os.path.join(path, "document.json")) as fh:
            doc_id = json.load(fh)["id"]
        self.registry.register_created(doc_id, path)
        self.request("POST", f"/documents/{doc_id}/open", {})
        return doc_id

    def request(self, method, path, body=None, ctype="application/json", raw=None):
        h = {"Host": f"127.0.0.1:{self.port}", "X-Client-Id": "client-aaaa1111"}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if method == "POST":
            h["Content-Type"] = ctype
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
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

    def get(self, path, expect=200):
        status, payload = self.request("GET", path)
        assert status == expect, (path, status, payload)
        return payload

    def entries(self, **q):
        return self.get("/files/lib?" + urllib.parse.urlencode(q))["entries"]

    def entry_id(self, rel):
        for e in self.get("/files/lib?" + urllib.parse.urlencode({"q": rel.rsplit("/", 1)[-1], "limit": 500}))["entries"]:
            if e["path"] == rel:
                return e["id"]
        raise AssertionError(rel)

    def close(self):
        try:
            SV.close_all(self.server, None)
        finally:
            P.SPARES.clear()


@pytest.fixture
def srv(tmp_path):
    made = []

    def make(**kw):
        s = _Srv(tmp_path, **kw)
        made.append(s)
        return s
    try:
        yield make
    finally:
        for s in made:
            s.close()


def _import(s, doc_id, body, tab="T1", ctype="application/json", **query):
    q = "?" + urllib.parse.urlencode(query) if query else ""
    if isinstance(body, bytes):
        return s.request("POST", f"/documents/{doc_id}/tabs/{tab}/import{q}", ctype=ctype, raw=body)
    return s.request("POST", f"/documents/{doc_id}/tabs/{tab}/import{q}", body, ctype)


# =========================================================================== listing

def test_files_lists_the_configured_root(srv):
    s = srv()
    assert s.get("/files") == {"roots": [{"name": "lib", "files": 4, "max_upload": SV.MAX_UPLOAD}]}


def test_no_roots_is_an_empty_list_and_any_root_name_is_404(srv):
    s = srv(roots=[])
    assert s.get("/files") == {"roots": []}
    s.get("/files/lib", 404)


def test_a_folder_lists_its_sub_folders_and_entries_with_licences_as_written(srv):
    s = srv()
    top = s.get("/files/lib")
    assert top["root"] == "lib" and top["dir"] == "" and top["q"] == "" and top["offset"] == 0
    assert top["limit"] == FR.DEFAULT_LIMIT and top["total"] == 1
    assert top["dirs"] == [{"name": "lib1", "path": "lib1", "files": 3}]
    assert [e["file"] for e in top["entries"]] == ["top.step"]
    a = s.get("/files/lib?dir=lib1/a")
    assert a["dir"] == "lib1/a" and a["dirs"] == [] and a["total"] == 2
    assert [e["name"] for e in a["entries"]] == ["plain part", "Skate Bearing 608"]
    b = a["entries"][1]
    assert set(b) == {"id", "name", "file", "path", "size", "description", "tags", "licence", "authors", "url"}
    assert (b["file"], b["path"], b["tags"], b["licence"], b["authors"], b["url"]) == (
        "b608.step", "lib1/a/b608.step", ["bearing"], "CC-BY-3.0", "Ann Author, Bo Writer", "https://example.org/b608.step")
    assert len(b["id"]) == 16 and a["entries"][0]["licence"] is None
    assert s.get("/files/lib?dir=lib1")["dirs"] == [{"name": "a", "path": "lib1/a", "files": 2},
                                                    {"name": "c", "path": "lib1/c", "files": 1}]
    assert "link.step" not in [e["file"] for e in top["entries"]]


def test_dir_is_a_lookup_and_traversal_or_an_unknown_folder_is_404(srv):
    s = srv()
    for bad in ("../..", "/etc", "..", "lib1/../lib1", "%2e%2e%2f%2e%2e", "nope", "lib1/a/", "lib1%00", "%2Fetc%2Fpasswd",
                "C:\\Windows", "lib1\\a"):
        status, reply = s.request("GET", f"/files/lib?dir={bad}")
        assert status == 404 and "error" in reply and "Traceback" not in json.dumps(reply), bad
        assert s.request("GET", f"/files/lib?q=step&dir={bad}")[0] == 404, bad
    assert s.get("/files/lib?dir=")["dir"] == ""


def test_q_searches_by_the_parts_json_name_and_can_stay_in_a_folder(srv):
    s = srv()
    hit = s.get("/files/lib?q=bearing")
    assert hit["q"] == "bearing" and hit["total"] == 1 and hit["dirs"] == []
    assert [e["path"] for e in hit["entries"]] == ["lib1/a/b608.step"]
    assert s.get("/files/lib?q=step")["total"] == 3
    assert [e["file"] for e in s.get("/files/lib?q=d.stp&dir=lib1/c")["entries"]] == ["d.stp"]
    assert s.get("/files/lib?q=bearing&dir=lib1/c")["total"] == 0


def test_offset_and_limit_are_checked_and_limit_is_cut_to_500(srv):
    s = srv()
    assert [e["file"] for e in s.get("/files/lib?dir=lib1/a&limit=1&offset=1")["entries"]] == ["b608.step"]
    assert s.get("/files/lib?dir=lib1/a&offset=5")["entries"] == []
    assert s.get("/files/lib?limit=100000")["limit"] == 500
    for q in ("offset=-1", "limit=0", "limit=-5", "offset=x", "limit=1.5", "offset=", "limit=", "offset=99999999999",
              "limit=%E0%A5%A7", "offset=1e3"):
        status, reply = s.request("GET", f"/files/lib?{q}")
        assert status == 400 and "error" in reply, q


def test_unknown_or_malformed_roots_and_wrong_methods_never_give_a_traceback(srv):
    s = srv()
    for path in ("/files/none", "/files/LIB", "/files/lib%20x", "/files/lib/", "/files/lib/rescan/x", "/files/../x",
                 "/files/" + "a" * 33, "/files//", "/files/lib/other"):
        assert s.request("GET", path)[0] in (404, 405), path
    assert s.request("POST", "/files/none/rescan", {})[0] == 404
    assert s.request("POST", "/files", {})[0] == 405
    assert s.request("POST", "/files/lib", {})[0] == 405
    assert s.request("GET", "/files/lib/rescan")[0] == 405
    assert s.request("DELETE", "/files/lib")[0] == 405
    assert s.request("DELETE", "/files/lib/rescan")[0] == 405


def test_rescan_rebuilds_the_index_and_no_route_writes_into_the_root(srv):
    s = srv()
    before = tree(s.lib)
    assert s.get("/files")["roots"][0]["files"] == 4
    s.get("/files/lib?q=bearing")
    _w(os.path.join(s.lib, "new", "n.step"), b"ISO-10303-21;\n")
    assert s.get("/files")["roots"][0]["files"] == 4                  # built once
    status, reply = s.request("POST", "/files/lib/rescan", {})
    assert status == 200 and reply == {"root": "lib", "files": 5}
    assert s.get("/files")["roots"][0]["files"] == 5
    assert s.get("/files/lib")["dirs"][1]["name"] == "new"
    os.remove(os.path.join(s.lib, "new", "n.step"))
    os.rmdir(os.path.join(s.lib, "new"))
    assert tree(s.lib) == before                                       # the server added and changed nothing


def test_the_index_is_built_off_the_event_loop(srv, monkeypatch):
    gate, entered = threading.Event(), threading.Event()
    real = FR.build_index

    def slow(root):
        entered.set()
        assert gate.wait(60)
        return real(root)
    monkeypatch.setattr(FR, "build_index", slow)
    s = srv()
    result = {}
    t = threading.Thread(target=lambda: result.update(r=s.request("GET", "/files")))
    t.start()
    try:
        assert entered.wait(30)
        assert s.get("/health")["ok"] is True                           # the loop answers while the scan is stuck
    finally:
        gate.set()
        t.join(60)
    assert result["r"][0] == 200 and result["r"][1]["roots"][0]["files"] == 4


# =========================================================================== import from a root

def _outputs(s, doc_id, tab="T1"):
    status, raw = s.request("GET", f"/documents/{doc_id}/tabs/{tab}/outputs")
    assert status == 200
    return wire.from_wire(raw)


def test_import_by_entry_adds_an_import_feature_named_after_the_file(srv):
    s = srv()
    doc_id = s.bracket()
    eid = s.entry_id("lib1/a/b608.step")
    before = _outputs(s, doc_id)
    status, reply = _import(s, doc_id, {"root": "lib", "entry": eid})
    assert status == 200, reply
    assert reply["feature"]["name"] == "Import b608.step" and reply["feature"]["error"] is None
    assert len(reply["parts"]) == 1 and reply["tab"]["id"] == "T1"
    after = _outputs(s, doc_id)
    added = [f for f in after.features if f.id not in {x.id for x in before.features}]
    assert [(f.type, f.name) for f in added] == [("import", "Import b608.step")]
    status, reply = _import(s, doc_id, {"root": "lib", "entry": s.entry_id("lib1/c/d.stp")}, new_tab="1")
    assert status == 200, reply
    assert reply["tab"]["name"] == "d" and reply["feature"]["name"] == "Import d.stp"


def test_import_by_entry_refuses_unknown_roots_entries_and_bad_bodies(srv):
    s = srv()
    doc_id = s.bracket()
    tabs = s.get(f"/documents/{doc_id}/tabs")
    eid = s.entry_id("top.step")
    for body, want in (({"root": "nope", "entry": eid}, 404), ({"root": "lib", "entry": "0" * 16}, 404),
                       ({"root": "lib", "entry": "../../etc/passwd"}, 404), ({"root": "lib", "entry": "top.step"}, 404),
                       ({"root": "../lib", "entry": eid}, 404), ({"root": "lib"}, 400), ({"entry": eid}, 400),
                       ({"root": 1, "entry": eid}, 400), ({"root": "lib", "entry": ["x"]}, 400), ({}, 400)):
        status, reply = _import(s, doc_id, body)
        assert status == want and "error" in reply, (body, status, reply)
    assert _import(s, doc_id, b"[1]")[0] == 400
    assert _import(s, doc_id, b"not json")[0] == 400
    assert _import(s, doc_id, {"root": "lib", "entry": eid}, tab="T99")[0] == 404
    assert s.get(f"/documents/{doc_id}/tabs") == tabs                   # nothing changed


def test_import_by_entry_refuses_an_entry_replaced_by_a_symlink_after_indexing(srv):
    s = srv()
    doc_id = s.bracket()
    eid = s.entry_id("top.step")
    os.remove(os.path.join(s.lib, "top.step"))
    os.symlink(os.path.join(s.out, "secret.step"), os.path.join(s.lib, "top.step"))
    tabs = s.get(f"/documents/{doc_id}/tabs")
    status, reply = _import(s, doc_id, {"root": "lib", "entry": eid})
    assert status == 409 and "rescan" in reply["error"], (status, reply)
    assert _import(s, doc_id, {"root": "lib", "entry": eid}, new_tab="1")[0] == 409
    assert s.get(f"/documents/{doc_id}/tabs") == tabs                   # the failed import left no new tab either
    assert s.request("POST", "/files/lib/rescan", {})[0] == 200          # after a rescan the link is simply gone
    assert [e["file"] for e in s.entries()] == []


def test_import_by_entry_refuses_a_file_over_the_limit_and_a_read_only_document(srv, monkeypatch):
    s = srv()
    doc_id = s.bracket()
    eid = s.entry_id("top.step")
    monkeypatch.setattr(SV, "MAX_UPLOAD", 100)
    status, reply = _import(s, doc_id, {"root": "lib", "entry": eid})
    assert status == 413, (status, reply)
    monkeypatch.undo()
    from freeshape import doc_lock
    s.request("POST", f"/documents/{doc_id}/close", {})
    lock = doc_lock.acquire(os.path.join(s.root, "Doc"), "desktop")
    try:
        assert s.request("POST", f"/documents/{doc_id}/open", {})[1]["read_only"] is True
        status, reply = _import(s, doc_id, {"root": "lib", "entry": eid})
        assert status == 423, (status, reply)
    finally:
        lock.release()


def test_an_upload_is_still_taken_and_other_content_types_are_415(srv, tmp_path):
    s = srv()
    doc_id = s.bracket()
    status, reply = _import(s, doc_id, _box_bytes(tmp_path), ctype="application/octet-stream", name="up.step")
    assert status == 200 and reply["feature"]["name"] == "Import up.step"
    assert _import(s, doc_id, b"x", ctype="text/plain")[0] == 415


# =========================================================================== the command line

def _cli(*args, cwd):
    return subprocess.run([sys.executable, "-m", "freeshape", "serve", str(cwd), *args], capture_output=True, text=True,
                          timeout=120, cwd=str(cwd), env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                                                              PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")))


def test_a_bad_file_root_name_or_a_missing_folder_stops_serve_with_a_message(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    lib = tmp_path / "lib"
    lib.mkdir()
    for arg, word in ((f"Bad_Name={lib}", "NAME"), (f"lib={tmp_path}/missing", "not an existing folder"),
                      ("justaname", "NAME"), (f"={lib}", "NAME"), (f"{'x' * 33}={lib}", "NAME")):
        done = _cli("--port", "0", "--file-root", arg, cwd=folder)
        assert done.returncode == 2 and "--file-root" in done.stderr and word in done.stderr, (arg, done.stderr)
        assert done.stdout == "" and "Traceback" not in done.stderr
    done = _cli("--port", "0", "--file-root", f"lib={lib}", "--file-root", f"lib={tmp_path}", cwd=folder)
    assert done.returncode == 2 and "different" in done.stderr


def test_serve_with_a_file_root_answers_on_the_command_line(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    lib, _ = make_lib(tmp_path)
    proc = subprocess.Popen([sys.executable, "-m", "freeshape", "serve", str(folder), "--port", "0",
                             "--file-root", f"lib={lib}", "--data-dir", str(tmp_path / "data")],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(folder),
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                                     PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")))
    try:
        line = proc.stdout.readline()
        assert line.startswith(SV.READY_PREFIX), line
        port = int(line.split("port=")[1].split()[0])
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        c.request("GET", "/files", headers={"Host": f"127.0.0.1:{port}"})
        r = c.getresponse()
        assert r.status == 200 and json.loads(r.read())["roots"][0]["name"] == "lib"
    finally:
        proc.terminate()
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()
