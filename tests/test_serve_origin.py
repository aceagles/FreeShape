"""`freeshape serve --allow-origin` (I1.3): one narrow, explicit public origin; every other Host or Origin stays refused."""
import http.client
import json
import os
import shutil
import subprocess
import sys
import threading

import pytest

from freeshape import serve as SV
from freeshape.engine import process as P
from freeshape.session import DocumentSession

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC = "https://cad.example.test"
PUBLIC_HOST = "cad.example.test"


def _env():
    return dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
                QT_QPA_PLATFORM="offscreen")


class _Running:
    def __init__(self, tmp_path, allow):
        folder = str(tmp_path / "Doc")
        shutil.copytree(os.path.join(ROOT, "examples", "documents", "bracket"), folder)
        self.session = DocumentSession(folder, spares=0)
        self.server = SV.make_server(self.session, 0, allow)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        assert self.server.wait_started(30)

    def request(self, method, path, host=None, origin=None, headers=None):
        h = {"Host": host if host is not None else f"127.0.0.1:{self.server.port}"}
        if origin is not None:
            h["Origin"] = origin
        if method == "POST":
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        data = b"{}" if method == "POST" else None
        c = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=60)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if data is not None:
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            return r.status, r.read()
        finally:
            c.close()

    def close(self):
        try:
            SV.close_all(self.server, self.session)
        finally:
            P.SPARES.clear()


@pytest.fixture
def running(tmp_path):
    made = []

    def make(*allow):
        s = _Running(tmp_path, list(allow))
        made.append(s)
        return s
    try:
        yield make
    finally:
        for s in made:
            s.close()


def test_the_allowed_host_and_origin_are_accepted(running):
    s = running(PUBLIC)
    assert s.request("GET", "/health", host=PUBLIC_HOST)[0] == 200
    assert s.request("GET", "/health", host=PUBLIC_HOST + ":443")[0] == 200
    assert s.request("GET", "/health", host=PUBLIC_HOST, origin=PUBLIC)[0] == 200
    status, body = s.request("POST", "/save", host=PUBLIC_HOST, origin=PUBLIC)
    assert status == 200, body
    assert "saved" in json.loads(body)
    assert s.request("GET", "/health")[0] == 200                  # loopback still works


def test_an_allowed_origin_with_a_port_accepts_that_port(running):
    s = running("https://cad.example.test:8443")
    assert s.request("GET", "/health", host="cad.example.test:8443", origin="https://cad.example.test:8443")[0] == 200
    assert s.request("GET", "/health", host="cad.example.test:9999")[0] == 421
    assert s.request("GET", "/health", host="cad.example.test:8443", origin=PUBLIC)[0] == 403


def test_other_hosts_and_origins_are_still_refused(running):
    s = running(PUBLIC)
    assert s.request("GET", "/health", host="evil.example")[0] == 421
    assert s.request("GET", "/health", host=PUBLIC_HOST, origin="https://evil.example")[0] == 403
    assert s.request("POST", "/save", host=PUBLIC_HOST, origin="https://evil.example")[0] == 403
    assert s.request("GET", "/health", host=PUBLIC_HOST, origin="http://cad.example.test")[0] == 403


def test_without_allow_origin_the_public_host_is_421(running):
    s = running()
    assert s.request("GET", "/health", host=PUBLIC_HOST)[0] == 421
    assert s.request("GET", "/health", origin=PUBLIC)[0] == 403


def test_x_forwarded_headers_do_not_decide(running):
    s = running(PUBLIC)
    status, _ = s.request("GET", "/health", host="evil.example",
                          headers={"X-Forwarded-Host": PUBLIC_HOST, "X-Forwarded-Proto": "https"})
    assert status == 421
    status, _ = s.request("GET", "/health", host=PUBLIC_HOST,
                          headers={"Origin": "https://evil.example", "X-Forwarded-Host": PUBLIC_HOST})
    assert status == 403


@pytest.mark.parametrize("value", ["https://*.example.test", "http://cad.example.test", "https://cad.example.test/path",
                                   "https://cad.example.test/", "cad.example.test", "https://", "https://a b.test",
                                   "https://user@cad.example.test", "https://cad.example.test:0",
                                   "https://cad.example.test:99999", "https://[::1]", ""])
def test_a_bad_allow_origin_stops_serve_before_listening(tmp_path, value):
    done = subprocess.run([sys.executable, "-m", "freeshape", "serve", str(tmp_path), "--port", "0",
                           "--allow-origin", value],
                          capture_output=True, text=True, timeout=120, cwd=str(tmp_path), env=_env())
    assert done.returncode == 2, (value, done.stderr)
    assert "refusing --allow-origin" in done.stderr
    assert done.stdout == ""                                      # no ready line: it never listened


def test_host_0_0_0_0_is_still_refused(tmp_path):
    done = subprocess.run([sys.executable, "-m", "freeshape", "serve", str(tmp_path), "--host", "0.0.0.0",
                           "--allow-origin", PUBLIC],
                          capture_output=True, text=True, timeout=120, cwd=str(tmp_path), env=_env())
    assert done.returncode != 0 and "authentication that does not exist yet" in done.stderr
    assert done.stdout == ""


def test_check_host_origin_is_one_function_for_http_and_the_socket():
    hosts, origins = {"127.0.0.1:1"}, {"http://127.0.0.1:1"}
    assert SV.check_host_origin("127.0.0.1:1", None, hosts, origins) is None
    assert SV.check_host_origin(None, None, hosts, origins)[0] == 421
    assert SV.check_host_origin("127.0.0.1:1", "https://x.test", hosts, origins)[0] == 403
