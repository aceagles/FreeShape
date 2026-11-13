"""I5.4: the loopback serve command (freeshape/serve.py, ADR 0010).

The read routes run against one in-process server on port 0; the edit routes against another, on their own
Document. The command line runs as a subprocess. Every server and session is closed by a fixture finaliser, so no
worker survives a failing assertion."""
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest
from test_document_session import _alive, _layout_document

from freeshape import docstore as DS
from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.engine import wire
from freeshape.session import DocumentSession

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Served:
    """A running in-process server on 127.0.0.1 and the Document it serves."""

    def __init__(self, folder):
        self.fd, self.ids, self.ext = _layout_document(DS.FolderStore(folder))
        self.folder = folder
        self.session = DocumentSession(folder, spares=0)
        self.server = SV.make_server(self.session, 0)
        self.port = self.server.port
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def request(self, method, path, body=None, headers=None, raw=None):
        """(status, headers, body bytes). `body` is JSON-encoded unless `raw` bytes are given."""
        h = {"Host": f"127.0.0.1:{self.port}"}
        if method == "POST":
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                if v is not None:
                    c.putheader(k, v)
            if data is not None and "Content-Length" not in h:
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def get_json(self, path):
        status, _h, body = self.request("GET", path)
        assert status == 200, (path, status, body[:300])
        return json.loads(body)

    def close(self):
        try:
            SV.close_all(self.server, self.session)
        finally:
            P.SPARES.clear()


def _serve(tmp_path_factory, name, monkeypatch=None):
    mp = pytest.MonkeyPatch()
    user = tmp_path_factory.mktemp(name + "_plugins")
    mp.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    P.SPARES.clear()
    s = None
    try:
        s = Served(str(tmp_path_factory.mktemp(name) / "Doc"))
        return s, mp
    except BaseException:
        if s is not None:
            s.close()
        mp.undo()
        raise


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    s, mp = _serve(tmp_path_factory, "read")
    try:
        yield s
    finally:
        s.close()
        mp.undo()


@pytest.fixture
def editable(tmp_path_factory):
    s, mp = _serve(tmp_path_factory, "edit")
    try:
        yield s
    finally:
        s.close()
        mp.undo()


def check_mesh(m):
    """Lengths consistent, indices in range, face ranges covering every triangle once."""
    pos, nor, idx = m["positions"], m["normals"], m["indices"]
    assert len(pos) == len(nor) and len(pos) % 3 == 0 and len(pos) > 0
    assert len(idx) % 3 == 0 and len(idx) > 0
    nv, nt = len(pos) // 3, len(idx) // 3
    assert all(isinstance(i, int) and 0 <= i < nv for i in idx)
    assert len(m["face_ids"]) == nt
    covered = []
    for fid, (a, b) in enumerate(m["face_ranges"], start=1):
        assert 0 <= a <= b <= nt
        assert all(m["face_ids"][t] == fid for t in range(a, b))
        covered.extend(range(a, b))
    assert sorted(covered) == list(range(nt))
    assert all(len(e) % 3 == 0 for e in m["edges"]) and len(m["vertices"]) % 3 == 0


def check_doc(doc, kind, n):
    assert doc["kind"] == kind and len(doc["meshes"]) == n
    for e in doc["meshes"]:
        assert isinstance(e["name"], str) and len(e["color"]) == 3
        assert all(isinstance(c, float) and 0 <= c <= 1 for c in e["color"])
        check_mesh(e["mesh"])
        if kind == "assembly":
            assert len(e["matrix"]) == 16 and e["part"]
        else:
            assert e["matrix"] is None


def _extent(mesh):
    p = mesh["positions"]
    return [max(p[i::3]) - min(p[i::3]) for i in range(3)]


# =========================================================================== read routes

def test_health_reports_version_and_no_qt(served):
    from freeshape import __version__
    h = served.get_json("/health")
    assert h["ok"] is True and h["version"] == __version__
    # this server runs inside the test process, where an earlier test may have imported Qt; that serve itself
    # loads no Qt module is proven by the subprocess test below, which starts `python -m freeshape serve`
    assert isinstance(h["qt_modules"], list) and h["qt_imported"] is bool(h["qt_modules"])


def test_tabs_lists_the_documents_tabs(served):
    t = served.get_json("/tabs")
    ids = served.ids
    assert [(x["id"], x["kind"], x["state"]) for x in t["tabs"]] == [
        (ids["vars"], "variables", "ok"), (ids["a"], "studio", "ok"), (ids["b"], "studio", "ok"),
        (ids["asm"], "assembly", "ok")]
    assert t["document"]["name"] == "session"


