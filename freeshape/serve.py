"""Headless serve (I5.4, ADR 0010; aiohttp since ADR 0014): one Document session over loopback HTTP. No Qt.

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

Every call into the session holds one lock (the session has none) and runs on a thread pool, never on the event
loop; /health and the other routes that need no session never wait for it. Errors are JSON {"error": message}: 400 a bad edit or body, 403/421 a wrong Host
or Origin, 404 an unknown route or tab, 405 a wrong method, 411 no Content-Length on a POST, 413 a body over
1 MB, 415 a POST that is not application/json, 500 anything unexpected (generic body; detail on stderr).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import signal
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

BIND_HOST = "127.0.0.1"
MAX_BODY = 1024 * 1024
CONNECTION_TIMEOUT = 15.0       # seconds an idle keep-alive connection is kept open
SESSION_THREADS = 8             # threads for session calls (they queue on the session lock, one runs at a time)
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


def _log(line):
    try:
        sys.stderr.write(f"[serve] {line}\n")
        sys.stderr.flush()
    except Exception:                                    # noqa: BLE001
        pass


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


_ORIGIN_RE = re.compile(r"https://((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?::([0-9]{1,5}))?")


def parse_allow_origin(value):
    """`(origin, hosts)` for one `--allow-origin` value: an exact `https://host[:port]` origin, no wildcard, userinfo,
    path, query or IP literal. `hosts` are the Host header values it admits (the port is optional). Raises ValueError."""
    m = _ORIGIN_RE.fullmatch(value.lower()) if isinstance(value, str) and value.isascii() else None
    if m is None:
        raise ValueError("must be an exact https://host[:port] origin, with no wildcard and no path")
    name, port = m.group(1), m.group(2)
    if port is not None and not 0 < int(port) <= 65535:
        raise ValueError("the port must be 1..65535")
    port = "443" if port is None else str(int(port))
    origins = {f"https://{name}"} if port == "443" else {f"https://{name}:{port}"}
    hosts = {name, f"{name}:{port}"} if port == "443" else {name, f"{name}:{port}"}
    if port == "443":
        origins.add(f"https://{name}:443")
    return origins, hosts


def check_host_origin(host, origin, hosts, origins):
    """None when the request may go on, else `(status, message)`: 421 for a Host that is not in `hosts`, 403 for an
    Origin (when one is sent) that is not in `origins`. Only the Host and Origin headers decide, never X-Forwarded-*.
    The HTTP handler and the WebSocket upgrade both call this."""
    if host is None or host.lower() not in hosts:
        return 421, "unexpected Host header"                     # DNS rebinding: a name that is not ours
    if origin is not None and origin not in origins:
        return 403, "cross-origin requests are refused"
    return None


class _Server:
    """The aiohttp server: bound to 127.0.0.1 when made, running once `serve_forever` is called (it blocks, on its
    own event loop, until `shutdown`). `lock` serialises every session call; those calls run on `pool`, never on
    the event loop."""

    def __init__(self, session, port=0, allow_origins=()):
        from .session import SessionError
        self.session = session
        self.session_error = SessionError
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=SESSION_THREADS, thread_name_prefix="serve-session")
        self.tab_ids = frozenset(t["id"] for t in session.tabs())     # the manifest is fixed for a session
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((BIND_HOST, port))
            sock.listen(128)
        except BaseException:
            sock.close()
            self.pool.shutdown(wait=False)
            raise
        self.socket = sock
        self.port = sock.getsockname()[1]
        self.hosts = {f"{BIND_HOST}:{self.port}", f"localhost:{self.port}"}
        self.origins = {f"http://{h}" for h in self.hosts}
        for value in allow_origins:                              # public origins named with --allow-origin
            o, h = parse_allow_origin(value)
            self.origins |= o
            self.hosts |= h
        self._loop = None
        self._stop = None
        self._stop_asked = False
        self._entered = False
        self._started = threading.Event()
        self._finished = threading.Event()

    # ------------------------------------------------------------------ life cycle
    def serve_forever(self, poll_interval=None):                 # poll_interval: kept for the old signature
        self._entered = True
        try:
            asyncio.run(self._main())
        finally:
            self._finished.set()

    async def _main(self):
        from aiohttp import web
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        runner = web.ServerRunner(web.Server(self._handle, access_log=None, keepalive_timeout=CONNECTION_TIMEOUT),
                                  handle_signals=False)
        await runner.setup()
        try:
            await web.SockSite(runner, self.socket).start()
            self._started.set()
            if not self._stop_asked:
                await self._stop.wait()
        finally:
            await runner.cleanup()

    def wait_started(self, timeout=30.0):
        return self._started.wait(timeout)

    def shutdown(self):
        """Stop `serve_forever` and wait for it to return (a no-op wait if it was never called)."""
        self._stop_asked = True
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None:
            try:
                loop.call_soon_threadsafe(stop.set)
            except RuntimeError:                                  # the loop has already closed
                pass
        if self._entered:
            self._finished.wait(30)

    def server_close(self):
        self.socket.close()
        self.pool.shutdown(wait=False)

    # ------------------------------------------------------------------ responses
    @staticmethod
    def _send(code, body, ctype, headers=(), close=False):
        from aiohttp import web
        resp = web.Response(status=code, body=body)
        resp.headers["Content-Type"] = ctype
        resp.headers["Server"] = "freeshape-serve"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        for k, v in headers:
            resp.headers[k] = v
        if close:
            resp.force_close()
        return resp

    def _json(self, code, obj, headers=(), close=False):
        return self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", headers, close)

    def _fail(self, code, message, headers=()):
        return self._json(code, {"error": message}, headers, close=True)

    # ------------------------------------------------------------------ one request
    async def _handle(self, request):
        try:
            resp = await self._respond(request)
        except self.session_error as e:
            resp = self._fail(400, str(e))
        except Exception as e:                                   # noqa: BLE001
            import traceback
            _log(f"500 {request.method} {_clean(request.raw_path)}: {type(e).__name__}: {_clean(str(e), 500)}")
            traceback.print_exc(file=sys.stderr)
            resp = self._fail(500, "internal error")
        _log(f"{request.method} {_clean(request.raw_path)} {resp.status}")
        return resp

    async def _respond(self, request):
        method = request.method
        refused = check_host_origin(request.headers.get("Host"), request.headers.get("Origin"), self.hosts, self.origins)
        if refused is not None:
            return self._fail(*refused)
        if method not in ("GET", "POST"):                         # PUT, DELETE, HEAD, OPTIONS, ... are refused
            return self._fail(405, "method not allowed", [("Allow", "GET, POST")])
        # the route comes from the raw request target, never from a decoded or normalised path
        path = request.raw_path.split("?", 1)[0]
        tab = None
        if path in _FIXED:
            route, want = path, _FIXED[path][0]
        else:
            m = _TAB_PATH.match(path)
            if m is None or m.group(1) not in self.tab_ids:
                return self._fail(404, "not found")
            route, tab = m.group(2), m.group(1)
            want = _EDGE_METHODS[route]
        if method != want:
            return self._fail(405, "method not allowed", [("Allow", want)])
        body = None
        if method == "POST":
            body = await self._body(request)
            if not isinstance(body, dict):
                return body                                        # the refusal response
        return await self._route(route, tab, body)

    async def _body(self, request):
        """The JSON object of a POST body, or the refusal response."""
        ctype = (request.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return self._fail(415, "Content-Type must be application/json")
        raw = request.headers.get("Content-Length")
        if request.headers.get("Transfer-Encoding") is not None or raw is None:
            return self._fail(411, "Content-Length is required")
        raw = raw.strip()
        if not raw.isascii() or not raw.isdigit():
            return self._fail(400, "Content-Length is not a number")
        n = int(raw)
        if n > MAX_BODY:
            return self._fail(413, f"body over {MAX_BODY} bytes")
        try:
            data = await asyncio.wait_for(request.content.readexactly(n), BODY_DEADLINE)
        except asyncio.TimeoutError:
            return self._fail(408, "request body too slow")
        except asyncio.IncompleteReadError:
            return self._fail(400, "body shorter than Content-Length")
        try:
            obj = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self._fail(400, "body is not valid JSON")
        if not isinstance(obj, dict):
            return self._fail(400, "body must be a JSON object")
        return obj

    async def _in_session(self, fn):
        """`fn()` on the session thread pool while holding the session lock."""
        def locked():
            with self.lock:
                return fn()
        return await asyncio.get_running_loop().run_in_executor(self.pool, locked)

    async def _route(self, route, tab, body):
        session = self.session
        if route == "/":
            page = _viewer_page()
            if page is None:
                return self._send(404, b"The viewer page is not installed; see /tabs and /tabs/<id>/mesh.json.\n",
                                  "text/plain; charset=utf-8")
            return self._send(200, page, "text/html; charset=utf-8", [("Content-Security-Policy", _VIEWER_CSP)])
        if route == "/health":                                   # needs no session, so never waits for the lock
            from . import __version__
            qt = _qt_modules()
            return self._json(200, {"ok": True, "version": __version__, "qt_imported": bool(qt), "qt_modules": qt})
        if route == "/tabs":
            tabs = await self._in_session(session.tabs)
            return self._json(200, {"document": {"id": session.id, "name": session.name}, "tabs": tabs})
        if route == "outputs":
            data = await self._in_session(lambda: session.outputs_wire(tab))
            return self._send(200, bytes(data), "application/octet-stream")
        if route == "mesh.json":
            out = await self._in_session(lambda: session.outputs(tab))
            doc = await asyncio.get_running_loop().run_in_executor(self.pool, mesh_document, out)
            if doc is None:
                return self._fail(404, f"tab {tab!r} ({out.kind}) has no mesh")
            return self._json(200, doc)
        if route == "edit":
            rebuilt = await self._in_session(lambda: session.submit_edit(tab, body))
            return self._json(200, {"rebuilt": rebuilt})
        if route == "/save":
            saved = await self._in_session(session.save)
            return self._json(200, {"saved": saved})
        return self._fail(404, "not found")


def make_server(session, port=0, allow_origins=()):
    """A server on 127.0.0.1:`port` over `session` (bound, not yet serving). Callers run `serve_forever` (it
    blocks) and, at the end, `shutdown`, `server_close` and close the session; `close_all` does the last three."""
    return _Server(session, port, allow_origins)


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
    ap.add_argument("--allow-origin", action="append", default=[], metavar="ORIGIN",
                    help="also accept this public https://host[:port] origin (repeatable; none by default)")
    args = ap.parse_args(argv)
    for value in args.allow_origin:
        try:
            parse_allow_origin(value)
        except ValueError as e:
            print(f"freeshape serve: refusing --allow-origin {_clean(value, 80)!r}: {e}.", file=sys.stderr)
            return 2
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
        server = make_server(session, args.port, args.allow_origin)
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
    server.wait_started()
    print(f"{READY_PREFIX} port={server.port} url=http://{BIND_HOST}:{server.port}/", flush=True)
    try:
        while not stop.wait(0.5):
            pass
    finally:
        close_all(server, session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
