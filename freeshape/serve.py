"""Headless serve (I5.4, ADR 0010; aiohttp since ADR 0014): Document sessions over loopback HTTP. No Qt.

    python -m freeshape serve <Document folder | Documents root> [--port N] [--data-dir DIR]

A Document folder (it holds document.json) is served as the one Document. Any other folder is a Documents root:
the Documents come from the registry in the data directory (`documents.json`, the desktop's own), new ones are
made inside the root, and at most MAX_OPEN_DOCUMENTS are open at once (opened by id, with the lock below).

Prints one line on stdout when it is listening:  `FREESHAPE_SERVE_READY port=<N> url=http://127.0.0.1:<N>/`
(`--port 0` picks a free port). SIGINT and SIGTERM stop the server, then close the session, so no worker is left.

The server binds 127.0.0.1 and nothing else; there is no option for another address and no authentication
(ADR 0010). `--host` exists only to refuse: `127.0.0.1` and `localhost` are accepted (both bind 127.0.0.1).

Routes (an exact table; no part of a request is ever joined to a file path):
    GET  /                      the built Browser client (freeshape/static/app/index.html), or, when it is not built,
                                a plain page naming `npm --prefix web run build`
    GET  <file of the client>   a file of the built client, looked up in a table of the files found under
                                freeshape/static/app/ (see `_client_files`); a path not in the table is 404
    GET  /health                {"ok", "version", "qt_imported", "qt_modules"}
    GET  /documents             {"documents": [{id, name, modified, holder, open, missing}]}  (ids from the registry)
    POST /documents             body {"name"}; makes a Document folder under the root, reply 201 {id, name}
    POST /documents/<id>/open   opens the session and takes the lock as holder "browser"; 409 naming the open one
                                when another Document is open; read-only (reply read_only true, holder) when the
                                lock is held elsewhere
    POST /documents/<id>/close  releases the lock, closes the session
    POST /documents/<id>/take-over   takes the lock from whoever holds it and reloads the session from the folder
    GET  /documents/<id>/tabs                  {"document": {"id", "name"}, "tabs": [...], "read_only", "holder"}
    GET  /documents/<id>/tabs/<tab>/outputs    the tab's outputs in the engine wire format (octet-stream)
    GET  /documents/<id>/tabs/<tab>/mesh.json  display meshes, see `mesh_document`
    POST /documents/<id>/tabs/<tab>/edit       body: a contract edit dict; reply {"rebuilt": [ids]}
    POST /documents/<id>/save                  body: {} ; reply {"saved": [ids]}
    GET  /ws?client=<id>[&document=<id>]       the live channel (WebSocket, JSON text messages; `_Server._socket`)
  On a server over one Document folder the old un-prefixed routes stay as aliases for that Document:
    GET /tabs, GET /tabs/<id>/outputs, GET /tabs/<id>/mesh.json, POST /tabs/<id>/edit, POST /save
  An edit or save on a Document this server does not hold the lock of answers 423 {"error", "document", "holder"}.
  A Document id is a key into the open table and the registry; it is never joined to a path. A new Document's
  folder name is made from its name by `folder_name_for`.

The live channel (I1.5). `GET /ws?client=<id>` (the id is 8..64 of letters, digits, `_`, `-`; the server makes one
when it is missing) upgrades after the same Host and Origin checks as every route. `&document=<id>` subscribes to
that open Document (a single-Document server subscribes to its one Document when none is named); a socket may also
send {"type": "subscribe", "document": id} and {"type": "unsubscribe", "document": id}. A message that acts on a
Document names it in "document" (an edit; the single-Document server defaults it). The server sends
    {"type": "hello", "client", "document": {"id", "name"}, "tabs": [tab entries + "revision"], "read_only", "holder"}
and then pushes, to every socket subscribed to that Document, each message carrying "document": <id>,
    {"type": "lock", "document", "read_only", "holder"}   when the lock is lost or taken over
    {"type": "closed", "document"}                        when the Document is closed
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
import os
import re
import signal
import socket
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import doc_lock

BIND_HOST = "127.0.0.1"
MAX_BODY = 1024 * 1024
CONNECTION_TIMEOUT = 15.0       # seconds an idle keep-alive connection is kept open
SESSION_THREADS = 8             # threads for session calls (they queue on the session lock, one runs at a time)
BODY_DEADLINE = 30.0            # seconds for the whole body of one request
DEFAULT_PORT = 8765
READY_PREFIX = "FREESHAPE_SERVE_READY"
LOCK_PROGRAM = "browser"        # the holder name this server writes in a Document's lock file
LOCK_REFRESH = doc_lock.REFRESH_INTERVAL     # seconds between lock heartbeats (tests lower it)
MAX_OPEN_DOCUMENTS = 1          # the open-Document limit; see `_Server._open_refusal`
MAX_NAME = 100                  # longest Document name POST /documents accepts
MAX_FOLDER = 80                 # longest folder name made from it
_DOC_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_TAB_PATH = re.compile(r"^/tabs/([A-Za-z0-9_-]{1,64})/(outputs|mesh\.json|edit)$")
_DOC_PATH = re.compile(r"^/documents/([A-Za-z0-9_-]{1,64})"
                       r"(?:/(open|close|take-over|save|tabs)"
                       r"|/tabs/([A-Za-z0-9_-]{1,64})(?:/(outputs|mesh\.json|edit|rename))?)$")
_DOC_VERBS = {"open": "POST", "close": "POST", "take-over": "POST", "save": "POST", "tabs": "GET"}
_FIXED = {"/health": ("GET",), "/tabs": ("GET",), "/save": ("POST",)}
_EDGE_METHODS = {"outputs": "GET", "mesh.json": "GET", "edit": "POST", "rename": "POST"}
MAX_TABS = 200                  # tabs one Document may hold through POST /documents/<id>/tabs
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
              b"then reload this page.\n")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
CLIENT_TTL = 600.0              # seconds a client's registry entry outlives its last socket
WS_HEARTBEAT = 25.0             # seconds between protocol pings that keep a tunnel open and find dead sockets
WS_SEND_TIMEOUT = 5.0           # seconds a slow socket gets for one pushed message before it is dropped
_REMOTE_MESSAGE = ("remote access needs authentication that does not exist yet; "
                   "serve binds 127.0.0.1 only (use a port forward or the tunnel)")


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


class DocumentLocked(Exception):
    """An edit or save on a Document this server does not hold the lock of (HTTP 423). `holder` is a dict
    {program, instance, heartbeat, stale} naming who has it, or None when the lock file is gone."""

    def __init__(self, document, holder):
        self.document = document
        self.holder = holder
        who = f"{holder['program']}" if holder else "another program"
        super().__init__(f"the Document is being edited by {who}; this server has it read-only "
                         f"(take the lock over to edit)")


def _holder_json(h):
    """A doc_lock.Holder (or None) as the dict the routes send."""
    if h is None:
        return None
    return {"program": h.program, "instance": h.instance, "heartbeat": h.heartbeat, "stale": bool(h.stale)}


def _modified(folder):
    """The newest modification time among the Document folder's own files (not `.git`, not the lock), or None."""
    newest = None
    try:
        with os.scandir(folder) as it:
            for e in it:
                if e.name.startswith(".") or e.is_symlink():
                    continue
                try:
                    t = e.stat().st_mtime
                except OSError:
                    continue
                newest = t if newest is None else max(newest, t)
    except OSError:
        return None
    return newest