def test_mesh_json_of_a_studio_and_an_assembly_validates(served):
    ids = served.ids
    st = served.get_json(f"/tabs/{ids['a']}/mesh.json")
    check_doc(st, "studio", 1)
    assert st["tab"] == ids["a"] and st["meshes"][0]["matrix"] is None
    assert sorted(round(x) for x in _extent(st["meshes"][0]["mesh"])) == [10, 10, 20]
    asm = served.get_json(f"/tabs/{ids['asm']}/mesh.json")
    check_doc(asm, "assembly", 2)
    assert [e["name"] for e in asm["meshes"]] == ["Plate A <1>", "Plate B <1>"]
    m = asm["meshes"][0]["matrix"]
    assert m[3] == m[7] == m[11] == 0 and m[15] == 1          # column-major: the last row sits at 3, 7, 11, 15


def test_a_tab_with_no_mesh_is_404(served):
    status, _h, body = served.request("GET", f"/tabs/{served.ids['vars']}/mesh.json")
    assert status == 404 and "no mesh" in json.loads(body)["error"]


def test_outputs_come_back_in_the_wire_format(served):
    status, h, body = served.request("GET", f"/tabs/{served.ids['asm']}/outputs")
    assert status == 200 and h["Content-Type"] == "application/octet-stream"
    out = wire.from_wire(body)
    assert out.tab == served.ids["asm"] and len(out.instances) == 2


def test_root_is_the_viewer_page(served):
    status, h, body = served.request("GET", "/")
    assert status == 200 and h["Content-Type"].startswith("text/html") and b"<title>FreeShape viewer</title>" in body


def test_the_socket_is_bound_to_loopback_only(served):
    assert served.server.socket.getsockname() == ("127.0.0.1", served.port)
    assert served.server.socket.family == socket.AF_INET


# =========================================================================== security

def test_every_response_has_the_safe_headers_and_no_cors(served):
    for status, h, _b in (served.request("GET", "/health"), served.request("GET", "/nope"),
                          served.request("PUT", "/health"),
                          served.request("POST", f"/tabs/{served.ids['a']}/edit", {"x": 1}, raw=b"[")):
        assert h["Content-Type"] and h["X-Content-Type-Options"] == "nosniff" and h["Cache-Control"] == "no-store"
        assert not any(k.lower().startswith("access-control-") for k in h)


def test_a_foreign_host_header_is_refused(served):
    for host in ("evil.example", "evil.example:%d" % served.port, "127.0.0.1", "localhost:1",
                 "127.0.0.1:%d.evil.example" % served.port, None):
        status, _h, _b = served.request("GET", "/health", headers={"Host": host})
        assert status == 421, host
    for host in (f"localhost:{served.port}", f"127.0.0.1:{served.port}"):
        assert served.request("GET", "/health", headers={"Host": host})[0] == 200


def test_a_foreign_origin_is_refused_and_our_own_is_allowed(served):
    for origin in ("http://evil.example", "null", f"http://127.0.0.1:{served.port + 1}",
                   f"https://127.0.0.1:{served.port}"):
        assert served.request("GET", "/health", headers={"Origin": origin})[0] == 403, origin
        assert served.request("POST", "/save", {}, headers={"Origin": origin})[0] == 403, origin
    assert served.request("GET", "/health", headers={"Origin": f"http://localhost:{served.port}"})[0] == 200


def test_a_post_must_be_json(served):
    path = f"/tabs/{served.ids['a']}/edit"
    for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None):
        assert served.request("POST", path, {"op": "undo"}, headers={"Content-Type": ctype})[0] == 415, ctype
    assert served.request("POST", "/save", raw=b"[]")[0] == 400          # json, but not an object


def test_options_is_not_answered_so_there_is_no_preflight(served):
    status, h, _b = served.request("OPTIONS", "/save", headers={"Origin": "http://evil.example",
                                                                 "Access-Control-Request-Method": "POST"})
    assert status in (403, 405) and "Access-Control-Allow-Origin" not in h
    assert served.request("OPTIONS", "/save")[0] == 405


def test_other_methods_are_405(served):
    for m in ("PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE", "FOO"):
        assert served.request(m, "/health")[0] == 405, m
    assert served.request("POST", "/health", {})[0] == 405
    assert served.request("GET", "/save")[0] == 405
    assert served.request("GET", f"/tabs/{served.ids['a']}/edit")[0] == 405
    assert served.request("POST", f"/tabs/{served.ids['a']}/mesh.json", {})[0] == 405


