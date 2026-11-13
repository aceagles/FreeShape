"""Headless serve (I5.4, ADR 0010): one Document session over loopback HTTP. Standard library only, no Qt.

    python -m freeshape serve <Document folder> [--port N]

Prints one line on stdout when it is listening:  `FREESHAPE_SERVE_READY port=<N> url=http://127.0.0.1:<N>/`
(`--port 0` picks a free port). SIGINT and SIGTERM stop the server, then close the session, so no worker is left.

The server binds 127.0.0.1 and nothing else; there is no option for another address and no authentication
(ADR 0010). `--host` exists only to refuse: `127.0.0.1` and `localhost` are accepted (both bind 127.0.0.1).

Routes (an exact table; no part of a request is ever joined to a file path):
    GET  /                      the viewer page (freeshape/static/viewer.html, package data; 404 if not installed)
    GET  /health                {"ok", "version", "qt_imported", "qt_modules"}
    GET  /tabs                  {"document": {"id", "name"}, "tabs": [session.tabs() entries]}
    GET  /tabs/<id>/outputs     the tab's outputs in the engine wire format (application/octet-stream)
    GET  /tabs/<id>/mesh.json   display meshes, see `mesh_document`
    POST /tabs/<id>/edit        body: a contract edit dict, e.g. {"op": "set_param", ...}; reply {"rebuilt": [ids]}
    POST /save                  body: {} ; reply {"saved": [ids]}

Every call into the session holds one lock (the session has none); /health and the other routes that need no
session never wait for it. Errors are JSON {"error": message}: 400 a bad edit or body, 403/421 a wrong Host
or Origin, 404 an unknown route or tab, 405 a wrong method, 411 no Content-Length on a POST, 413 a body over
1 MB, 415 a POST that is not application/json, 500 anything unexpected (generic body; detail on stderr).
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND_HOST = "127.0.0.1"
MAX_BODY = 1024 * 1024
CONNECTION_TIMEOUT = 15.0       # seconds a client may stay silent on one socket call
BODY_DEADLINE = 30.0            # seconds for the whole body of one request
DEFAULT_PORT = 8765
READY_PREFIX = "FREESHAPE_SERVE_READY"
_TAB_PATH = re.compile(r"^/tabs/([A-Za-z0-9_-]{1,64})/(outputs|mesh\.json|edit)$")
_FIXED = {"/": ("GET",), "/health": ("GET",), "/tabs": ("GET",), "/save": ("POST",)}
_EDGE_METHODS = {"outputs": "GET", "mesh.json": "GET", "edit": "POST"}
_VIEWER_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
               "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_REMOTE_MESSAGE = ("remote access needs authentication that does not exist yet; "
                   "serve binds 127.0.0.1 only (use a port forward or the tunnel)")


def _viewer_page():
    """The viewer page bytes read from the package data, or None when the file is not installed."""
    try:
        from importlib import resources
        return resources.files("freeshape").joinpath("static/viewer.html").read_bytes()
    except (OSError, ModuleNotFoundError, FileNotFoundError):
        return None


def _qt_modules():
    return sorted(m for m in sys.modules if m.split(".")[0] in ("PySide6", "PyQt5", "PyQt6", "PySide2", "shiboken6"))


def _clean(text, limit=200):
    """`text` safe for a log line: control characters dropped, length capped."""
    return "".join(c for c in text if c.isprintable())[:limit]


def _vec(a):
    return [float(x) for x in a]


def mesh_document(out):
    """The mesh JSON for a tab's outputs (a StudioOutputs or an AssemblyOutputs), or None for any other kind.

    {"tab", "kind", "revision", "errors": {id: message},
     "meshes": [{"id", "name", "color": [r, g, b], "hidden", "part", "matrix", "mesh"}, ...]}

    A studio has one entry per part, in the studio's part order: "part" is null and "matrix" is null (the part
    is in the studio's own coordinates). An Assembly has one entry per instance, in the Assembly's order: "id"
    and "name" are the instance's, "color" its colour, "part" the id of the part it shows, "matrix" its
    placement as 16 numbers in COLUMN-MAJOR order (m[12], m[13], m[14] are the translation; pass it to WebGL's
    uniformMatrix4fv as it is). "color" is three numbers 0..1. "mesh" is `wire.mesh_json` of the part (see
    there for the keys, all indices 0-based) or null when the part has no mesh; an instance of a part
    shares the same mesh values as its sibling instances. A studio part's entry also carries "hidden"."""
    from .engine import contract as C
    from .engine import wire
    meshes = []
    if isinstance(out, C.StudioOutputs):
        for p in out.parts:
            meshes.append({"id": p.id, "name": p.name, "color": _vec(p.color)[:3], "hidden": bool(p.hidden),
                           "part": None, "matrix": None,
                           "mesh": wire.mesh_json(p.mesh) if p.mesh is not None else None})
    elif isinstance(out, C.AssemblyOutputs):
        cache = {}
        for inst in out.instances:
            part = out.parts.get(inst.part) if inst.part is not None else None
            mesh = None
            if part is not None and part.mesh is not None:
                if inst.part not in cache:
                    cache[inst.part] = wire.mesh_json(part.mesh)
                mesh = cache[inst.part]
            flat = [float(inst.T[r][c]) for c in range(4) for r in range(4)]      # column-major
            meshes.append({"id": inst.id, "name": inst.name, "color": _vec(inst.color)[:3],
                           "hidden": bool(inst.hidden), "part": inst.part, "matrix": flat, "mesh": mesh})
    else:
        return None
    return {"tab": out.tab, "kind": out.kind, "revision": out.revision,
            "errors": {str(k): str(v) for k, v in (out.errors or {}).items()}, "meshes": meshes}


