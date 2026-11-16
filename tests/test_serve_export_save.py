"""I11.13: POST /documents/<id>/tabs/<tab>/export (save an export into a server export root), the `export_roots` of
GET /files and `freeshape serve --export-root`. Each test builds its own folders and server, so each passes alone."""
import http.client
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import urllib.parse

import pytest

from freeshape import doc_registry as R
from freeshape import export_roots as ER
from freeshape import serve as SV
from freeshape.engine import process as P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples", "documents")


class _Srv:
    def __init__(self, tmp_path, export_roots=True, file_roots=()):
        self.tmp = tmp_path
        self.root = str(tmp_path / "Documents")
        self.out = str(tmp_path / "out")
        os.makedirs(self.root)
        os.makedirs(self.out)
        self.registry = R.DocRegistry(os.path.join(str(tmp_path / "data"), R.REGISTRY_NAME))
        self.server = SV.make_server(None, 0, registry=self.registry, root=self.root, file_roots=list(file_roots),
                                     export_roots=[("out", self.out)] if export_roots else [])
        self.port = self.server.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        assert self.server.wait_started(30)

    def document(self, example="bracket"):
        path = os.path.join(self.root, example)
        shutil.copytree(os.path.join(EXAMPLES, example), path)
        with open(os.path.join(path, "document.json")) as fh:
            doc_id = json.load(fh)["id"]
        self.registry.register_created(doc_id, path)
        return doc_id, os.path.realpath(path)

    def request(self, method, path, body=None, headers=(), raw=None, length=None):
        """(status, reply as JSON or bytes, raw bytes)"""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=300)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            c.putheader("Host", f"127.0.0.1:{self.port}")
            c.putheader("X-Client-Id", "client-aaaa1111")
            for k, v in headers:
                c.putheader(k, v)
            data = None
            if method == "POST":
                data = raw if raw is not None else json.dumps(body if body is not None else {}).encode()
                c.putheader("Content-Type", "application/json")
                c.putheader("Content-Length", str(len(data) if length is None else length))
            c.endheaders()
            if data is not None:
                try:
                    c.send(data)
                except OSError:
                    pass                                         # the server answered a 413 before reading it all
            r = c.getresponse()
            payload = r.read()
            text = payload
            if r.getheader("Content-Type", "").startswith("application/json"):
                text = json.loads(payload)
            return r.status, text, payload
        finally:
            c.close()

    def ok(self, method, path, body=None):
        status, reply, _ = self.request(method, path, body)
        assert status == 200, (method, path, status, reply)
        return reply

    def save(self, doc_id, body, tab="T1", **kw):
        return self.request("POST", f"/documents/{doc_id}/tabs/{tab}/export", body, **kw)

    def get_export(self, doc_id, tab="T1", **query):
        q = "?" + urllib.parse.urlencode(query)
        status, _, payload = self.request("GET", f"/documents/{doc_id}/tabs/{tab}/export{q}")
        assert status == 200
        return payload

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


def _open(srv, example="bracket", **kw):
    s = srv(**kw)
    doc_id, folder = s.document(example)
    s.ok("POST", f"/documents/{doc_id}/open")
    return s, doc_id, folder


def _listing(path):
    return sorted(os.listdir(path))


def test_saving_an_stl_writes_the_tab_named_file_byte_equal_to_the_download(srv):
    s, doc_id, _ = _open(srv)
    body = {"root": "out", "format": "stl", "preset": "Coarse"}
    status, reply, raw = s.save(doc_id, body)
    assert status == 201, reply
    assert reply == {"root": "out", "name": "bracket.stl", "bytes": reply["bytes"], "replaced": False, "skipped": []}
    with open(os.path.join(s.out, "bracket.stl"), "rb") as fh:
        saved = fh.read()
    assert saved == s.get_export(doc_id, format="stl", preset="Coarse") and len(saved) == reply["bytes"] > 1000
    assert s.out not in raw.decode() and str(s.tmp) not in raw.decode()
    assert _listing(s.out) == ["bracket.stl"]
    st = os.lstat(os.path.join(s.out, "bracket.stl"))
    assert stat.S_IMODE(st.st_mode) == 0o644