def folder_name_for(name):
    """The folder name a new Document called `name` gets, or ValueError (the message is for the client).

    `name` must be text of 1..MAX_NAME characters with no control character, no path separator (`/`, `\\`), no
    drive colon and no NUL, so `../x`, `/etc` and `C:\\x` are refused outright. The folder name is then made from
    it: only letters, digits, space, `-`, `_` and `.` survive (anything else becomes `-`), runs of those collapse,
    leading and trailing `.`, `-` and space are stripped, and what is left is capped at MAX_FOLDER characters. A
    name with nothing left (`..`, `...`) is refused. The result never contains a separator and never starts with
    a dot, so joining it to the Documents root cannot leave that root."""
    if not isinstance(name, str):
        raise ValueError("the name must be text")
    text = name.strip()
    if not text:
        raise ValueError("the name is empty")
    if len(text) > MAX_NAME:
        raise ValueError(f"the name is longer than {MAX_NAME} characters")
    if any(not c.isprintable() for c in text) or any(c in text for c in "/\\:"):
        raise ValueError("the name may not contain a path separator, a colon or a control character")
    out = []
    for c in text:
        out.append(c if (c.isalnum() or c in " _.-") else "-")
    stem = re.sub(r"-{2,}", "-", re.sub(r" {2,}", " ", "".join(out))).strip(".- ")[:MAX_FOLDER].strip(".- ")
    if not stem:
        raise ValueError("the name has no letters or digits")
    return stem


class _Open:
    """One open Document: its session, the lock that serialises calls into the session (`lock`, held on the thread
    pool, never on the event loop), the Document-lock file this server holds (`file_lock`, a doc_lock.Lock, or None
    when it has none) and what the live channel needs (tab revisions, the last pushed tab states, the refresh task).

    Every Document-scoped route and message goes through one of these, found by Document id in `_Server.docs`."""

    def __init__(self, doc_id, folder, session, file_lock=None, holder=None, single=False):
        self.id = doc_id
        self.folder = folder
        self.session = session
        self.lock = threading.Lock()
        self.file_lock = file_lock           # None: no lock file (an in-process server, or read-only below)
        self.holder = holder                 # doc_lock.Holder seen at open when it was held elsewhere, else None
        self.read_only = file_lock is None and holder is not None
        self.lost = False                    # the lock was taken from us after we had it
        self.single = single                 # the one Document of a single-Document server (old routes alias it)
        self.tab_ids = frozenset(t["id"] for t in session.tabs())     # changes only through `refresh_tab_ids`
        self.revisions = {}                  # tab id -> last known revision (under `lock`)
        self.tabs_sig = None                 # last pushed tab states, to push `tabs` on change
        self.refresher = None                # the asyncio task that refreshes the lock file

    @property
    def name(self):
        return self.session.name

    def refresh_tab_ids(self):
        """Session lock held. Tab ids and revisions as the session now has them (after an add or a delete)."""
        ids = frozenset(t["id"] for t in self.session.tabs())
        self.revisions = {k: v for k, v in self.revisions.items() if k in ids}
        self.tab_ids = ids

    def lock_state(self):
        """{"read_only", "holder"} for messages. Reads the lock file when the Document is read-only (blocking I/O:
        call from the pool)."""
        if not self.is_read_only():
            return {"read_only": False, "holder": None}
        try:
            h = doc_lock.holder(self.folder) if self.folder else None
        except OSError:
            h = None
        return {"read_only": True, "holder": _holder_json(h) or _holder_json(self.holder)}

    def is_read_only(self):
        return self.read_only or self.lost

    def check_writable(self):
        """Pool thread. Raises DocumentLocked unless this server still holds the lock (the file is read again, so a
        take-over is seen at once, not at the next refresh)."""
        if self.file_lock is None and not self.is_read_only():
            return                                         # no lock file: an in-process server
        if not self.is_read_only() and self.file_lock is not None:
            try:
                mine = self.file_lock.still_mine()
            except OSError:
                mine = True                                # an unreadable lock is not a reason to refuse an edit
            if mine:
                return
            self.lost = True
        state = self.lock_state()
        raise DocumentLocked(self.id, state["holder"])