def test_body_limits(served):
    path = f"/tabs/{served.ids['a']}/edit"
    over = {"Content-Length": str(2 * 1024 * 1024)}
    assert served.request("POST", path, headers=over, raw=b"")[0] == 413         # refused on the header alone
    assert served.request("POST", path, headers={"Content-Length": str(SV.MAX_BODY + 1)}, raw=b"")[0] == 413
    assert served.request("POST", path, headers={"Content-Length": None}, raw=b"")[0] == 411
    assert served.request("POST", path, headers={"Content-Length": "abc"}, raw=b"")[0] == 400
    assert served.request("POST", path, headers={"Content-Length": "-5"}, raw=b"")[0] == 400
    assert served.request("POST", path, headers={"Transfer-Encoding": "chunked", "Content-Length": None},
                          raw=b"")[0] == 411
    big = b'{"op": "no_such_op", "pad": "' + b"x" * (SV.MAX_BODY - 40) + b'"}'
    assert len(big) < SV.MAX_BODY
    assert served.request("POST", path, raw=big)[0] == 400                      # under the cap: read and parsed


def test_a_short_body_is_400_and_a_stalled_client_is_dropped(served, monkeypatch):
    monkeypatch.setattr(SV._Handler, "timeout", 0.5)
    c = socket.create_connection(("127.0.0.1", served.port), timeout=10)
    try:
        c.sendall(f"POST /save HTTP/1.1\r\nHost: 127.0.0.1:{served.port}\r\nContent-Type: application/json\r\n"
                  "Content-Length: 50\r\n\r\n{".encode())
        t0 = time.monotonic()
        data = c.recv(4096)
        assert time.monotonic() - t0 < 8
        assert data == b"" or b" 400 " in data.split(b"\r\n", 1)[0] or b" 408 " in data.split(b"\r\n", 1)[0]
    finally:
        c.close()


def test_unknown_and_odd_paths_are_404(served):
    ids = served.ids
    for path in ("/nope", "/../document.json", "/tabs/../document.json", "/tabs/nope/outputs",
                 "/tabs/nope/mesh.json", f"/tabs/{ids['a']}/../{ids['b']}/outputs", f"/tabs/{ids['a']}%2f..%2fx",
                 "/%2e%2e/document.json", "/tabs/%2e%2e/outputs", f"/tabs/{ids['a']}/outputs/", "/tabs/",
                 "//etc/passwd", "/tabs//outputs", "/document.json", "/static/viewer.html",
                 f"/tabs/{ids['a'].lower()}x/outputs", "/health/..", "/health%00"):
        status, _h, body = served.request("GET", path)
        assert status == 404, (path, status)
        assert path.encode() not in body                                    # nothing reflected
    assert served.request("GET", "/health?x=1")[0] == 200
    assert served.request("POST", "/tabs/nope/edit", {"op": "undo"})[0] == 404
    assert served.request("POST", "/tabs/../save", {})[0] == 404


def test_a_malformed_edit_is_400_with_the_engines_message(served):
    path = f"/tabs/{served.ids['a']}/edit"
    for raw in (b"{", b"", b"\xff\xfe", b"[1]", b'"op"', b"null", b"3"):
        assert served.request("POST", path, raw=raw)[0] == 400, raw
    status, _h, body = served.request("POST", path, {"op": "set_param", "feature": "nope", "key": "x", "value": 1})
    assert status == 400 and "nope" in json.loads(body)["error"]
    status, _h, body = served.request("POST", path, {"op": "no_such_op"})
    assert status == 400 and json.loads(body)["error"]
    status, _h, body = served.request("POST", path, {"op": "set_param"})
    assert status == 400
    assert served.get_json("/tabs")["tabs"][1]["dirty"] is False             # nothing changed


def test_an_unexpected_error_is_a_generic_500(served, monkeypatch, capsys):
    def boom(_tab):
        raise RuntimeError("secret detail /home/x")
    monkeypatch.setattr(served.session, "outputs", boom)
    status, h, body = served.request("GET", f"/tabs/{served.ids['a']}/mesh.json")
    assert status == 500 and json.loads(body) == {"error": "internal error"}
    assert b"secret" not in body and h["Content-Type"].startswith("application/json")
    assert "secret detail" in capsys.readouterr().err


def test_health_does_not_wait_for_the_session_lock(served):
    served.server.lock.acquire()
    try:
        result = {}
        t = threading.Thread(target=lambda: result.update(h=served.request("GET", "/health")), daemon=True)
        t.start()
        t.join(10)
        assert result["h"][0] == 200
        blocked = threading.Thread(target=lambda: result.update(t=served.request("GET", "/tabs")), daemon=True)
        blocked.start()
        blocked.join(0.5)
        assert blocked.is_alive() and "t" not in result                      # /tabs waits for the lock
    finally:
        served.server.lock.release()
    blocked.join(30)
    assert result["t"][0] == 200