class _Server(ThreadingHTTPServer):
    address_family = __import__("socket").AF_INET
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, session, port=0):
        from .session import SessionError
        self.session = session
        self.session_error = SessionError
        self.lock = threading.Lock()
        self.tab_ids = frozenset(t["id"] for t in session.tabs())     # the manifest is fixed for a session
        super().__init__((BIND_HOST, port), _Handler)
        self.port = self.server_address[1]
        self.hosts = {f"{BIND_HOST}:{self.port}", f"localhost:{self.port}"}
        self.origins = {f"http://{h}" for h in self.hosts}


class _Handler(BaseHTTPRequestHandler):
    server_version = "freeshape-serve"
    sys_version = ""
    timeout = CONNECTION_TIMEOUT

    # ------------------------------------------------------------------ plumbing
    def __getattr__(self, name):
        if name.startswith("do_"):          # any other method (PUT, DELETE, HEAD, OPTIONS, ...) is refused
            return self._not_allowed
        raise AttributeError(name)

    def log_request(self, code="-", size="-"):
        self._log(f"{self.command} {_clean(self.path or '')} {code}")

    def log_message(self, format, *args):                   # noqa: A002  (http.server's own messages)
        self._log(_clean(format % args))

    def log_error(self, format, *args):                     # noqa: A002
        self._log(_clean(format % args))

    def _log(self, line):
        try:
            sys.stderr.write(f"[serve] {line}\n")
            sys.stderr.flush()
        except Exception:                                    # noqa: BLE001
            pass

    def send_error(self, code, message=None, explain=None):
        """http.server's own errors (bad request line, too long, ...) as JSON, never reflecting the request."""
        self._json(code, {"error": http.client.responses.get(code, "error")}, close=True)

    def _send(self, code, body, ctype, headers=(), close=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj, headers=(), close=False):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", headers, close)

    def _fail(self, code, message, headers=()):
        self._json(code, {"error": message}, headers, close=True)

    def _not_allowed(self):
        if self._guard():
            self._fail(405, "method not allowed", [("Allow", "GET, POST")])

    # ------------------------------------------------------------------ checks
    def _guard(self):
        """Host and Origin checks; sends the refusal and returns False when the request fails them."""
        if self.headers.get("Host") not in self.server.hosts:
            self._fail(421, "unexpected Host header")          # DNS rebinding: a name that is not ours
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.origins:
            self._fail(403, "cross-origin requests are refused")
            return False
        return True

    def _route(self, method):
        """(route name, tab id or None) for this request, or sends 404 / 405 and returns None."""
        path = self.path.split("?", 1)[0]
        if path in _FIXED:
            if method not in _FIXED[path]:
                self._fail(405, "method not allowed", [("Allow", _FIXED[path][0])])
                return None
            return path, None
        m = _TAB_PATH.match(path)
        if m is None or m.group(1) not in self.server.tab_ids:
            self._fail(404, "not found")
            return None
        want = _EDGE_METHODS[m.group(2)]
        if method != want:
            self._fail(405, "method not allowed", [("Allow", want)])
            return None
        return m.group(2), m.group(1)

    def _body(self):
        """The JSON object of a POST body, or None after sending the refusal."""
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self._fail(415, "Content-Type must be application/json")
            return None
        if self.headers.get("Transfer-Encoding") is not None or self.headers.get("Content-Length") is None:
            self._fail(411, "Content-Length is required")
            return None
        raw = self.headers.get("Content-Length").strip()
        if not raw.isascii() or not raw.isdigit():
            self._fail(400, "Content-Length is not a number")
            return None
        n = int(raw)
        if n > MAX_BODY:
            self._fail(413, f"body over {MAX_BODY} bytes")
            return None
        chunks, got, deadline = [], 0, time.monotonic() + BODY_DEADLINE
        while got < n:
            if time.monotonic() > deadline:
                self._fail(408, "request body too slow")
                return None
            try:
                part = self.rfile.read(min(n - got, 65536))
            except OSError:
                self.close_connection = True
                return None
            if not part:
                self._fail(400, "body shorter than Content-Length")
                return None
            chunks.append(part)
            got += len(part)
        try:
            obj = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._fail(400, "body is not valid JSON")
            return None
        if not isinstance(obj, dict):
            self._fail(400, "body must be a JSON object")
            return None
        return obj

    # ------------------------------------------------------------------ methods
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        if not self._guard():
            return
        found = self._route(method)
        if found is None:
            return
        route, tab = found
        body = None
        if method == "POST":
            body = self._body()
            if body is None:
                return
        try:
            self._handle(route, tab, body)
        except self.server.session_error as e:
            self._fail(400, str(e))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:                               # noqa: BLE001
            import traceback
            self._log(f"500 {self.command} {_clean(self.path)}: {type(e).__name__}: {_clean(str(e), 500)}")
            traceback.print_exc(file=sys.stderr)
            self._fail(500, "internal error")

    def _handle(self, route, tab, body):
        srv = self.server
        if route == "/":
            page = _viewer_page()
            if page is None:
                self._send(404, b"The viewer page is not installed; see /tabs and /tabs/<id>/mesh.json.\n",
                           "text/plain; charset=utf-8")
            else:
                self._send(200, page, "text/html; charset=utf-8", [("Content-Security-Policy", _VIEWER_CSP)])
        elif route == "/health":
            from . import __version__
            qt = _qt_modules()
            self._json(200, {"ok": True, "version": __version__, "qt_imported": bool(qt), "qt_modules": qt})
        elif route == "/tabs":
            with srv.lock:
                tabs = srv.session.tabs()
            self._json(200, {"document": {"id": srv.session.id, "name": srv.session.name}, "tabs": tabs})
        elif route == "outputs":
            with srv.lock:
                data = srv.session.outputs_wire(tab)
            self._send(200, bytes(data), "application/octet-stream")
        elif route == "mesh.json":
            with srv.lock:
                out = srv.session.outputs(tab)
            doc = mesh_document(out)
            if doc is None:
                self._fail(404, f"tab {tab!r} ({out.kind}) has no mesh")
            else:
                self._json(200, doc)
        elif route == "edit":
            with srv.lock:
                rebuilt = srv.session.submit_edit(tab, body)
            self._json(200, {"rebuilt": rebuilt})
        elif route == "/save":
            with srv.lock:
                saved = srv.session.save()
            self._json(200, {"saved": saved})