class _Server:
    """The aiohttp server: bound to 127.0.0.1 when made, running once `serve_forever` is called (it blocks, on its
    own event loop, until `shutdown`). Each open Document has its own `lock` serialising its session calls; those
    calls run on `pool`, never on the event loop.

    The open-Document table is `docs` (id -> _Open); `_open_refusal` is the one place that says how many may be open
    at once. Opening, closing, taking over, the lock refresh and the per-Document broadcast all live here, and the
    later cards (tab add and delete, autosave, idle close) extend them. A server is made over one session (a single
    Document: the old un-prefixed routes alias it) or over a `registry` (a Documents root: Documents are opened
    by id on request)."""

    def __init__(self, session=None, port=0, allow_origins=(), client_ttl=CLIENT_TTL, client_expired=None,
                 registry=None, root=None, folder=None, lock_program=None):
        from .session import SessionError
        self.session_error = SessionError
        self.registry = registry
        self.root = os.path.realpath(root) if root is not None else None
        self.lock_program = lock_program
        self.pool = ThreadPoolExecutor(max_workers=SESSION_THREADS, thread_name_prefix="serve-session")
        self.docs = {}                                           # open Document id -> _Open
        self._open_lock = None                                   # asyncio.Lock, made on the loop
        self._create_lock = threading.Lock()                     # one new Document folder at a time
        if session is not None:
            self._adopt(session, folder, lock_program)
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
        self._subs = {}                                          # live WebSocketResponse -> set of Document ids
        self._loop = None
        self._stop = None
        self._stop_asked = False
        self._entered = False
        self._started = threading.Event()
        self._finished = threading.Event()

    # ------------------------------------------------------------------ the single Document, for old callers
    @property
    def _single(self):
        for d in self.docs.values():
            if d.single:
                return d
        return None

    @property
    def session(self):
        d = self._single
        return d.session if d is not None else None

    @property
    def lock(self):
        d = self._single
        return d.lock if d is not None else None

    @property
    def tab_ids(self):
        d = self._single
        return d.tab_ids if d is not None else frozenset()

    def _adopt(self, session, folder, program):
        """Register the session of a single-Document server. With `program` and `folder` the Document lock is
        taken as that program (held elsewhere: the Document is read-only); with neither there is no lock file."""
        file_lock, holder = None, None
        if program is not None and folder is not None:
            try:
                file_lock = doc_lock.acquire(folder, program)
            except doc_lock.LockHeld as e:
                holder = e.holder
        self.docs[session.id] = _Open(session.id, folder, session, file_lock, holder, single=True)

    # ------------------------------------------------------------------ the open-Document policy
    @staticmethod
    def _open_refusal(docs, doc_id):
        """The open Document that stops `doc_id` being opened, or None. THE place for the limit: for now one
        Document is open at a time. Opening one that is already open is not refused."""
        if doc_id in docs:
            return None
        if len(docs) >= MAX_OPEN_DOCUMENTS:
            return next(iter(docs.values()))
        return None

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
        self._open_lock = asyncio.Lock()
        runner = web.ServerRunner(web.Server(self._handle, access_log=None, keepalive_timeout=CONNECTION_TIMEOUT),
                                  handle_signals=False)
        await runner.setup()
        try:
            await web.SockSite(runner, self.socket).start()
            for d in list(self.docs.values()):
                self._start_refresh(d)
            self._started.set()
            if not self._stop_asked:
                await self._stop.wait()
        finally:
            for d in list(self.docs.values()):
                self._stop_refresh(d)
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

    def close_documents(self, wait=10.0):
        """After `shutdown` and `server_close`: release every lock this server holds and close every open
        Document's session (waiting up to `wait` seconds for a call in flight)."""
        for d in list(self.docs.values()):
            self.docs.pop(d.id, None)
            got = d.lock.acquire(timeout=wait)
            try:
                if d.file_lock is not None:
                    try:
                        d.file_lock.release()
                    except OSError:
                        pass
                d.session.close()
            finally:
                if got:
                    d.lock.release()

    # ------------------------------------------------------------------ the lock refresh
    def _start_refresh(self, d):
        if d.file_lock is not None and d.refresher is None and not d.is_read_only():
            d.refresher = asyncio.get_running_loop().create_task(self._refresh_loop(d))

    def _stop_refresh(self, d):
        task, d.refresher = d.refresher, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _refresh_loop(self, d):
        while True:
            await asyncio.sleep(LOCK_REFRESH)
            if not await self._refresh_one(d):
                return

    async def _refresh_one(self, d):
        """Write one heartbeat for `d` (off the loop). True while the lock is ours; on False the Document turns
        read-only and its clients are told (`lock` message)."""
        lock = d.file_lock
        if lock is None or d.is_read_only():
            return False
        try:
            ok = await asyncio.get_running_loop().run_in_executor(self.pool, lock.refresh)
        except Exception as e:                                   # noqa: BLE001  (file I/O trouble: try again later)
            _log(f"lock refresh for {_clean(d.id, 16)}: {type(e).__name__}: {_clean(str(e), 200)}")
            return True
        if not ok:
            d.lost = True
            d.refresher = None
            await self._announce_lock(d)
        return ok

    def refresh_locks_now(self, timeout=30.0):
        """Run one refresh of every open Document now, from any thread; {id: still ours}. For tests and for a
        caller that must not wait for the interval."""
        async def go():
            return {d.id: await self._refresh_one(d) for d in list(self.docs.values())}
        return asyncio.run_coroutine_threadsafe(go(), self._loop).result(timeout)

    async def _announce_lock(self, d):
        state = await asyncio.get_running_loop().run_in_executor(self.pool, d.lock_state)
        await self._broadcast({"type": "lock", "document": d.id, **state}, d)

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

    def _fail(self, code, message, headers=(), **extra):
        return self._json(code, {"error": message, **extra}, headers, close=True)

    # ------------------------------------------------------------------ one request
    async def _handle(self, request):
        try:
            resp = await self._respond(request)
        except DocumentLocked as e:
            resp = self._fail(423, str(e), document=e.document, holder=e.holder)
        except self.session_error as e:
            resp = self._fail(400, str(e))
        except Exception as e:                                   # noqa: BLE001
            import traceback
            _log(f"500 {request.method} {_clean(request.raw_path)}: {type(e).__name__}: {_clean(str(e), 500)}")
            traceback.print_exc(file=sys.stderr)
            resp = self._fail(500, "internal error")
        _log(f"{request.method} {_clean(request.raw_path)} {resp.status}")
        return resp

    def _match(self, path):
        """`(route, doc_id, tab, wanted method)` for a raw path, or None. The Document-keyed routes always match
        on the Document id in the path; the un-prefixed ones (`/tabs`, `/tabs/<id>/...`, `/save`) match only on a
        single-Document server and carry doc_id None (the caller fills in the one Document)."""
        if path == "/documents":
            return "documents", None, None, None             # GET lists, POST creates
        m = _DOC_PATH.match(path)
        if m is not None:
            doc_id, verb, tab, edge = m.group(1), m.group(2), m.group(3), m.group(4)
            if tab is not None and edge is None:
                return "tab", doc_id, tab, "DELETE"          # DELETE /documents/<id>/tabs/<tab>
            if edge is not None:
                return edge, doc_id, tab, _EDGE_METHODS[edge]
            return verb, doc_id, None, _DOC_VERBS[verb]
        if self._single is not None:
            if path in _FIXED and path != "/health":
                return path, None, None, _FIXED[path][0]
            t = _TAB_PATH.match(path)
            if t is not None:
                return t.group(2), None, t.group(1), _EDGE_METHODS[t.group(2)]
        return None

    async def _respond(self, request):
        method = request.method
        refused = check_host_origin(request.headers.get("Host"), request.headers.get("Origin"), self.hosts, self.origins)
        if refused is not None:
            return self._fail(*refused)
        if method not in ("GET", "POST", "DELETE"):               # PUT, HEAD, OPTIONS, ... are refused
            return self._fail(405, "method not allowed", [("Allow", "GET, POST")])
        # the route comes from the raw request target, never from a decoded or normalised path
        path = request.raw_path.split("?", 1)[0]
        tab = None
        doc_id = None
        if path == "/ws":
            if method != "GET":
                return self._fail(405, "method not allowed", [("Allow", "GET")])
            return await self._socket(request)
        if path == "/health":
            route, want = path, "GET"
        elif (hit := self._match(path)) is not None:
            route, doc_id, tab, want = hit
            if route == "documents":
                want = method if method in ("GET", "POST") else "GET"
                route = "documents.list" if method == "GET" else "documents.create"
            if route == "tabs" and method == "POST":               # POST adds a tab, GET lists them
                route, want = "tabs.add", "POST"
            if tab is not None and doc_id is None:                # old route: the tab must exist, as it always did
                one = self._single
                if tab not in one.tab_ids:
                    return self._fail(404, "not found")
        elif path == "/" or path in _client_files():
            route, want = "client", "GET"                          # the table decides; see `_client_files`
            tab = _client_files().get(path)                        # (file, content type); None: client not built
        else:
            return self._fail(404, "not found")
        if method != want:
            return self._fail(405, "method not allowed", [("Allow", want)])
        body = None
        if method == "POST":
            body = await self._body(request)
            if not isinstance(body, dict):
                return body                                        # the refusal response
        elif method == "DELETE" and (request.headers.get("Content-Length", "0").strip() != "0"
                                     or request.headers.get("Transfer-Encoding") is not None):
            return self._fail(400, "DELETE takes no body")
        return await self._route(route, doc_id, tab, body, request)

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

    async def _in_session(self, d, fn):
        """`fn()` on the session thread pool while holding the Document's session lock."""
        def locked():
            with d.lock:
                return fn()
        return await asyncio.get_running_loop().run_in_executor(self.pool, locked)

    async def _off_loop(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *args)

    async def _find_open(self, doc_id):
        """The open Document `doc_id`: the one `_Open`, or a refusal response (404 for an id nothing knows, 409 for
        a registered Document that is not open). The id is only ever a key, never part of a path."""
        d = self.docs.get(doc_id)
        if d is not None:
            return d
        if self.registry is not None and await self._off_loop(self._known, doc_id):
            return self._fail(409, "the Document is not open", document=doc_id)
        return self._fail(404, "not found")

    def _known(self, doc_id):
        return self.registry is not None and self.registry.trusted_path(doc_id) is not None

    async def _route(self, route, doc_id, tab, body, request=None):
        if route == "client":
            if tab is None:                                      # `tab` carries (file, content type) for this route
                return self._send(200, _NOT_BUILT, "text/plain; charset=utf-8")
            data = await asyncio.get_running_loop().run_in_executor(None, tab[0].read_bytes)
            return self._send(200, data, tab[1], [("Content-Security-Policy", _CLIENT_CSP)])
        if route == "/health":                                   # needs no session, so never waits for the lock
            from . import __version__
            qt = _qt_modules()
            return self._json(200, {"ok": True, "version": __version__, "qt_imported": bool(qt), "qt_modules": qt})
        if route == "documents.list":
            return self._json(200, {"documents": await self._off_loop(self._list_documents)})
        if route == "documents.create":
            return await self._create_document(body)
        d = None
        if doc_id is None:                                       # an old route: it means the single Document
            d = self._single
        elif route not in ("open", "close"):
            d = await self._find_open(doc_id)
            if not isinstance(d, _Open):
                return d                                         # the refusal response
        if route == "open":
            return await self._open_document(doc_id)
        if route == "close":
            return await self._close_document(doc_id)
        if route == "take-over":
            return await self._take_over(d)
        if route in ("/tabs", "tabs"):
            tabs = await self._in_session(d, d.session.tabs)
            return self._json(200, {"document": {"id": d.session.id, "name": d.session.name}, "tabs": tabs,
                                    **await self._off_loop(d.lock_state)})
        if route == "tabs.add":
            return await self._add_tab(d, body)
        if tab is not None and tab not in d.tab_ids:
            return self._fail(404, "not found")
        if route == "rename":
            return await self._rename_tab(d, tab, body)
        if route == "tab":
            return await self._delete_tab(d, tab)
        if route == "outputs":
            data = await self._in_session(d, lambda: d.session.outputs_wire(tab))
            return self._send(200, bytes(data), "application/octet-stream")
        if route == "mesh.json":
            out = await self._in_session(d, lambda: d.session.outputs(tab))
            doc = await asyncio.get_running_loop().run_in_executor(self.pool, mesh_document, out)
            if doc is None:
                return self._fail(404, f"tab {tab!r} ({out.kind}) has no mesh")
            return self._json(200, doc)
        if route == "edit":
            client, base = self._edit_meta(request.headers.get("X-Client-Id"), request.headers.get("X-Base-Revision"))
            rebuilt = await self._edit(d, tab, body, client, base)
            return self._json(200, {"rebuilt": rebuilt})
        if route in ("/save", "save"):
            client, _ = self._edit_meta(request.headers.get("X-Client-Id"), None)
            saved = await self._save(d, client)
            return self._json(200, {"saved": saved})
        return self._fail(404, "not found")

    # ------------------------------------------------------------------ Documents: list, create, open, close
    def _list_documents(self):
        """Pool thread. [{id, name, modified, holder, open, missing}] -- from the registry on a Documents root, the
        one Document on a single-Document server. `holder` is the lock file's holder or null."""
        rows = []
        if self.registry is not None:
            for r in self.registry.list_documents():
                folder = None if r["missing"] else r["path"]
                rows.append({"id": r["id"], "name": r["name"], "folder": folder, "missing": r["missing"]})
        else:
            d = self._single
            rows = [{"id": d.id, "name": d.name, "folder": d.folder, "missing": False}] if d is not None else []
        out = []
        for r in rows:
            try:
                h = doc_lock.holder(r["folder"]) if r["folder"] else None
            except OSError:
                h = None
            out.append({"id": r["id"], "name": r["name"], "modified": _modified(r["folder"]) if r["folder"] else None,
                        "holder": _holder_json(h), "open": r["id"] in self.docs, "missing": r["missing"]})
        return out

    async def _create_document(self, body):
        if self.registry is None:
            return self._fail(409, "this server serves a single Document; it cannot create another")
        try:
            stem = folder_name_for(body.get("name"))
        except ValueError as e:
            return self._fail(400, str(e))
        name = body["name"].strip()
        doc_id = await self._off_loop(self._make_document, stem, name)
        return self._json(201, {"id": doc_id, "name": name})

    def _make_document(self, stem, name):
        """Pool thread. Make `<root>/<stem>` (or `<stem> 2`, ...) as a new Document with one empty Part Studio and
        register it; return its id. The folder is made with `os.mkdir`, so one that exists is never reused, and
        its real path must sit directly under the root."""
        from . import doc_folder
        with self._create_lock:
            os.makedirs(self.root, exist_ok=True)
            for n in range(1, 1000):
                leaf = stem if n == 1 else f"{stem} {n}"
                path = os.path.join(self.root, leaf)
                try:
                    os.mkdir(path)
                except FileExistsError:
                    continue
                if os.path.dirname(os.path.realpath(path)) != self.root:
                    os.rmdir(path)
                    raise self.session_error("the Document folder would be outside the Documents folder")
                break
            else:
                raise self.session_error("too many Documents with that name")
            fd = doc_folder.FolderDocument.create(path, name, registry=self.registry)
            fd.add_tab("studio", "Part Studio 1")
            fd.save()
            if self.registry.trusted_path(fd.id) is None:
                raise self.session_error("the new Document could not be recorded in the registry")
            return fd.id

    async def _open_document(self, doc_id):
        """POST /documents/<id>/open. Opens the Document's session and takes the lock as `browser`; when the lock
        is held elsewhere the Document opens read-only. Opening an open Document answers 200 again."""
        async with self._open_lock:
            d = self.docs.get(doc_id)
            if d is None:
                blocker = self._open_refusal(self.docs, doc_id)
                if blocker is not None:
                    return self._fail(409, f"another Document is open: {blocker.name!r} ({blocker.id}); close it first",
                                      open={"id": blocker.id, "name": blocker.name})
                if self.registry is None:
                    return self._fail(404, "not found")
                folder = await self._off_loop(self.registry.trusted_path, doc_id)
                if folder is None:
                    return self._fail(404, "not found")
                d = await self._off_loop(self._start_document, doc_id, folder)
                self.docs[doc_id] = d
                self._start_refresh(d)
        tabs = await self._in_session(d, lambda: self._tab_view(d))
        return self._json(200, {"document": {"id": d.id, "name": d.name}, "tabs": tabs,
                                **await self._off_loop(d.lock_state)})

    def _start_document(self, doc_id, folder):
        """Pool thread. Take the lock, then start the session (so it reads what the last holder saved)."""
        file_lock, holder = None, None
        try:
            file_lock = doc_lock.acquire(folder, LOCK_PROGRAM)
        except doc_lock.LockHeld as e:
            holder = e.holder
        try:
            session = self._new_session(folder)
        except BaseException:
            if file_lock is not None:
                file_lock.release()
            raise
        return _Open(doc_id, folder, session, file_lock, holder)

    @staticmethod
    def _new_session(folder):
        from .session import DocumentSession
        return DocumentSession(folder)

    async def _close_document(self, doc_id):
        """POST /documents/<id>/close: stop the refresh, release the lock, close the session, tell the sockets.
        Unsaved edits are dropped (autosave is I2.6)."""
        async with self._open_lock:
            d = self.docs.get(doc_id)
            if d is None:
                if self.registry is not None and await self._off_loop(self._known, doc_id):
                    return self._json(200, {"closed": False})   # already closed: closing is idempotent
                return self._fail(404, "not found")
            if d.single:
                return self._fail(409, "this server serves a single Document; it stays open")
            self._stop_refresh(d)
            self.docs.pop(doc_id, None)
            await self._broadcast({"type": "closed", "document": doc_id}, d)
            for ws, subs in list(self._subs.items()):
                subs.discard(doc_id)

            def shut():
                with d.lock:
                    try:
                        if d.file_lock is not None:
                            d.file_lock.release()
                    finally:
                        d.session.close()
            await self._off_loop(shut)
        return self._json(200, {"closed": True})

    async def _take_over(self, d):
        """POST /documents/<id>/take-over: take the lock whoever holds it, then reload the session from the folder
        (the old holder may have saved since this server read it). Does nothing when the lock is already ours."""
        if d.single and d.file_lock is None and not d.is_read_only():
            return self._json(200, {"read_only": False, "holder": None})     # no lock file in this server
        if d.folder is None:
            return self._fail(409, "this Document has no lock")

        def work():
            with d.lock:
                if not d.is_read_only() and d.file_lock is not None and d.file_lock.still_mine():
                    return False
                d.file_lock = doc_lock.take_over(d.folder, LOCK_PROGRAM)
                old = d.session
                d.session = self._new_session(d.folder)
                old.close()
                d.tab_ids = frozenset(t["id"] for t in d.session.tabs())
                d.revisions = {}
                d.tabs_sig = None
                d.read_only = d.lost = False
                d.holder = None
                return True
        changed = await self._off_loop(work)
        if changed:
            self._stop_refresh(d)
            self._start_refresh(d)
            tabs = await self._in_session(d, lambda: self._tab_view(d))
            await self._announce_lock(d)
            await self._broadcast({"type": "tabs", "document": d.id, "tabs": tabs}, d)
        return self._json(200, {"read_only": False, "holder": None})

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

    def _tab_view(self, d, rebuilt=()):
        """Document's session lock held. session.tabs() with each tab's "revision" (None when unknown); the
        revisions of the `rebuilt` tabs are read again, the others come from the cache."""
        tabs = d.session.tabs()
        for t in tabs:
            tid = t["id"]
            if tid in rebuilt or tid not in d.revisions:
                try:
                    d.revisions[tid] = None if t["state"] in ("failed", "crashed") else d.session.outputs(tid).revision
                except Exception:                                # noqa: BLE001  (a tab that cannot answer has none)
                    d.revisions[tid] = None
            t["revision"] = d.revisions.get(tid)
        return tabs

    async def _edit(self, d, tab, edit, client, base):
        """Apply one edit, record who made it, push `changed` (and `tabs` when states moved); the rebuilt ids."""
        def work():
            d.check_writable()
            rebuilt = d.session.submit_edit(tab, edit)
            return rebuilt, self._tab_view(d, rebuilt)
        try:
            rebuilt, tabs = await self._in_session(d, work)
        except DocumentLocked:
            if d.lost:
                await self._announce_lock(d)
            raise
        revisions = {t["id"]: t["revision"] for t in tabs if t["id"] in rebuilt}
        entry = self.clients.get(client) if client else None
        if entry is not None:
            entry.last_edit = {"document": d.id, "tab": tab, "base": base, "revision": revisions.get(tab)}
        await self._announce(d, {"type": "changed", "document": d.id, "client": client, "base": base,
                                 "rebuilt": rebuilt, "revisions": revisions, "saved": []}, tabs)
        return rebuilt

    async def _tabs_changed(self, d, work):
        """Run `work` (a tab-list change on the session) under the session lock after `check_writable`; refresh the
        tab ids, push `tabs` to the Document's sockets; returns (work's result, the tab views)."""
        def run():
            d.check_writable()
            out = work()
            d.refresh_tab_ids()
            return out, self._tab_view(d)
        try:
            out, tabs = await self._in_session(d, run)
        except DocumentLocked:
            if d.lost:
                await self._announce_lock(d)
            raise
        d.tabs_sig = json.dumps([{k: v for k, v in t.items() if k != "revision"} for t in tabs], sort_keys=True)
        await self._broadcast({"type": "tabs", "document": d.id, "tabs": tabs}, d)
        return out, tabs

    async def _add_tab(self, d, body):
        """POST /documents/<id>/tabs {"kind": "studio" | "assembly", "name": optional}: 201 {"tab": <tab>, "tabs": [...]}."""
        kind, name = body.get("kind"), body.get("name")
        if kind not in ("studio", "assembly"):
            return self._fail(400, "kind must be \"studio\" or \"assembly\"")
        if name is not None:
            check = self._check_name(name)
            if check is not None:
                return check
        if len(d.tab_ids) >= MAX_TABS:
            return self._fail(409, f"a Document holds at most {MAX_TABS} tabs")
        tid, tabs = await self._tabs_changed(d, lambda: d.session.add_tab(kind, name))
        return self._json(201, {"document": d.id, "tab": next(t for t in tabs if t["id"] == tid), "tabs": tabs})

    async def _rename_tab(self, d, tab, body):
        """POST /documents/<id>/tabs/<tab>/rename {"name": ...}: 200 {"tab": <tab>, "tabs": [...]}."""
        check = self._check_name(body.get("name"))
        if check is not None:
            return check
        _, tabs = await self._tabs_changed(d, lambda: d.session.rename_tab(tab, body["name"]))
        return self._json(200, {"document": d.id, "tab": next(t for t in tabs if t["id"] == tab), "tabs": tabs})

    async def _delete_tab(self, d, tab):
        """DELETE /documents/<id>/tabs/<tab>: 200 {"deleted": id, "tabs": [...]}; 409 naming the tabs that link to it."""
        from .session import TabLinked
        try:
            _, tabs = await self._tabs_changed(d, lambda: d.session.delete_tab(tab))
        except TabLinked as e:
            return self._fail(409, str(e), document=d.id, tab=e.tab, linked_by=e.linked_by)
        return self._json(200, {"document": d.id, "deleted": tab, "tabs": tabs})

    def _check_name(self, name):
        """None if `name` is a valid tab name, else the 400 response."""
        from .session import check_tab_name
        try:
            check_tab_name(name)
        except self.session_error as e:
            return self._fail(400, str(e))
        return None

    async def _save(self, d, client):
        def work():
            d.check_writable()
            saved = d.session.save()
            return saved, self._tab_view(d)
        try:
            saved, tabs = await self._in_session(d, work)
        except DocumentLocked:
            if d.lost:
                await self._announce_lock(d)
            raise
        await self._announce(d, {"type": "changed", "document": d.id, "client": client, "base": None, "rebuilt": [],
                                 "revisions": {}, "saved": saved}, tabs)
        return saved

    async def _announce(self, d, changed, tabs):
        await self._broadcast(changed, d)
        sig = json.dumps([{k: v for k, v in t.items() if k != "revision"} for t in tabs], sort_keys=True)
        if sig != d.tabs_sig:
            d.tabs_sig = sig
            await self._broadcast({"type": "tabs", "document": d.id, "tabs": tabs}, d)

    async def _broadcast(self, message, d):
        """`message` as JSON text to every socket subscribed to Document `d`; one that is slow or dead is dropped."""
        sockets = [ws for ws in self._sockets if d.id in self._subs.get(ws, ())]
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
    async def _hello(self, client, d):
        """The hello for Document `d`, or (no Document subscribed yet) one with `document` null."""
        if d is None:
            return {"type": "hello", "client": client, "document": None, "tabs": []}
        tabs = await self._in_session(d, lambda: self._tab_view(d))
        if d.tabs_sig is None:
            d.tabs_sig = json.dumps([{k: v for k, v in t.items() if k != "revision"} for t in tabs], sort_keys=True)
        state = await self._off_loop(d.lock_state)
        return {"type": "hello", "client": client, "document": {"id": d.session.id, "name": d.session.name},
                "tabs": tabs, **state}

    async def _socket(self, request):
        """GET /ws: upgrade, then serve one socket until it closes. The Host and Origin rules apply before the
        upgrade. `?document=<id>` subscribes to that Document; a single-Document server subscribes to its one
        Document when none is named (the old client)."""
        from aiohttp import WSMsgType, web
        refused = check_host_origin(request.headers.get("Host"), request.headers.get("Origin"), self.hosts, self.origins)
        if refused is not None:
            return self._fail(*refused)
        client = request.query.get("client")
        if client is None:
            client = uuid.uuid4().hex
        elif _CLIENT_ID.match(client) is None:
            return self._fail(400, "the client id must be 8..64 letters, digits, '_' or '-'")
        want = request.query.get("document")
        if want is not None and _DOC_ID.fullmatch(want) is None:
            return self._fail(400, "the Document id must be 1..64 letters, digits, '_' or '-'")
        ws = web.WebSocketResponse(heartbeat=WS_HEARTBEAT, max_msg_size=MAX_BODY)
        if not ws.can_prepare(request).ok:
            return self._fail(400, "a WebSocket upgrade is required")
        await ws.prepare(request)
        self.clients.attach(client)
        self._sockets[ws] = client
        self._subs[ws] = set()
        try:
            if want is None and self._single is not None:
                want = self._single.id
            if want is None:
                await self._push(ws, await self._hello(client, None))
            else:
                await self._subscribe(ws, client, want)
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._message(ws, client, msg.data)
                elif msg.type == WSMsgType.BINARY:
                    await self._push(ws, {"type": "error", "message": "messages are JSON text"})
        except Exception as e:                                   # noqa: BLE001
            _log(f"socket {_clean(client, 16)}: {type(e).__name__}: {_clean(str(e), 300)}")
        finally:
            self._sockets.pop(ws, None)
            self._subs.pop(ws, None)
            self.clients.detach(client)
            if not ws.closed:
                await ws.close()
        return ws

    async def _subscribe(self, ws, client, doc_id):
        """Subscribe `ws` to open Document `doc_id` and send its hello, or send an error when it is not open."""
        d = self.docs.get(doc_id) if isinstance(doc_id, str) and _DOC_ID.fullmatch(doc_id) else None
        if d is None:
            await self._push(ws, {"type": "error", "document": doc_id if isinstance(doc_id, str) else None,
                                  "message": "the Document is not open"})
            return
        self._subs[ws].add(d.id)
        await self._push(ws, await self._hello(client, d))

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
        doc_id = msg.get("document") if isinstance(msg, dict) else None
        if doc_id is None and self._single is not None:
            doc_id = self._single.id                              # the old client names no Document
        if kind == "ping":
            await self._push(ws, {"type": "pong", "t": msg.get("t")})
        elif kind in ("hello", "subscribe"):
            if doc_id is None:
                await self._push(ws, await self._hello(client, None))
            else:
                await self._subscribe(ws, client, doc_id)
        elif kind == "unsubscribe":
            self._subs[ws].discard(doc_id)
        elif kind == "edit":
            ident = msg.get("id")
            try:
                d = self.docs.get(doc_id) if isinstance(doc_id, str) else None
                if d is None:
                    raise self.session_error("the message must name an open Document")
                tab = msg.get("tab")
                if not isinstance(tab, str) or tab not in d.tab_ids:
                    raise self.session_error("unknown tab")
                if not isinstance(msg.get("edit"), dict):
                    raise self.session_error("an edit is a dict with an 'op' key")
                _, base = self._edit_meta(None, msg.get("base"))
                rebuilt = await self._edit(d, tab, msg["edit"], client, base)
            except DocumentLocked as e:
                await self._push(ws, {"type": "error", "id": ident, "document": e.document, "message": str(e),
                                      "locked": True, "holder": e.holder})
            except self.session_error as e:
                await self._push(ws, {"type": "error", "id": ident, "message": str(e)})
            else:
                await self._push(ws, {"type": "edited", "id": ident, "document": d.id, "rebuilt": rebuilt})
        else:
            await self._push(ws, {"type": "error", "message": "unknown message"})