def test_each_request_logs_one_line_without_control_characters(served, capsys):
    served.request("GET", "/nope%0d%0aX")
    served.request("GET", "/tabs")
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("[serve]")]
    assert lines == ["[serve] GET /nope%0d%0aX 404", "[serve] GET /tabs 200"]
    assert SV._clean("/a\r\nb\x1b[0m") == "/ab[0m"


# =========================================================================== edits

def test_a_set_param_edit_rebuilds_the_studio_and_its_assembly(editable):
    ids = editable.ids
    before = {k: editable.get_json(f"/tabs/{ids[k]}/mesh.json") for k in ("a", "asm")}
    status, _h, body = editable.request("POST", f"/tabs/{ids['a']}/edit",
                                        {"op": "set_param", "feature": editable.ext["a"], "key": "depth", "value": 5})
    assert status == 200 and json.loads(body) == {"rebuilt": [ids["a"], ids["asm"]]}
    after = {k: editable.get_json(f"/tabs/{ids[k]}/mesh.json") for k in ("a", "asm")}
    for k, n in (("a", 1), ("asm", 2)):
        check_doc(after[k], "studio" if k == "a" else "assembly", n)
        assert after[k]["meshes"][0]["mesh"] != before[k]["meshes"][0]["mesh"]
    assert after["asm"]["meshes"][1]["mesh"] == before["asm"]["meshes"][1]["mesh"]       # Plate B is untouched
    assert sorted(round(x) for x in _extent(after["a"]["meshes"][0]["mesh"])) == [5, 10, 10]
    assert {t["id"] for t in editable.get_json("/tabs")["tabs"] if t["dirty"]} == {ids["a"]}


def test_save_writes_the_edit_and_a_fresh_session_sees_it(editable):
    ids = editable.ids
    editable.request("POST", f"/tabs/{ids['a']}/edit",
                     {"op": "set_param", "feature": editable.ext["a"], "key": "depth", "value": 5})
    status, _h, body = editable.request("POST", "/save", {})
    assert status == 200 and json.loads(body)["saved"] == list(ids.values())
    assert not any(t["dirty"] for t in editable.get_json("/tabs")["tabs"])
    with DocumentSession(editable.folder, spares=0) as fresh:
        assert len(fresh.outputs(ids["a"]).parts) == 1
        assert round(fresh.outputs(ids["a"]).parts[0].mesh.verts[:, 2].max() - fresh.outputs(ids["a"]).parts[0]
                     .mesh.verts[:, 2].min()) == 5


# =========================================================================== the command line

def _env():
    return dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
                QT_QPA_PLATFORM="offscreen")


def test_a_remote_host_is_refused_with_a_message(tmp_path):
    for host in ("0.0.0.0", "::", "192.168.1.5", "example.com", ""):
        done = subprocess.run([sys.executable, "-m", "freeshape", "serve", str(tmp_path), "--host", host],
                              capture_output=True, text=True, timeout=120, cwd=str(tmp_path), env=_env())
        assert done.returncode != 0 and "authentication that does not exist yet" in done.stderr, host
        assert done.stdout == ""


def _descendants(pid):
    kids = set()
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                with open(f"/proc/{p}/stat") as fh:
                    rest = fh.read().rsplit(")", 1)[1].split()
            except OSError:
                continue
            if int(rest[1]) == pid or int(rest[1]) in kids:
                kids.add(int(p))
    return kids


@pytest.mark.parametrize("sig,host", [(signal.SIGTERM, "localhost"), (signal.SIGINT, "127.0.0.1")])
def test_the_command_serves_loopback_without_qt_and_stops_on_a_signal(tmp_path, sig, host):
    folder = str(tmp_path / "Doc")
    _layout_document(DS.FolderStore(folder))
    proc = subprocess.Popen([sys.executable, "-m", "freeshape", "serve", folder, "--port", "0", "--host", host],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(tmp_path), env=_env())
    try:
        line = proc.stdout.readline()
        assert line.startswith(SV.READY_PREFIX + " port="), (line, proc.poll())
        port = int(line.split()[1].split("=")[1])
        assert line.split()[2] == f"url=http://127.0.0.1:{port}/"
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        c.request("GET", "/health")
        h = json.loads(c.getresponse().read())
        assert h["qt_imported"] is False and h["qt_modules"] == []
        c.close()
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        c.request("GET", "/tabs")
        assert len(json.loads(c.getresponse().read())["tabs"]) == 4
        c.close()
        workers = _descendants(proc.pid)
        assert workers                                                     # one per tab, plus the spare
        proc.send_signal(sig)
        assert proc.wait(60) == 0
        assert not [p for p in workers if _alive(p)]
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()