def make_server(session, port=0):
    """A server on 127.0.0.1:`port` over `session` (not started). Callers run `serve_forever` and, at the end,
    `shutdown`, `server_close` and close the session."""
    return _Server(session, port)


def close_all(server, session, wait=10.0):
    """Stop `server` and close `session`, waiting up to `wait` seconds for a call in flight (its lock)."""
    server.shutdown()
    server.server_close()
    got = server.lock.acquire(timeout=wait)
    try:
        session.close()
    finally:
        if got:
            server.lock.release()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m freeshape serve",
                                 description="Serve one Document over HTTP on 127.0.0.1 (no authentication).")
    ap.add_argument("document", help="the Document folder")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on (0 picks a free one)")
    ap.add_argument("--host", default=BIND_HOST, help="only 127.0.0.1 or localhost; anything else is refused")
    args = ap.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost"):
        print(f"freeshape serve: refusing --host {_clean(args.host, 80)!r}: {_REMOTE_MESSAGE}.", file=sys.stderr)
        return 2
    if not 0 <= args.port <= 65535:
        print("freeshape serve: --port must be 0..65535.", file=sys.stderr)
        return 2
    import os
    if not os.path.isdir(args.document):
        print(f"freeshape serve: {args.document!r} is not a folder.", file=sys.stderr)
        return 2
    from .session import DocumentSession, SessionError
    try:
        session = DocumentSession(args.document)
    except SessionError as e:
        print(f"freeshape serve: cannot open the Document: {e}", file=sys.stderr)
        return 1
    try:
        server = make_server(session, args.port)
    except OSError as e:
        session.close()
        print(f"freeshape serve: cannot listen on {BIND_HOST}:{args.port}: {e}", file=sys.stderr)
        return 1
    except BaseException:
        session.close()
        raise
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    thread.start()
    print(f"{READY_PREFIX} port={server.port} url=http://{BIND_HOST}:{server.port}/", flush=True)
    try:
        while not stop.wait(0.5):
            pass
    finally:
        close_all(server, session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