def make_server(session=None, port=0, allow_origins=(), client_ttl=CLIENT_TTL, client_expired=None, *,
                registry=None, root=None, folder=None, lock_program=None):
    """A server on 127.0.0.1:`port` (bound, not yet serving). Over `session`: a single Document (the old routes
    alias it); `folder` and `lock_program` (the CLI passes "browser") make it take the Document lock, so an
    in-process server given neither has no lock file. Over `registry` and `root` instead: a Documents root, where
    Documents are created under `root` and opened by id. Callers run `serve_forever` (it blocks) and, at the end,
    `shutdown`, `server_close` and close the session; `close_all` does the last three."""
    return _Server(session, port, allow_origins, client_ttl, client_expired, registry, root, folder, lock_program)


def close_all(server, session=None, wait=10.0):
    """Stop `server`, release its locks and close `session` (the one given to make_server) and every Document the
    server opened itself, waiting up to `wait` seconds for a call in flight (its lock)."""
    server.shutdown()
    server.server_close()
    server.close_documents(wait)
    if session is not None:
        session.close()                                      # a no-op when the server's own table closed it


def registry_mismatch(registry):
    """A message when the registry lists Documents and none of their folders exist (the Documents are not mounted at
    the path the registry records), else None. The registry stores absolute real paths, so serve must see the
    Documents where the desktop does (docs/serve.md)."""
    rows = registry.list_documents()
    if rows and all(r["missing"] for r in rows):
        return (f"the registry {registry.path} lists {len(rows)} Document(s) but none of their folders exist; the "
                f"first is {_clean(rows[0]['path'], 300)!r}. The registry stores each Document by its absolute "
                "path, so serve must see the Documents at the same path as the desktop: mount the config folder "
                "at the same path, with the same home and data directory (see docs/serve.md).")
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m freeshape serve",
                                 description="Serve one Document, or a folder of Documents, over HTTP on 127.0.0.1 "
                                             "(no authentication).")
    ap.add_argument("document", help="a Document folder (serves that one Document), or a Documents root "
                                     "(a folder that is not itself a Document: serves the registry's Documents "
                                     "and makes new ones inside it)")
    ap.add_argument("--data-dir", default=None, metavar="DIR",
                    help="the app's data directory, whose documents.json is the registry (default: the "
                         "desktop's, or $FREESHAPE_DATA_DIR); used with a Documents root")
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
    if not os.path.isdir(args.document):
        print(f"freeshape serve: {args.document!r} is not a folder.", file=sys.stderr)
        return 2
    from . import doc_registry
    session = None
    if os.path.isfile(os.path.join(args.document, doc_registry.MANIFEST_NAME)):
        from .session import DocumentSession, SessionError
        try:
            session = DocumentSession(args.document)
        except SessionError as e:
            print(f"freeshape serve: cannot open the Document: {e}", file=sys.stderr)
            return 1
        extra = {"folder": os.path.realpath(args.document), "lock_program": LOCK_PROGRAM}
    else:
        data = args.data_dir or doc_registry.data_dir()
        registry = doc_registry.DocRegistry(os.path.join(data, doc_registry.REGISTRY_NAME))
        problem = registry_mismatch(registry)
        if problem:
            print(f"freeshape serve: {problem}", file=sys.stderr)
            return 1
        extra = {"registry": registry, "root": args.document}
    try:
        server = make_server(session, args.port, args.allow_origin, args.client_expiry, **extra)
    except OSError as e:
        if session is not None:
            session.close()
        print(f"freeshape serve: cannot listen on {BIND_HOST}:{args.port}: {e}", file=sys.stderr)
        return 1
    except BaseException:
        if session is not None:
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
