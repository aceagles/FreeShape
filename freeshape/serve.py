"""Headless serve (I5.4, ADR 0010; aiohttp since ADR 0014): one Document session over loopback HTTP. No Qt.

    python -m freeshape serve <Document folder> [--port N]

Prints one line on stdout when it is listening:  `FREESHAPE_SERVE_READY port=<N> url=http://127.0.0.1:<N>/`
(`--port 0` picks a free port). SIGINT and SIGTERM stop the server, then close the session, so no worker is left.

The server binds 127.0.0.1 and nothing else; there is no option for another address and no authentication
(ADR 0010). `--host` exists only to refuse: `127.0.0.1` and `localhost` are accepted (both bind 127.0.0.1).

Routes (an exact table; no part of a request is ever joined to a file path):
    GET  /                      the built Browser client (freeshape/static/app/index.html), or, when it is not built,
                                a plain page naming `npm --prefix web run build`
    GET  /viewer                the old viewer page (freeshape/static/viewer.html, package data; 404 if not installed)
    GET  <file of the client>   a file of the built client, looked up in a table of the files found under
                                freeshape/static/app/ (see `_client_files`); a path not in the table is 404
    GET  /health                {"ok", "version", "qt_imported", "qt_modules"}
    GET  /tabs                  {"document": {"id", "name"}, "tabs": [session.tabs() entries]}
    GET  /tabs/<id>/outputs     the tab's outputs in the engine wire format (application/octet-stream)
    GET  /tabs/<id>/mesh.json   display meshes, see `mesh_document`
    POST /tabs/<id>/edit        body: a contract edit dict, e.g. {"op": "set_param", ...}; reply {"rebuilt": [ids]}
    POST /save                  body: {} ; reply {"saved": [ids]}
    GET  /ws                    the live channel (WebSocket, JSON text messages; see `_Server._socket`)

The live channel (I1.5). `GET /ws?client=<id>` (the id is 8..64 of letters, digits, `_`, `-`; the server makes one
when it is missing) upgrades after the same Host and Origin checks as every route. The server sends
    {"type": "hello", "client", "document": {"id", "name"}, "tabs": [tab entries + "revision"]}
and then pushes, to every connected socket,
    {"type": "changed", "client": <id that made the edit or null>, "base": <its base revision or null>,
     "rebuilt": [tab ids], "revisions": {tab id: revision}, "saved": [tab ids]}   after an edit or a save
    {"type": "tabs", "tabs": [...]}                                               when a tab's state/error/dirty changed
A client may send {"type": "hello"} (answered with hello), {"type": "ping", "t": x} (answered with
{"type": "pong", "t": x}) and {"type": "edit", "id", "tab", "edit", "base"} (answered with {"type": "edited", "id",
"rebuilt"} or {"type": "error", "id", "message"}). An HTTP edit names its sender with the headers X-Client-Id and
X-Base-Revision (both optional). The server records both and does not yet act on a stale base revision.
State of one person lives in `ClientRegistry`, keyed by client id, kept `client_ttl` seconds (default 600, option
`--client-expiry`) after the client's last socket closes; a reconnect with the same id finds its entry.

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
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BIND_HOST = "127.0.0.1"
MAX_BODY = 1024 * 1024
CONNECTION_TIMEOUT = 15.0       # seconds an idle keep-alive connection is kept open
SESSION_THREADS = 8             # threads for session calls (they queue on the session lock, one runs at a time)
BODY_DEADLINE = 30.0            # seconds for the whole body of one request
DEFAULT_PORT = 8765
READY_PREFIX = "FREESHAPE_SERVE_READY"
_TAB_PATH = re.compile(r"^/tabs/([A-Za-z0-9_-]{1,64})/(outputs|mesh\.json|edit)$")
_FIXED = {"/viewer": ("GET",), "/health": ("GET",), "/tabs": ("GET",), "/save": ("POST",)}
_EDGE_METHODS = {"outputs": "GET", "mesh.json": "GET", "edit": "POST"}
_VIEWER_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
               "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_CLIENT_CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
               "img-src 'self' data: blob:; font-src 'self'; worker-src 'self' blob:; base-uri 'none'; "
               "form-action 'none'; frame-ancestors 'none'")
_CLIENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".json": "application/json; charset=utf-8", ".map": "application/json; charset=utf-8",
                 ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".webp": "image/webp",
                 ".woff2": "font/woff2", ".woff": "font/woff", ".wasm": "application/wasm",
                 ".txt": "text/plain; charset=utf-8"}
_CLIENT_DIR = Path(__file__).resolve().parent / "static" / "app"     # `npm --prefix web run build` writes here
_CLIENT_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_NOT_BUILT = (b"The FreeShape client is not built.\n\nBuild it with:\n\n    npm --prefix web run build\n\n"
              b"then reload this page. The old viewer is at /viewer.\n")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
CLIENT_TTL = 600.0              # seconds a client's registry entry outlives its last socket
WS_HEARTBEAT = 25.0             # seconds between protocol pings that keep a tunnel open and find dead sockets
WS_SEND_TIMEOUT = 5.0           # seconds a slow socket gets for one pushed message before it is dropped
_REMOTE_MESSAGE = ("remote access needs authentication that does not exist yet; "
                   "serve binds 127.0.0.1 only (use a port forward or the tunnel)")


def _viewer_page():
    """The viewer page bytes read from the package data, or None when the file is not installed."""
    try:
        from importlib import resources
        return resources.files("freeshape").joinpath("static/viewer.html").read_bytes()
    except (OSError, ModuleNotFoundError, FileNotFoundError):
        return None


def _client_files():
    """{URL path: (file, content type)} for the built client: every regular file under freeshape/static/app/, found
    by listing the folder, never by joining a request value to a path. A request is answered only when its raw
    path is a key of this table. File and folder names must be plain (letters, digits, `_`, `.`, `-`, no leading
    dot); symbolic links are skipped. Empty when the client is not built (no index.html)."""
    root = _CLIENT_DIR
    table = {}

    def walk(folder, prefix):
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            return
        for entry in entries:
            name = entry.name
            if entry.is_symlink() or name.startswith(".") or not _CLIENT_NAME.match(name):
                continue
            if entry.is_dir():
                walk(entry, prefix + name + "/")
            elif entry.is_file():
                ctype = _CLIENT_TYPES.get(entry.suffix.lower())
                if ctype is not None:
                    table[prefix + name] = (entry, ctype)

    walk(root, "/")
    if "/index.html" not in table:
        return {}
    table["/"] = table.pop("/index.html")
    return table


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


class ClientEntry:
    """What the server keeps for one client id: `data` is for later cards (an open dialog, a sketch session)."""

    def __init__(self, client):
        self.client = client
        self.data = {}
        self.sockets = 0
        self.last_edit = None            # {"tab", "base", "revision"} of this client's latest edit
        self._timer = None


class ClientRegistry:
    """Per-client state, keyed by client id and never by socket. An entry is made by the first `attach`, survives
    while the client has a socket and for `ttl` seconds after the last one closes; a reconnect in that time gets
    the same entry. When it expires it is dropped and `on_expire(entry)` runs. Event-loop thread only."""

    def __init__(self, ttl=CLIENT_TTL, on_expire=None):
        self.ttl = ttl
        self.on_expire = on_expire
        self._entries = {}

    def get(self, client):
        return self._entries.get(client)

    def ids(self):
        return sorted(self._entries)

    def attach(self, client):
        entry = self._entries.get(client)
        if entry is None:
            entry = self._entries[client] = ClientEntry(client)
        if entry._timer is not None:
            entry._timer.cancel()
            entry._timer = None
        entry.sockets += 1
        return entry

    def detach(self, client):
        entry = self._entries.get(client)
        if entry is None:
            return
        entry.sockets = max(0, entry.sockets - 1)
        if entry.sockets == 0 and entry._timer is None:
            entry._timer = asyncio.get_running_loop().call_later(self.ttl, self._expire, entry)

    def _expire(self, entry):
        entry._timer = None
        if entry.sockets == 0 and self._entries.get(entry.client) is entry:
            del self._entries[entry.client]
            if self.on_expire is not None:
                try:
                    self.on_expire(entry)
                except Exception as e:                           # noqa: BLE001
                    _log(f"client-expired callback failed: {type(e).__name__}: {_clean(str(e), 200)}")

    def cancel_timers(self):
        for entry in self._entries.values():
            if entry._timer is not None:
                entry._timer.cancel()
                entry._timer = None


class _Server:
    """The aiohttp server: bound to 127.0.0.1 when made, running once `serve_forever` is called (it blocks, on its
    own event loop, until `shutdown`). `lock` serialises every session call; those calls run on `pool`, never on
    the event loop."""

    def __init__(self, session, port=0, allow_origins=(), client_ttl=CLIENT_TTL, client_expired=None):
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
        self.clients = ClientRegistry(client_ttl, client_expired)
        self._sockets = {}                                       # live WebSocketResponse -> client id
        self._revisions = {}                                     # tab id -> last known revision (under `lock`)
        self._tabs_sig = None                                    # last pushed tab states, to push `tabs` on change
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
            await asyncio.gather(*(ws.close(code=1001, message=b"server stopping") for ws in list(self._sockets)),
                                 return_exceptions=True)
            await runner.cleanup()
            self.clients.cancel_timers()

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
        if path == "/ws":
            if method != "GET":
                return self._fail(405, "method not allowed", [("Allow", "GET")])
            return await self._socket(request)
        if path in _FIXED:
            route, want = path, _FIXED[path][0]
        elif path == "/" or (_TAB_PATH.match(path) is None and path in (files := _client_files())):
            route, want = "client", "GET"                          # the table decides; see `_client_files`
            tab = _client_files().get(path)                        # (file, content type); None: client not built
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
        return await self._route(route, tab, body, request)

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

    async def _route(self, route, tab, body, request=None):
        session = self.session
        if route == "client":
            if tab is None:                                      # `tab` carries (file, content type) for this route
                return self._send(200, _NOT_BUILT, "text/plain; charset=utf-8")
            data = await asyncio.get_running_loop().run_in_executor(None, tab[0].read_bytes)
            return self._send(200, data, tab[1], [("Content-Security-Policy", _CLIENT_CSP)])
        if route == "/viewer":
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
            client, base = self._edit_meta(request.headers.get("X-Client-Id"), request.headers.get("X-Base-Revision"))
            rebuilt = await self._edit(tab, body, client, base)
            return self._json(200, {"rebuilt": rebuilt})
        if route == "/save":
            client, _ = self._edit_meta(request.headers.get("X-Client-Id"), None)
            saved = await self._save(client)
            return self._json(200, {"saved": saved})
        return self._fail(404, "not found")

    # ------------------------------------------------------------------ edits, pushes
    def _edit_meta(self, client, base):
        """(client id or None, base revision or None) from an edit's header or field values; SessionError if bad."""
        if client is not None and (not isinstance(client, str) or _CLIENT_ID.match(client) is None):
            raise self.session_error("the client id must be 8..64 letters, digits, '_' or '-'")
        if base is not None:
            text = base.strip() if isinstance(base, str) else base
            if isinstance(text, str) and text.isascii() and text.isdigit() and len(text) <= 12:
                base = int(text)
            elif isinstance(text, int) and not isinstance(text, bool) and 0 <= text < 10 ** 12:
                base = text
            else:
                raise self.session_error("the base revision must be a non-negative integer")
        return client, base

    def _tab_view(self, rebuilt=()):
        """Session lock held. session.tabs() with each tab's "revision" (None when unknown); the revisions of the
        `rebuilt` tabs are read again, the others come from the cache."""
        tabs = self.session.tabs()
        for t in tabs:
            tid = t["id"]
            if tid in rebuilt or tid not in self._revisions:
                try:
                    self._revisions[tid] = None if t["state"] in ("failed", "crashed") else self.session.outputs(tid).revision
                except Exception:                                # noqa: BLE001  (a tab that cannot answer has none)
                    self._revisions[tid] = None
            t["revision"] = self._revisions.get(tid)
        return tabs

    async def _edit(self, tab, edit, client, base):
        """Apply one edit, record who made it, push `changed` (and `tabs` when states moved); the rebuilt ids."""
        def work():
            rebuilt = self.session.submit_edit(tab, edit)
            return rebuilt, self._tab_view(rebuilt)
        rebuilt, tabs = await self._in_session(work)
        revisions = {t["id"]: t["revision"] for t in tabs if t["id"] in rebuilt}
        entry = self.clients.get(client) if client else None
        if entry is not None:
            entry.last_edit = {"tab": tab, "base": base, "revision": revisions.get(tab)}
        await self._announce({"type": "changed", "client": client, "base": base, "rebuilt": rebuilt,
                              "revisions": revisions, "saved": []}, tabs)
        return rebuilt

    async def _save(self, client):
        def work():
            saved = self.session.save()
            return saved, self._tab_view()
        saved, tabs = await self._in_session(work)
        await self._announce({"type": "changed", "client": client, "base": None, "rebuilt": [], "revisions": {},
                              "saved": saved}, tabs)
        return saved

    async def _announce(self, changed, tabs):
        await self._broadcast(changed)
        sig = json.dumps([{k: v for k, v in t.items() if k != "revision"} for t in tabs], sort_keys=True)
        if sig != self._tabs_sig:
            self._tabs_sig = sig
            await self._broadcast({"type": "tabs", "tabs": tabs})

    async def _broadcast(self, message):
        """`message` as JSON text to every connected socket; one that is slow or dead is dropped."""
        sockets = list(self._sockets)
        if not sockets:
            return
        text = json.dumps(message)

        async def one(ws):
            try:
                await asyncio.wait_for(ws.send_str(text), WS_SEND_TIMEOUT)
            except Exception:                                    # noqa: BLE001
                await ws.close()
        await asyncio.gather(*(one(ws) for ws in sockets), return_exceptions=True)

    # ------------------------------------------------------------------ the live channel
    async def _hello(self, client):
        tabs = await self._in_session(self._tab_view)
        if self._tabs_sig is None:
            self._tabs_sig = json.dumps([{k: v for k, v in t.items() if k != "revision"} for t in tabs], sort_keys=True)
        return {"type": "hello", "client": client, "document": {"id": self.session.id, "name": self.session.name},
                "tabs": tabs}

    async def _socket(self, request):
        """GET /ws: upgrade, then serve one socket until it closes. The Host and Origin rules apply before the upgrade."""
        from aiohttp import WSMsgType, web
        refused = check_host_origin(request.headers.get("Host"), request.headers.get("Origin"), self.hosts, self.origins)
        if refused is not None:
            return self._fail(*refused)
        client = request.query.get("client")
        if client is None:
            client = uuid.uuid4().hex
        elif _CLIENT_ID.match(client) is None:
            return self._fail(400, "the client id must be 8..64 letters, digits, '_' or '-'")
        ws = web.WebSocketResponse(heartbeat=WS_HEARTBEAT, max_msg_size=MAX_BODY)
        if not ws.can_prepare(request).ok:
            return self._fail(400, "a WebSocket upgrade is required")
        await ws.prepare(request)
        self.clients.attach(client)
        self._sockets[ws] = client
        try:
            await self._push(ws, await self._hello(client))
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._message(ws, client, msg.data)
                elif msg.type == WSMsgType.BINARY:
                    await self._push(ws, {"type": "error", "message": "messages are JSON text"})
        except Exception as e:                                   # noqa: BLE001
            _log(f"socket {_clean(client, 16)}: {type(e).__name__}: {_clean(str(e), 300)}")
        finally:
            self._sockets.pop(ws, None)
            self.clients.detach(client)
            if not ws.closed:
                await ws.close()
        return ws

    @staticmethod
    async def _push(ws, message):
        try:
            await asyncio.wait_for(ws.send_str(json.dumps(message)), WS_SEND_TIMEOUT)
        except Exception:                                        # noqa: BLE001
            await ws.close()

    async def _message(self, ws, client, text):
        try:
            msg = json.loads(text)
        except ValueError:
            msg = None
        kind = msg.get("type") if isinstance(msg, dict) else None
        if kind == "ping":
            await self._push(ws, {"type": "pong", "t": msg.get("t")})
        elif kind == "hello":
            await self._push(ws, await self._hello(client))
        elif kind == "edit":
            ident = msg.get("id")
            try:
                tab = msg.get("tab")
                if not isinstance(tab, str) or tab not in self.tab_ids:
                    raise self.session_error("unknown tab")
                if not isinstance(msg.get("edit"), dict):
                    raise self.session_error("an edit is a dict with an 'op' key")
                _, base = self._edit_meta(None, msg.get("base"))
                rebuilt = await self._edit(tab, msg["edit"], client, base)
            except self.session_error as e:
                await self._push(ws, {"type": "error", "id": ident, "message": str(e)})
            else:
                await self._push(ws, {"type": "edited", "id": ident, "rebuilt": rebuilt})
        else:
            await self._push(ws, {"type": "error", "message": "unknown message"})


def make_server(session, port=0, allow_origins=(), client_ttl=CLIENT_TTL, client_expired=None):
    """A server on 127.0.0.1:`port` over `session` (bound, not yet serving). Callers run `serve_forever` (it
    blocks) and, at the end, `shutdown`, `server_close` and close the session; `close_all` does the last three."""
    return _Server(session, port, allow_origins, client_ttl, client_expired)


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
    ap.add_argument("--client-expiry", type=float, default=CLIENT_TTL, metavar="SECONDS",
                    help="seconds a client's state is kept after its last socket closes (default 600)")
    args = ap.parse_args(argv)
    if not args.client_expiry >= 0:
        print("freeshape serve: --client-expiry must be 0 or more.", file=sys.stderr)
        return 2
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
        server = make_server(session, args.port, args.allow_origin, args.client_expiry)
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