def test_saving_a_step_writes_the_tab_named_file_byte_equal_to_the_download(srv):
    s, doc_id, _ = _open(srv)
    status, reply, raw = s.save(doc_id, {"root": "out", "format": "step"})
    assert status == 201 and reply["name"] == "bracket.step", reply
    def untimed(raw):             # the STEP header holds the time it was written; OCP counts assembly usages per process
        raw = re.sub(rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", b"T", raw)
        return re.sub(rb"(NEXT_ASSEMBLY_USAGE_OCCURRENCE\(')\d+", rb"\1N", raw)
    with open(os.path.join(s.out, "bracket.step"), "rb") as fh:
        saved = fh.read()
    assert untimed(saved) == untimed(s.get_export(doc_id, format="step")) and len(saved) == reply["bytes"] > 1000
    assert s.out not in raw.decode() and str(s.tmp) not in raw.decode()


def test_a_given_name_is_cleaned_with_the_formats_extension(srv):
    s, doc_id, _ = _open(srv)
    status, reply, _ = s.save(doc_id, {"root": "out", "format": "step", "name": "My Part.STL"})
    assert status == 201 and reply["name"] == "My Part.step" and _listing(s.out) == ["My Part.step"]


def test_a_second_save_of_the_same_name_is_409_and_replace_true_replaces(srv):
    s, doc_id, _ = _open(srv)
    assert s.save(doc_id, {"root": "out", "format": "stl", "preset": "Coarse"})[0] == 201
    path = os.path.join(s.out, "bracket.stl")
    with open(path, "rb") as fh:
        first = fh.read()
    status, reply, raw = s.save(doc_id, {"root": "out", "format": "stl", "preset": "Fine"})
    assert status == 409 and reply["error"] == "exists" and reply["name"] == "bracket.stl"
    assert s.out not in raw.decode()
    with open(path, "rb") as fh:
        assert fh.read() == first and _listing(s.out) == ["bracket.stl"]
    status, reply, _ = s.save(doc_id, {"root": "out", "format": "stl", "preset": "Fine", "replace": True})
    assert status == 201 and reply["replaced"] is True
    with open(path, "rb") as fh:
        assert fh.read() == s.get_export(doc_id, format="stl", preset="Fine") != first
    assert _listing(s.out) == ["bracket.stl"]


def test_names_that_try_to_leave_the_folder_save_one_file_inside_it(srv):
    s, doc_id, _ = _open(srv)
    parent = str(s.tmp)
    before = _listing(parent)
    want = {"../x": "x.stl", "/etc/passwd": "passwd.stl", "a/b": "b.stl", "..": "export.stl"}
    for name, saved in want.items():
        status, reply, raw = s.save(doc_id, {"root": "out", "format": "stl", "name": name, "preset": "Coarse"})
        assert status == 201 and reply["name"] == saved, (name, reply)
        assert s.out not in raw.decode()
    assert _listing(s.out) == sorted(want.values())
    assert _listing(parent) == before                           # nothing new beside the root, nor above it
    assert not os.path.exists("/etc/passwd.stl")


def test_a_symlink_a_folder_and_a_fifo_in_the_way_are_409_with_and_without_replace(srv):
    s, doc_id, _ = _open(srv)
    outside = s.tmp / "outside.txt"
    outside.write_bytes(b"precious")
    os.symlink(str(outside), os.path.join(s.out, "link.stl"))
    os.mkdir(os.path.join(s.out, "dir.stl"))
    os.mkfifo(os.path.join(s.out, "fifo.stl"))
    for name in ("link", "dir", "fifo"):
        for replace in (False, True):
            status, reply, raw = s.save(doc_id, {"root": "out", "format": "stl", "name": name, "replace": replace})
            assert status == 409 and "error" in reply and s.out not in raw.decode(), (name, replace, reply)
    assert outside.read_bytes() == b"precious" and _listing(s.out) == ["dir.stl", "fifo.stl", "link.stl"]


def test_a_root_swapped_for_a_symlink_after_start_is_refused(srv):
    s, doc_id, _ = _open(srv)
    other = s.tmp / "other"
    other.mkdir()
    shutil.rmtree(s.out)
    os.symlink(str(other), s.out)
    status, reply, raw = s.save(doc_id, {"root": "out", "format": "stl"})
    assert status == 409 and os.listdir(other) == [] and str(s.tmp) not in raw.decode()


def test_an_unknown_root_and_a_file_roots_name_are_404_and_the_file_root_is_unchanged(srv, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.step").write_bytes(b"ISO-10303-21;")
    s2 = srv(file_roots=[("lib", str(lib))])
    doc_id, _ = s2.document()
    s2.ok("POST", f"/documents/{doc_id}/open")
    for root in ("nope", "lib"):
        status, reply, _ = s2.save(doc_id, {"root": root, "format": "stl"})
        assert status == 404, (root, reply)
    assert _listing(lib) == ["a.step"] and _listing(s2.out) == []
    for body in ({"format": "stl"}, {"root": 5, "format": "stl"}, {"root": ["out"], "format": "stl"}):
        assert s2.save(doc_id, body)[0] == 400
    assert s2.request("GET", "/files/lib")[0] == 200 and s2.request("GET", "/files/out")[0] == 404


def test_with_no_export_root_the_list_is_empty_and_the_post_is_404(srv):
    s, doc_id, _ = _open(srv, export_roots=False)
    status, reply, _ = s.request("GET", "/files")
    assert status == 200 and reply == {"roots": [], "export_roots": []}
    assert s.save(doc_id, {"root": "out", "format": "stl"})[0] == 404
    assert _listing(s.out) == []


def test_files_lists_an_export_root_by_name_and_limit_only_and_it_is_not_browsed(srv):
    s = srv()
    status, reply, raw = s.request("GET", "/files")
    assert status == 200 and reply == {"roots": [], "export_roots": [{"name": "out", "max_bytes": SV.MAX_EXPORT_SAVE}]}
    assert s.out not in raw.decode() and str(s.tmp) not in raw.decode()
    for path in ("/files/out", "/files/out/rescan"):
        assert s.request("GET" if path == "/files/out" else "POST", path)[0] == 404


def test_bytes_over_the_save_limit_are_413_and_nothing_is_written(srv, monkeypatch):
    s, doc_id, _ = _open(srv)
    monkeypatch.setattr(SV, "MAX_EXPORT_SAVE", 100)
    status, reply, _ = s.save(doc_id, {"root": "out", "format": "stl"})
    assert status == 413 and "error" in reply and _listing(s.out) == []


def test_a_body_over_max_body_is_413_and_a_foreign_origin_is_refused(srv):
    s, doc_id, _ = _open(srv)
    big = json.dumps({"root": "out", "format": "stl", "name": "x" * (SV.MAX_BODY + 10)}).encode()
    assert s.save(doc_id, None, raw=big)[0] == 413
    assert s.save(doc_id, {"root": "out", "format": "stl"}, headers=[("Origin", "https://evil.example")])[0] == 403
    assert s.save(doc_id, {"root": "out", "format": "stl"}, headers=[("Origin", "http://localhost:1")])[0] == 403
    assert _listing(s.out) == []


def test_a_read_only_document_can_save_an_export(srv):
    from freeshape import doc_lock
    s = srv()
    doc_id, folder = s.document()
    lock = doc_lock.acquire(folder, "desktop")
    try:
        assert s.ok("POST", f"/documents/{doc_id}/open")["read_only"] is True
        assert s.save(doc_id, {"root": "out", "format": "stl"})[0] == 201
        assert _listing(s.out) == ["bracket.stl"]
    finally:
        lock.release()


def test_bad_requests_are_400_and_write_nothing(srv):
    s, doc_id, _ = _open(srv)
    for body in ({"root": "out", "format": "stl", "parts": ["nope"]}, {"root": "out", "format": "obj"},
                 {"root": "out"}, {"root": "out", "format": "stl", "preset": "Huge"},
                 {"root": "out", "format": "stl", "replace": "yes"}, {"root": "out", "format": "stl", "name": 5},
                 {"root": "out", "format": "stl", "path": "/tmp/x"}, {"root": "out", "format": "stl", "parts": []}):
        status, reply, raw = s.save(doc_id, body)
        assert status == 400 and "error" in reply, (body, reply)
        assert s.out not in raw.decode()
    assert s.save(doc_id, {"root": "out", "format": "step"}, tab="T9")[0] == 404
    assert _listing(s.out) == []


def test_a_full_folder_is_507_with_no_partial_file(srv, monkeypatch):
    s, doc_id, _ = _open(srv)
    import errno

    def full(fd, data):
        raise OSError(errno.ENOSPC, "full")
    monkeypatch.setattr(ER.os, "write", full)
    status, reply, raw = s.save(doc_id, {"root": "out", "format": "stl"})
    monkeypatch.undo()
    assert status == 507 and s.out not in raw.decode() and _listing(s.out) == []


def test_the_write_runs_off_the_event_loop(srv, monkeypatch):
    s, doc_id, _ = _open(srv)
    gate, entered = threading.Event(), threading.Event()
    real = ER.ExportRoot.write

    def slow(self, name, data, replace=False):
        entered.set()
        assert gate.wait(60)
        return real(self, name, data, replace)
    monkeypatch.setattr(ER.ExportRoot, "write", slow)
    result = {}
    t = threading.Thread(target=lambda: result.update(r=s.save(doc_id, {"root": "out", "format": "stl"})))
    t.start()
    try:
        assert entered.wait(120)
        assert s.ok("GET", "/health")["ok"] is True              # the loop answers while the write is stuck
    finally:
        gate.set()
        t.join(120)
    assert result["r"][0] == 201 and _listing(s.out) == ["bracket.stl"]


def test_the_server_refuses_export_roots_that_overlap_or_share_a_name(tmp_path):
    docs, data, lib = tmp_path / "Documents", tmp_path / "data", tmp_path / "lib"
    for p in (docs, data, lib, docs / "inner"):
        p.mkdir(exist_ok=True)
    registry = R.DocRegistry(os.path.join(str(data), R.REGISTRY_NAME))
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    doc_dir = outside / "doc"
    shutil.copytree(os.path.join(EXAMPLES, "bracket"), doc_dir)
    with open(doc_dir / "document.json") as fh:
        registry.register_created(json.load(fh)["id"], str(doc_dir))
    for path in (docs, docs / "inner", tmp_path, data, lib, doc_dir, outside):
        with pytest.raises(ER.OverlapError):
            SV.make_server(None, 0, registry=registry, root=str(docs), data_dir=str(data),
                           file_roots=[("lib", str(lib))], export_roots=[("out", str(path))])
    free = tmp_path / "free"
    free.mkdir()
    for roots in ([("lib", str(free))], [("a", str(free)), ("a", str(free))]):
        with pytest.raises(ER.OverlapError):
            SV.make_server(None, 0, registry=registry, root=str(docs), file_roots=[("lib", str(lib))], export_roots=roots)


def _cli(*args, cwd):
    return subprocess.run([sys.executable, "-m", "freeshape", "serve", str(cwd), *args], capture_output=True, text=True,
                          timeout=120, cwd=str(cwd), env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                                                              PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")))


def test_serve_refuses_a_bad_export_root_on_the_command_line_with_a_message(tmp_path):
    docs, data, lib, ok = (tmp_path / n for n in ("docs", "data", "lib", "ok"))
    for p in (docs, data, lib, ok, docs / "inner"):
        p.mkdir()
    (tmp_path / "file").write_bytes(b"x")
    base = ("--port", "0", "--data-dir", str(data), "--file-root", f"lib={lib}")
    cases = [(f"Bad_Name={ok}", "NAME"), (f"out={tmp_path}/missing", "not an existing folder"),
             (f"out={tmp_path}/file", "not an existing folder"), (f"lib={ok}", "name"),
             (f"out={docs}/inner", "overlaps"), (f"out={tmp_path}", "overlaps"), (f"out={docs}", "overlaps"),
             (f"out={lib}", "overlaps"), (f"out={data}", "overlaps")]
    for arg, word in cases:
        done = _cli(*base, "--export-root", arg, cwd=docs)
        assert done.returncode == 2 and "--export-root" in done.stderr and word in done.stderr, (arg, done.stderr)
        assert done.stdout == "" and "Traceback" not in done.stderr
    done = _cli(*base, "--export-root", f"a={ok}", "--export-root", f"a={ok}", cwd=docs)
    assert done.returncode == 2 and "name" in done.stderr and "Traceback" not in done.stderr


def test_serve_with_an_export_root_starts_and_answers_on_the_command_line(tmp_path):
    docs, data, ok = (tmp_path / n for n in ("docs", "data", "shared"))
    for p in (docs, data, ok):
        p.mkdir()
    proc = subprocess.Popen([sys.executable, "-m", "freeshape", "serve", str(docs), "--port", "0", "--data-dir", str(data),
                             "--export-root", f"shared={ok}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=str(docs), env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                                                    PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")))
    try:
        line = proc.stdout.readline()
        assert line.startswith(SV.READY_PREFIX), line
        port = int(line.split("port=")[1].split()[0])
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        c.request("GET", "/files", headers={"Host": f"127.0.0.1:{port}"})
        r = c.getresponse()
        assert r.status == 200 and json.loads(r.read())["export_roots"] == [
            {"name": "shared", "max_bytes": SV.MAX_EXPORT_SAVE}]
    finally:
        proc.terminate()
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()
