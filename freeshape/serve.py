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
    POST /documents/<id>/recover     loads the recoverable autosave(s) into the open session as unsaved edits (never
                                writes the Document), then removes them; 404 when there is none (see "Autosave")
    POST /documents/<id>/discard-autosave   deletes the recoverable autosave(s) of the Document
    GET  /documents/<id>/tabs                  {"document": {"id", "name"}, "tabs": [...], "read_only", "holder"}
    GET  /documents/<id>/tabs/<tab>/outputs    the tab's outputs in the engine wire format (octet-stream)
    GET  /documents/<id>/tabs/<tab>/mesh.json  display meshes, see `mesh_document`
    POST /documents/<id>/tabs/<tab>/edit       body: a contract edit dict; reply {"rebuilt": [ids]}
    POST /documents/<id>/tabs/<tab>/refs       body: {"picks": [pick, ...]}; reply {"document", "tab", "revision",
                                "items": [item, ...]}, one item per pick, in order (I4.3; see "Refs" below)
    POST /documents/<id>/tabs/<tab>/features/<fid>/dialog        open a feature dialog (I5.3; see "Dialogs" below)
    POST /documents/<id>/tabs/<tab>/features/dialog              open a dialog for a NEW feature (I6.1; "Dialogs")
    GET  /documents/<id>/tabs/<tab>/features/types[?picks=<JSON>]  feature types, label, standard, can_start (I6.1)
    POST /documents/<id>/tabs/<tab>/dialog/<did>/update|accept|cancel
    GET  /documents/<id>/tabs/<tab>/dialog/<did>/preview         the dialog's newest preview (I5.4), wire format
    POST /documents/<id>/save                  body: {} ; reply {"saved": [ids]}
    GET  /ws?client=<id>[&document=<id>]       the live channel (WebSocket, JSON text messages; `_Server._socket`)
  On a server over one Document folder the old un-prefixed routes stay as aliases for that Document:
    GET /tabs, GET /tabs/<id>/outputs, GET /tabs/<id>/mesh.json, POST /tabs/<id>/edit, POST /save
  An edit or save on a Document this server does not hold the lock of answers 423 {"error", "document", "holder"}.
  A Document id is a key into the open table and the registry; it is never joined to a path. A new Document's
  folder name is made from its name by `folder_name_for`.

Refs (I4.3, freeshape/picking.py). A pick in the Browser client's viewport is turned into the stored reference a
feature's params hold (an Element name plus a geometric signature), by the same code the desktop uses. It is a
query: it changes nothing (no edit, no revision, no undo step, not dirty, no rebuild), so a read-only Document
answers it too (the client may show what is under the pen while it cannot edit). Part Studio tabs only; another
kind of tab is a 400. A pick is
    {"part": id, "kind": kind, "index": n | "entity": id, "point": [x, y, z], "want": [kind, ...]}
  kind   "face", "edge", "vertex", "part", "region", "sketch_line" or "sketch_point".
  part   the part's id; for "region", "sketch_line" and "sketch_point" the id of the sketch feature.
  index  the element's 1-BASED index, as the engine's Element names and `PartOut.names` count (the first face is
         1): required for face, edge, vertex and region (a sketch's region i is regions[i - 1]), absent for the
         others. A client whose decoder counts from 0 adds 1 (web/src/wire/types.ts). 0 and a number past the end
         are a 400.
  entity the sketch entity id ("l3", "c1", "pt2"), required for "sketch_line" and "sketch_point", absent otherwise.
  point  optional, three finite numbers (the hit point in model coordinates). It is checked and not used: the answer
         does not depend on it.
  want   the kinds the field being filled accepts: any of freeshape.picking.WANTED (face, edge, vertex, plane, axis,
         part, path, region, element, sketch_line, sketch_point, axis_feature, axis_connector, connector, feature),
         as a dialog field's own `want`. The answer depends on it exactly as on the desktop: a face is a "face"
         reference for a face field, its edges ("edges", a list) for an edge field, a "plane" for a plane field.
  Up to 200 picks; unknown fields are refused. A malformed body, an unknown part or sketch, or an index out of range
  is a 400 {"error": "picks[2].index: ..."} naming the field. Nothing is partly answered.
Each item is either
    {"ok": true, "kind": k, "ref": ..., "name": n}   `ref` is exactly what the feature's params store (send it back
         unchanged in an edit); `kind` is the item kind the field stores it as ("face", "edge", "edges", "vertex",
         "region", "plane", "axis", "part", "path", "sketch_line", "sketch", ...); `name` is the Element name of
         the face, edge or vertex (also of the face a plane or axis came from) or null (a part, a region without
         a name, a legacy reference). Compare it with the name the client already holds for that index.
    {"ok": false, "code": c, "reason": text}   the pick cannot fill that field; `reason` is a sentence to show.
         codes: "wrong_kind" (nothing in `want` takes this kind: a vertex for an edge field says "A vertex cannot be
         used here; an edge is needed"; a face is not one, it gives its edges),
         "not_flat" (a plane is wanted, the face is curved), "no_axis" (an axis is wanted, the face is not round),
         "no_region" (a sketch line that bounds no closed area), "nothing_here" (the right kind, but no item).

Dialogs (I5.3, freeshape/session.py FeatureDialog, freeshape/dialog_schema.py). A feature dialog edits an existing
feature of a Part Studio tab. Every dialog route needs X-Client-Id (a 400 without): the dialog belongs to that client
and lives in its registry entry (made by the first dialog when the client has no socket yet), so it survives a
reconnect, goes when the entry expires and when its Document closes, and any other client id gets a 404 for it.
At most MAX_DIALOGS (8) per client and MAX_DIALOGS_TOTAL (256) in all (409). Bodies take no unknown field.
  open    POST .../features/<fid>/dialog  body {}; 201 {"dialog": did, "document", "tab", "feature": fid, "name",
          "revision", "description", "values", "fields", "active"}. Open is a query, so a read-only Document may open
          one; accept is refused there (423, the dialog stays open).
          description  freeshape.dialog_schema.describe(type, params): fields (key, kind, standard, visible, ...).
          values       {key: {"value", "text", "error"?}} for the number, choice, ops, check and text rows; a number
                       row's "value" is the evaluated number (or null), "text" what the person typed (or the stored
                       expression), "error" the message of the last input the dialog refused.
          fields       {key: [item]} for the selection rows; item = {"ok": true, "kind", "ref", "name"}, the refs
                       route's item (name is the Element name the stored reference holds, null for a legacy one).
          active       null on open.
  create  (I6.1) POST .../tabs/<tab>/features/dialog  body {"type": a feature type with a dialog, "picks": [pick,...]
          (optional)}. A pick is the refs route's (1-based indices) with `want` optional. The type's default
          parameters are made as the desktop makes them, the picks seed its selection fields with
          dialog_schema.apply_preselection (run in the engine worker, where the shapes are), and a dialog in create
          mode opens at the rollback position. 201, the same reply as above with "feature": null (the engine gives
          the id at accept) and "type"; the fields hold what the picks chose, a pick no field takes is dropped.
          Updates, previews (the engine is asked for a NEW feature) and cancel are the same routes. Accept sends
          add_feature with no index (the rollback bar, else the end), 200 {"rebuilt", "revision"}, the usual
          `changed` push; 423 read-only. A pick the tab cannot read, an unknown type or a bad body is a 400.
  types   (I6.1) GET .../tabs/<tab>/features/types[?picks=<URL-encoded JSON list of picks>]  200 {"document", "tab",
          "revision", "types": [{"type", "label", "standard", "can_start"}]}, sorted by type. `standard` is false
          for the types with a hand-built row (dialog_schema.custom_feature_types()); `can_start` is true when no
          picks are given, else when a pick would fill one of the type's selection fields. No X-Client-Id.
  update  POST .../dialog/<did>/update  body {"values": {key: value}} (1..64 keys). A number takes text or a number
          and is evaluated by freeshape.expr (units, #variables, 1/2 in), the desktop's rule: an expression that names
          a #variable is stored as text, else as the number; below `min`, or zero where `signed`, is refused. A choice
          or ops value must be one of its options, a check a bool, a text row text, a selection row a list of
          {"kind", "ref"} items (at most 200). Each change goes through on_field_set, then fields_to_params. Reply 200
          {"description", "values", "fields", "active", "errors": {key: message}}: `active` is the field the change
          named to fill next (end "Up to face" gives "upto_face"), else null; a refused number is in `errors`, its
          last good value unchanged, the other keys of the call still applied. An unknown key or a value of the
          wrong type is a 400 naming it and nothing is applied.
  accept  POST .../dialog/<did>/accept  body {}; sends replace_feature (the desktop's accept: hole label, loft fold),
          closes the dialog, 200 {"rebuilt", "revision"}, and the `changed` push every edit makes. 423 on a read-only
          Document; a refusal by the engine is a 400 and leaves the dialog open.
  cancel  POST .../dialog/<did>/cancel  body {}; closes it, changes nothing, 200 {"cancelled": true}.
  preview (I5.4) Each update also asks the engine for a preview (contract.Engine.preview: the model rebuilt from the
          state before the feature with the dialog's params, stopping there; a query, so no revision, undo step,
          dirty flag or saved byte changes) and the update reply carries its number, "preview": n (null when it
          could not be asked for). When it is ready the server sends, to the sockets of the client that owns the
          dialog and are subscribed to its Document, and to no other socket,
            {"type": "preview", "document", "tab", "dialog": did, "feature": fid, "preview": n, "revision": the
             committed revision, "ok": bool, "outputs": bool, "error": null or a message naming the feature}
          and the client fetches the model with
            GET .../dialog/<did>/preview   (X-Client-Id as for every dialog route) 200 the previewed StudioOutputs in
                the engine wire format, exactly as GET .../tabs/<tab>/outputs sends a tab's (the same decoder reads
                it), with header X-Preview: n; 404 when the dialog has no preview with a model.
          `ok` false with `outputs` true: the feature failed, and the model is the one before it. The server keeps
          only the newest preview per dialog; an update made while a preview is building supersedes any update's
          preview still waiting to start (the engine's queue rule), so a fast run of updates sends a `preview` for
          the first and the last, not for each. Accept and cancel discard the preview, and one that finishes after
          them is dropped.

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
Autosave and idle close (I2.6, freeshape/autosave.py). Edits made here live in the session until `save`, so a Document
whose lock this server holds is autosaved to `<data dir>/autosave-server/<lock instance id>/` every `--autosave-interval`
seconds (30) and a few seconds after each burst of edits, when something changed; a save removes it. The desktop never sees
that folder. An autosave whose holder still holds a fresh lock is live and not touched; one whose holder's lock is gone or
stale is recoverable. `POST .../open` and `GET /documents` carry `autosave` (null, or {"time", "count", "tabs", ...}) for a
recoverable one; only `recover` and `discard-autosave` (and a close that says {"discard": true}) delete it. When no client has
a socket on an open Document for the client expiry time (`--client-expiry`), the server autosaves it, closes the session and
releases the lock (a `closed` message with "reason": "idle" goes to a client that comes back). A graceful stop and a plain
close autosave too. The server never writes an autosave over the Document.
State of one person lives in `ClientRegistry`, keyed by client id, kept `client_ttl` seconds (default 600, option
`--client-expiry`) after the client's last socket closes; a reconnect with the same id finds its entry.

Every call into the session holds one lock (the session has none) and runs on a thread pool, never on the event
loop; /health and the other routes that need no session never wait for it. Errors are JSON {"error": message}: 400 a bad edit or body, 403/421 a wrong Host
or Origin, 404 an unknown route or tab, 405 a wrong method, 411 no Content-Length on a POST, 413 a body over
1 MB, 415 a POST that is not application/json, 500 anything unexpected (generic body; detail on stderr).

Sketch sessions (I7.3, freeshape/sketch_session.py, ADR 0015). A sketch is edited on the server, over the live channel,
one sketch per client id, kept in that client's registry entry: it survives a reconnect with the same id. Every message
is JSON text on /ws with "type", an optional "id" (a string of at most 64 characters or an integer, echoed in the
answer) and "document" (needed by sketch.open on a Documents root, as for an edit; on the others it may be left out).
The answer goes to the sending socket only. A message with an unknown field, a value of the wrong type or anything the
sketch cannot take is answered with
    {"type": "error", "id", "for": <the message type>, "message": a sentence naming the field}
and changes nothing; the socket stays open. A step that is refused leaves the sketch and its undo stack as they were.
  sketch.open   {"type": "sketch.open", "document", "tab", "feature": id}  an existing sketch feature of a Part Studio, or
                {"type": "sketch.open", "document", "tab", "plane": ref}   a new sketch on a plane reference, as a sketch's
                "plane" param stores it: {"kind": "std", "name": "Top" | "Front" | "Right"}, {"kind": "plane", "fid"}
                or {"kind": "face", "face": a face reference (the refs route's "plane" item)}; a face that is not
                planar is an error naming it. Refused while this client has a sketch open, and past MAX_SKETCHES.
                Answer {"type": "sketch.opened", "document", "tab", "feature": id or null, "name", "plane", "sketch",
                "status", "can_undo", "can_redo", "frame", "edges", "revision"}:
                  sketch   {"points": {id: [u, v]}, "entities": {id: entity}, "constraints": [constraint]}, the
                           freeshape.sketch format (an existing sketch exactly as its params store it);
                  status   {"ok", "dof", "entities": {id: "free" | "defined" | "conflicting"}, "points": {id: same},
                           "constraints": {id: "ok" | "conflicting"}} (sketch_session.status_of);
                  frame    {"origin", "xdir", "ydir", "normal"} and edges [{"pick", "points", "height"}]: the engine's
                           sketch_frame (contract.Engine.sketch_frame, I7.1);
                  revision the tab's revision when it opened; name "Sketch <n>" for a new sketch.
  sketch.add    {"type": "sketch.add", "shape", "picks", "construction"?, "radius"?, "sweep"?, "constraints"?}
                shape "point" (1 pick), "line" (start, end), "rect" (corner, opposite corner), "center_rect" (centre,
                corner), "circle" (centre, and "radius"), "arc3" (start, end, a point it passes through: a clockwise
                pick swaps the ends), "arc_center" (centre, start, end direction, and "sweep": below 0 the pen went
                clockwise), each built by freeshape.sketch_ops. A pick is [u, v] (a new point, sketch coordinates)
                or {"at": [u, v], "point": id} (that existing point). constraints [{"type", "refs", "value"?}] (at
                most 64) are attached in the same step: a ref is an id in the sketch, "$p<i>" (the point of pick i)
                or "$e<i>" (entity i of "made"); a coincident between a new point and an old one merges them.
  sketch.delete {"type": "sketch.delete", "ids": [entity, point or constraint id]}  (and what depends on them)
  sketch.construction {"type": "sketch.construction", "ids": [entity id], "value"?: bool}  (no value: turn over)
  sketch.undo, sketch.redo  {"type": ...}  inside the session; nothing to undo or redo is an error
                Each of these four answers {"type": "sketch.changed", "document", "tab", "feature", "name", "plane",
                "sketch", "status", "can_undo", "can_redo"}, the whole sketch after the step's solve; sketch.add adds
                "made": {"points": [point id per pick], "entities": [ids the builder returned]}.
  sketch.state  {"type": "sketch.state"}  answers {"type": "sketch.state"} with the fields of sketch.opened but
                "revision" (a client that reconnected asks for it).
  sketch.commit {"type": "sketch.commit"}  writes the sketch through the normal edit path: replace_feature (an
                existing sketch) or add_feature (a new one, at the rollback bar), so the `changed` push and the revision
                are an edit's; the regions and names come from the rebuild. Answer {"type": "sketch.committed",
                "document", "tab", "feature": the feature id, "rebuilt", "revision"}; the session ends. On a read-only
                Document the answer is the error with "locked": true and "holder", and the session stays open; so does
                it when the engine refuses the feature.
  sketch.cancel {"type": "sketch.cancel"}  ends the session, changes nothing: {"type": "sketch.cancelled", "document",
                "tab", "feature"}.
When the client's registry entry expires, or the Document closes or the server stops gracefully, an unfinished sketch is
written into the Document's autosave (I2.6) before the session is dropped: the tab's autosaved data holds the sketch
feature (an existing one with the session's params, a new one added at the rollback bar), the open session is not
changed, and recover brings it back as an unsaved edit. It stays in every later autosave of that Document while it is
open on this server.
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

from . import autosave as AS
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
AUTOSAVE_INTERVAL = 30.0        # seconds between autosaves of a Document that changed
AUTOSAVE_BURST = 3.0            # seconds after an edit, with no further edit, before the burst is autosaved
MAX_OPEN_DOCUMENTS = 1          # the open-Document limit; see `_Server._open_refusal`
MAX_NAME = 100                  # longest Document name POST /documents accepts
MAX_FOLDER = 80                 # longest folder name made from it
_DOC_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_TAB_PATH = re.compile(r"^/tabs/([A-Za-z0-9_-]{1,64})/(outputs|mesh\.json|edit)$")
_DOC_PATH = re.compile(r"^/documents/([A-Za-z0-9_-]{1,64})"
                       r"(?:/(open|close|take-over|save|tabs|recover|discard-autosave)"
                       r"|/tabs/([A-Za-z0-9_-]{1,64})(?:/(outputs|mesh\.json|edit|rename|refs))?)$")
_DOC_VERBS = {"open": "POST", "close": "POST", "take-over": "POST", "save": "POST", "tabs": "GET", "recover": "POST",
              "discard-autosave": "POST"}
_DIALOG_PATH = re.compile(r"^/documents/([A-Za-z0-9_-]{1,64})/tabs/([A-Za-z0-9_-]{1,64})"
                          r"(?:/features/(?:([A-Za-z0-9_.-]{1,64})/(dialog)|(dialog|types))"
                          r"|/dialog/([0-9a-f]{16})/(update|accept|cancel|preview))$")
MAX_PICKS_QUERY = 8000          # longest query string of GET .../features/types
MAX_DIALOGS = 8                 # open dialogs one client may hold
MAX_DIALOGS_TOTAL = 256         # and all clients together
_FIXED = {"/health": ("GET",), "/tabs": ("GET",), "/save": ("POST",)}
_EDGE_METHODS = {"outputs": "GET", "mesh.json": "GET", "edit": "POST", "rename": "POST", "refs": "POST"}
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


class NoAutosave(Exception):
    """recover or discard-autosave on a Document with no recoverable autosave (HTTP 404)."""


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
        self.autosaver = None                # the asyncio task that autosaves every interval
        self.burst = None                    # the timer handle that autosaves after a burst of edits
        self.idle = None                     # the timer handle that closes the Document when nobody is connected
        self.seq = 0                         # counts changes made through the server (under `lock`)
        self.written = 0                     # `seq` as of the last autosave written or removed

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
                 registry=None, root=None, folder=None, lock_program=None, data_dir=None,
                 autosave_interval=AUTOSAVE_INTERVAL, autosave_burst=AUTOSAVE_BURST,
                 lock_timeout=doc_lock.DEFAULT_TIMEOUT):
        from .session import SessionError
        self.session_error = SessionError
        self.registry = registry
        self.root = os.path.realpath(root) if root is not None else None
        self.lock_program = lock_program
        self.data_dir = os.fspath(data_dir) if data_dir is not None else None    # None: no autosave
        self.autosave_interval = autosave_interval
        self.autosave_burst = autosave_burst
        self.lock_timeout = lock_timeout                         # seconds after which a heartbeat is stale
        self._closing = set()                                    # ids being closed: still open until the lock is released
        self.closed_reasons = {}                                 # Document id -> why the server closed it (idle)
        self._bg = set()                                         # tasks started from timers
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
        self.clients.on_expire = self._sketch_expiry(client_expired)      # I7.3: keep an unfinished sketch first
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
                file_lock = doc_lock.acquire(folder, program, self.lock_timeout)
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
                self._disarm_idle(d)
            for task in list(self._bg):
                task.cancel()
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
                if got and d.file_lock is not None:
                    self._add_kept(d, self._take_sketches(d))    # I7.3: and the unfinished sketches
                    self._final_autosave(d)                      # a graceful stop keeps what was not saved
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
            loop = asyncio.get_running_loop()
            d.refresher = loop.create_task(self._refresh_loop(d))
            if self.data_dir is not None:
                d.autosaver = loop.create_task(self._autosave_loop(d))

    def _stop_refresh(self, d):
        task, d.refresher = d.refresher, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        task, d.autosaver = d.autosaver, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        burst, d.burst = d.burst, None
        if burst is not None:
            burst.cancel()

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
            await self._lost(d)
        return ok

    def refresh_locks_now(self, timeout=30.0):
        """Run one refresh of every open Document now, from any thread; {id: still ours}. For tests and for a
        caller that must not wait for the interval."""
        async def go():
            return {d.id: await self._refresh_one(d) for d in list(self.docs.values())}
        return asyncio.run_coroutine_threadsafe(go(), self._loop).result(timeout)

    async def _lost(self, d):
        """The lock was taken from us: tell the clients, and autosave what was not saved (once, under our instance)."""
        await self._announce_lock(d)
        await self._autosave_one(d, final=True)

    async def _announce_lock(self, d):
        state = await asyncio.get_running_loop().run_in_executor(self.pool, d.lock_state)
        await self._broadcast({"type": "lock", "document": d.id, **state}, d)

    # ------------------------------------------------------------------ autosave
    def _autosave_write(self, d, final=False):
        """Session lock held (pool thread). Write `d`'s autosave under this server's lock instance when something
        changed since the last one, or remove it when nothing is unsaved any more; True when a file was written.
        A Document that is read-only is not autosaved, except `final` (the lock was just lost, or it is closing)."""
        lock = d.file_lock
        if self.data_dir is None or lock is None or (d.is_read_only() and not final) or d.seq == d.written:
            return False
        state = d.session.autosave_state_with(getattr(d, "kept_sketches", None))     # I7.3: with kept sketches
        if state is None:
            AS.remove(self.data_dir, lock.instance)
            d.written = d.seq
            return False
        state["document"] = d.id
        AS.write(self.data_dir, lock.instance, state)
        d.written = d.seq
        return True

    def _final_autosave(self, d):
        try:
            self._autosave_write(d, final=True)
        except Exception as e:                                   # noqa: BLE001  (never stop a close for this)
            _log(f"autosave of {_clean(d.id, 16)} at close: {type(e).__name__}: {_clean(str(e), 200)}")

    async def _autosave_one(self, d, final=False):
        """Autosave `d` now if something changed (off the loop); True when a file was written."""
        if self.data_dir is None or d.file_lock is None or (not final and self.docs.get(d.id) is not d):
            return False
        try:
            return await self._in_session(d, lambda: self._autosave_write(d, final))
        except Exception as e:                                   # noqa: BLE001  (file trouble: try again later)
            _log(f"autosave of {_clean(d.id, 16)}: {type(e).__name__}: {_clean(str(e), 200)}")
            return False

    async def _autosave_loop(self, d):
        while True:
            await asyncio.sleep(self.autosave_interval)
            if self.docs.get(d.id) is not d:
                return
            await self._autosave_one(d)

    def _spawn(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    def _arm_burst(self, d):
        """Event loop. An edit was made: autosave once `autosave_burst` seconds pass with no further edit."""
        if self.data_dir is None or d.file_lock is None:
            return
        if d.burst is not None:
            d.burst.cancel()
        d.burst = asyncio.get_running_loop().call_later(self.autosave_burst, lambda: self._spawn(self._autosave_one(d)))

    def autosave_now(self, timeout=60.0):
        """Autosave every open Document that changed, now, from any thread; {id: a file was written}. For tests and for
        a caller that must not wait for the interval."""
        async def go():
            return {d.id: await self._autosave_one(d) for d in list(self.docs.values())}
        return asyncio.run_coroutine_threadsafe(go(), self._loop).result(timeout)

    def _recoverable(self, d):
        """Pool thread. The autosaves of Document `d` that may be offered: oldest first, not the live one and not ours."""
        if self.data_dir is None or d.folder is None:
            return []
        infos = AS.index(self.data_dir).get(d.id, [])
        if not infos:
            return []
        try:
            holder = doc_lock.holder(d.folder, self.lock_timeout)
        except OSError:
            holder = None
        return AS.recoverable(infos, holder, self._own_instance(d))

    @staticmethod
    def _own_instance(d):
        """The instance id of the lock `d` holds, or None when it holds none or has lost it (its autosave is then
        like anyone's: its holder's lock is gone)."""
        return d.file_lock.instance if d.file_lock is not None and not d.lost else None

    def _offer(self, d):
        """Session lock held. The `autosave` field of an open reply: null, or what recovering would do: the newest
        time, how many autosaves there are, the tabs they hold, the tabs the saved Document has changed since they were
        written (`moved_on`: recover would replace those), and whether document.json changed (`list_moved_on`)."""
        infos = self._recoverable(d)
        if not infos:
            return None
        tabs, deleted, moved, list_moved = {}, [], set(), False
        for info in infos:
            summary = info.summary()
            tabs.update({t["id"]: t["name"] for t in summary["tabs"]})
            deleted += [x for x in summary["deleted"] if x not in deleted]
            m, lm = d.session.autosave_moved_on(info.bases(), info.manifest_base, info.list_changed)
            moved.update(m)
            list_moved = list_moved or lm
        return {"time": infos[-1].time, "count": len(infos), "tabs": [{"id": k, "name": v} for k, v in sorted(tabs.items())],
                "deleted": deleted, "moved_on": sorted(moved), "list_moved_on": list_moved,
                "writable": not d.is_read_only()}

    async def _recover(self, d):
        """POST .../recover: put the recoverable autosave(s) on the open session as unsaved edits (oldest first, so the
        newest wins a tab both hold), write a new autosave of that state under this server's lock, then remove the ones
        applied. The Document on disk is not written; the client's Save does that. 423 when the Document is read-only."""
        def work():
            d.check_writable()
            infos = self._recoverable(d)
            if not infos:
                raise NoAutosave("there is no autosave to recover")
            moved, list_moved, applied, skipped, recovered, unreadable = set(), False, [], [], [], 0
            for info in infos:
                m, lm = d.session.autosave_moved_on(info.bases(), info.manifest_base, info.list_changed)
                try:
                    snap = AS.load(self.data_dir, info)
                except AS.AutosaveError as e:
                    _log(f"autosave {info.instance[:8]}: {_clean(str(e), 200)}")
                    unreadable += 1
                    continue
                got = d.session.restore_autosave(snap)
                moved.update(m)
                list_moved = list_moved or lm
                skipped += got["skipped"]
                recovered += [t for t in got["recovered"] if t not in recovered]
                applied.append(info)
            d.seq += 1
            d.refresh_tab_ids()
            self._autosave_write(d)                             # the recovered state is safe before the old files go
            for info in applied:
                AS.remove(self.data_dir, info.instance)
            return {"recovered": recovered, "moved_on": sorted(moved), "list_moved_on": list_moved, "skipped": skipped,
                    "unreadable": unreadable}, self._tab_view(d, recovered)
        try:
            out, tabs = await self._in_session(d, work)
        except DocumentLocked:
            if d.lost:
                await self._lost(d)
            raise
        self._arm_burst(d)
        await self._announce(d, {"type": "changed", "document": d.id, "client": None, "base": None,
                                 "rebuilt": out["recovered"], "revisions": {t["id"]: t["revision"] for t in tabs
                                                                            if t["id"] in out["recovered"]},
                                 "saved": []}, tabs)
        return {**out, "tabs": tabs}

    async def _discard_autosave(self, d):
        """POST .../discard-autosave: delete the recoverable autosave(s) of the Document; the session is not touched."""
        def work():
            d.check_writable()
            infos = self._recoverable(d)
            if not infos:
                raise NoAutosave("there is no autosave to discard")
            for info in infos:
                AS.remove(self.data_dir, info.instance)
            return len(infos)
        n = await self._in_session(d, work)
        return {"discarded": n}

    # ------------------------------------------------------------------ idle close
    def _watched(self, d):
        """Whether any socket is subscribed to Document `d`."""
        return any(d.id in subs for subs in self._subs.values())

    def _disarm_idle(self, d):
        handle, d.idle = d.idle, None
        if handle is not None:
            handle.cancel()

    def _arm_idle(self, d):
        """Event loop. Start the idle timer of `d` unless a client is connected to it (or it is the single Document of
        a server that stays open): when it runs out the Document is autosaved and closed."""
        self._disarm_idle(d)
        if d.single or self._watched(d) or self.docs.get(d.id) is not d:
            return
        d.idle = asyncio.get_running_loop().call_later(self.clients.ttl, lambda: self._spawn(self._idle_fire(d)))

    async def _idle_fire(self, d):
        d.idle = None
        await self._idle_close(d)

    async def _idle_close(self, d):
        """Autosave `d`, close its session and release its lock, when no client is connected to it; True when closed."""
        async with self._open_lock:
            if self.docs.get(d.id) is not d or d.single or self._watched(d):
                return False
            self.closed_reasons[d.id] = "idle"
            await self._release_document(d, reason="idle")
        return True

    def expire_idle_now(self, timeout=60.0):
        """Close every open Document that no client is connected to, as the idle timer would; the ids closed. From any
        thread, for tests and for a caller that must not wait for the client expiry time."""
        async def go():
            return [d.id for d in list(self.docs.values()) if not d.single and await self._idle_close(d)]
        return asyncio.run_coroutine_threadsafe(go(), self._loop).result(timeout)

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
        except NoAutosave as e:
            resp = self._fail(404, str(e))
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
        m = _DIALOG_PATH.match(path)
        if m is not None:
            verb = "open" if m.group(4) else ("new" if m.group(5) == "dialog" else m.group(5) or m.group(7))
            return "dialog." + verb, m.group(1), m.group(2), "GET" if verb in ("preview", "types") else "POST"
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
        except (UnicodeDecodeError, ValueError, RecursionError):        # a body nested past the parser's depth
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
            return await self._close_document(doc_id, body.get("discard") is True)
        if route == "recover":
            return self._json(200, await self._recover(d))
        if route == "discard-autosave":
            return self._json(200, await self._discard_autosave(d))
        if route == "take-over":
            return await self._take_over(d)
        if route in ("/tabs", "tabs"):
            tabs = await self._in_session(d, lambda: self._tab_view(d))      # with each "revision", as open and the socket give
            return self._json(200, {"document": {"id": d.session.id, "name": d.session.name}, "tabs": tabs,
                                    **await self._off_loop(d.lock_state)})
        if route == "tabs.add":
            return await self._add_tab(d, body)
        if tab is not None and tab not in d.tab_ids:
            return self._fail(404, "not found")
        if route == "rename":
            return await self._rename_tab(d, tab, body)
        if route == "refs":
            return await self._refs(d, tab, body)
        if route.startswith("dialog."):
            return await self._dialog(d, tab, route[7:], body, request)
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
        autosaves = AS.index(self.data_dir) if self.data_dir is not None else {}
        for r in rows:
            try:
                h = doc_lock.holder(r["folder"], self.lock_timeout) if r["folder"] else None
            except OSError:
                h = None
            mine = self.docs.get(r["id"])
            found = AS.recoverable(autosaves.get(r["id"], []), h, self._own_instance(mine) if mine is not None else None)
            out.append({"id": r["id"], "name": r["name"], "modified": _modified(r["folder"]) if r["folder"] else None,
                        "holder": _holder_json(h), "open": r["id"] in self.docs or r["id"] in self._closing,
                        "missing": r["missing"],
                        "autosave": {"time": found[-1].time, "count": len(found)} if found else None})
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
                self.closed_reasons.pop(doc_id, None)
                self._start_refresh(d)
            self._arm_idle(d)                                    # nobody is connected to it yet
        tabs, offer = await self._in_session(d, lambda: (self._tab_view(d), self._offer(d)))
        return self._json(200, {"document": {"id": d.id, "name": d.name}, "tabs": tabs, "autosave": offer,
                                **await self._off_loop(d.lock_state)})

    def _start_document(self, doc_id, folder):
        """Pool thread. Take the lock, then start the session (so it reads what the last holder saved)."""
        file_lock, holder = None, None
        try:
            file_lock = doc_lock.acquire(folder, LOCK_PROGRAM, self.lock_timeout)
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

    async def _close_document(self, doc_id, discard=False):
        """POST /documents/<id>/close: autosave what is not saved (or, with {"discard": true}, remove this server's
        autosave of it), stop the refresh, release the lock, close the session, tell the sockets."""
        async with self._open_lock:
            d = self.docs.get(doc_id)
            if d is None:
                if self.registry is not None and await self._off_loop(self._known, doc_id):
                    return self._json(200, {"closed": False})   # already closed: closing is idempotent
                return self._fail(404, "not found")
            if d.single:
                return self._fail(409, "this server serves a single Document; it stays open")
            await self._release_document(d, discard=discard)
        return self._json(200, {"closed": True})

    async def _release_document(self, d, discard=False, reason=None):
        """`_open_lock` held. Take `d` out of the table, tell its sockets (`closed`, with `reason` when given), then
        under the session lock: autosave unsaved changes (or remove this server's autosave when `discard`), release
        the lock file and close the session. The autosave is written before the lock goes."""
        self._stop_refresh(d)
        self._disarm_idle(d)
        self.docs.pop(d.id, None)
        self.drop_dialogs(d.id)
        await self._keep_sketches(d)                             # I7.3: unfinished sketches go to the autosave
        self._closing.add(d.id)
        message = {"type": "closed", "document": d.id}
        if reason is not None:
            message["reason"] = reason
        await self._broadcast(message, d)
        for ws, subs in list(self._subs.items()):
            subs.discard(d.id)

        def shut():
            with d.lock:
                try:
                    if d.file_lock is not None and self.data_dir is not None:
                        if discard:
                            AS.remove(self.data_dir, d.file_lock.instance)
                        else:
                            self._final_autosave(d)
                    if d.file_lock is not None:
                        d.file_lock.release()
                finally:
                    d.session.close()
        try:
            await self._off_loop(shut)
        finally:
            self._closing.discard(d.id)

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
                d.file_lock = doc_lock.take_over(d.folder, LOCK_PROGRAM, self.lock_timeout)
                old = d.session
                d.session = self._new_session(d.folder)
                old.close()
                d.tab_ids = frozenset(t["id"] for t in d.session.tabs())
                d.revisions = {}
                d.tabs_sig = None
                d.read_only = d.lost = False
                d.holder = None
                d.seq = d.written = 0
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
            d.seq += 1
            return rebuilt, self._tab_view(d, rebuilt)
        try:
            rebuilt, tabs = await self._in_session(d, work)
        except DocumentLocked:
            if d.lost:
                await self._lost(d)
            raise
        self._arm_burst(d)
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
            d.seq += 1
            d.refresh_tab_ids()
            return out, self._tab_view(d)
        try:
            out, tabs = await self._in_session(d, run)
        except DocumentLocked:
            if d.lost:
                await self._lost(d)
            raise
        self._arm_burst(d)
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

    async def _refs(self, d, tab, body):
        """POST /documents/<id>/tabs/<tab>/refs {"picks": [...]}: 200 {"document", "tab", "revision", "items": [...]}.
        See "Refs" in the module docstring. A query, so it is allowed on a read-only Document and never touches
        the lock, the revision or the dirty flag."""
        from . import picking
        extra = sorted(str(k) for k in body if k != "picks")
        if extra:
            return self._fail(400, f"{extra[0][:40]}: unknown field")
        try:
            picks = picking.parse_picks(body.get("picks"))
        except ValueError as e:
            return self._fail(400, str(e))
        items, revision = await self._in_session(d, lambda: (d.session.refs(tab, picks), d.revisions.get(tab)))
        return self._json(200, {"document": d.id, "tab": tab, "revision": revision, "items": items})

    # ------------------------------------------------------------------ feature dialogs (I5.3)
    def _dialog_client(self, request):
        client = request.headers.get("X-Client-Id")
        if client is None:
            raise self.session_error("X-Client-Id is required: a dialog belongs to a client")
        return self._edit_meta(client, None)[0]

    def _client_dialogs(self, client, make=False):
        """The client's {dialog id: (Document id, FeatureDialog)} table in its registry entry, or None. `make`: the
        entry is created (and left to expire like any other) when the client has no socket yet."""
        entry = self.clients.get(client)
        if entry is None and make:
            self.clients.attach(client)
            self.clients.detach(client)                       # no socket: the expiry timer starts now
            entry = self.clients.get(client)
        return None if entry is None else entry.data.setdefault("dialogs", {})

    def drop_dialogs(self, doc_id):
        """Forget every client's dialogs on the Document `doc_id` (it closed)."""
        for client in self.clients.ids():
            table = (self.clients.get(client).data or {}).get("dialogs") or {}
            for did in [k for k, (doc, _) in table.items() if doc == doc_id]:
                del table[did]

    async def _dialog(self, d, tab, verb, body, request):
        """The dialog routes; see "Dialogs" in the module docstring."""
        if verb == "types":
            return await self._feature_types(d, tab, request)
        client = self._dialog_client(request)
        match = _DIALOG_PATH.match(request.raw_path.split("?", 1)[0])
        allowed = {"update": ("values",), "new": ("type", "picks")}.get(verb, ())
        extra = sorted(str(k) for k in (body or {}) if k not in allowed)
        if extra:
            return self._fail(400, f"{extra[0][:40]}: unknown field")
        if verb == "open":
            return await self._dialog_open(d, tab, match.group(3), client)
        if verb == "new":
            return await self._dialog_open(d, tab, None, client, body)
        table = self._client_dialogs(client)
        hit = None if table is None else table.get(match.group(6))
        if hit is None or hit[0] != d.id or hit[1].tab != tab:
            return self._fail(404, "not found")                  # another client's dialog is not found either
        dialog, did = hit[1], match.group(6)
        if verb == "preview":
            got = dialog.preview
            if got is None or got["data"] is None:
                return self._fail(404, "no preview")
            return self._send(200, got["data"], "application/octet-stream", [("X-Preview", str(got["n"]))])
        if verb == "cancel":
            table.pop(did, None)
            dialog.preview = None                                # the preview goes with the dialog
            return self._json(200, {"cancelled": True})
        if verb == "update":
            def work():
                parsed = dialog.check(body.get("values"))
                errors, active = dialog.apply(parsed)
                try:
                    n, fut = d.session.preview_dialog(dialog)
                except self.session_error as e:                  # a preview that cannot be asked for: say why
                    n, fut = None, e
                return errors, active, self._dialog_view(dialog), n, fut
            errors, active, view, n, fut = await self._in_session(d, work)
            self._spawn(self._deliver_preview(d, tab, did, dialog, client, n, fut))
            return self._json(200, {**view, "active": active, "errors": errors, "preview": n})
        feature = await self._in_session(d, dialog.result)       # accept
        edit = {"op": "add_feature" if dialog.is_new else "replace_feature", "feature": feature}   # no index: at the bar
        rebuilt = await self._edit(d, tab, edit, client, None)
        table.pop(did, None)
        dialog.preview = None
        return self._json(200, {"rebuilt": rebuilt, "revision": d.revisions.get(tab)})

    async def _deliver_preview(self, d, tab, did, dialog, client, n, fut):
        """Wait for preview `n` of a dialog, keep it on the dialog and tell the client that owns the dialog, on its
        own sockets only. A preview a newer one superseded is dropped, and so is any preview of a dialog that has
        been accepted or cancelled (or whose Document closed) by the time it is ready."""
        from .engine import wire
        if isinstance(fut, Exception):
            got = {"preview": n, "ok": False, "outputs": None, "error": f"{dialog.feature.get('name')}: {fut}"}
        else:
            try:
                got = await asyncio.wrap_future(fut)
            except Exception as e:                               # noqa: BLE001  (a worker that stopped, ...)
                got = {"preview": n, "ok": False, "outputs": None, "error": f"{dialog.feature.get('name')}: {e}"}
        if got.get("superseded") or not self._dialog_alive(client, did, dialog, d):
            return
        data = None
        if got.get("outputs") is not None:
            data = await self._off_loop(wire.to_wire, got["outputs"])
        if not self._dialog_alive(client, did, dialog, d):
            return
        if dialog.preview is not None and n is not None and dialog.preview["n"] is not None \
                and dialog.preview["n"] > n:
            return                                               # an older answer after a newer one: not shown
        error = got.get("error")
        dialog.preview = {"n": n, "data": data, "error": error}
        message = {"type": "preview", "document": d.id, "tab": tab, "dialog": did, "feature": dialog.fid,
                   "preview": n, "revision": d.revisions.get(tab), "ok": bool(got.get("ok")),
                   "outputs": data is not None, "error": error}
        text = json.dumps(message)
        for ws, owner in list(self._sockets.items()):
            if owner == client and d.id in self._subs.get(ws, ()):
                try:
                    await asyncio.wait_for(ws.send_str(text), WS_SEND_TIMEOUT)
                except Exception:                                # noqa: BLE001
                    await ws.close()

    def _dialog_alive(self, client, did, dialog, d):
        """Is `dialog` still open as `did` of `client` on the open Document `d`?"""
        table = self._client_dialogs(client)
        hit = None if table is None else table.get(did)
        return hit is not None and hit[1] is dialog and self.docs.get(d.id) is d

    @staticmethod
    def _dialog_view(dialog):
        return {"description": dialog.description(), "values": dialog.values(), "fields": dialog.fields()}

    async def _dialog_open(self, d, tab, fid, client, body=None):
        import secrets
        if fid is None:                                          # create mode (I6.1): validate before taking a slot
            ftype, picks = body.get("type"), body.get("picks", [])
            if not isinstance(ftype, str) or not 1 <= len(ftype) <= 64:
                return self._fail(400, "type: must be a feature type (a string)")
            if not isinstance(picks, list):
                return self._fail(400, "picks: must be a list")
            if picks:
                from . import picking
                try:
                    picking.parse_picks([dict(p, want=p.get("want", ["element"])) if isinstance(p, dict) else p
                                         for p in picks])
                except ValueError as e:
                    return self._fail(400, str(e))
        table = self._client_dialogs(client, make=True)
        if len(table) >= MAX_DIALOGS:
            return self._fail(409, f"a client may hold at most {MAX_DIALOGS} open dialogs")
        if sum(len((self.clients.get(c).data.get("dialogs") or {})) for c in self.clients.ids()) >= MAX_DIALOGS_TOTAL:
            return self._fail(409, "too many open dialogs")
        def work():
            dialog = d.session.open_dialog(tab, fid) if fid is not None else d.session.start_dialog(tab, ftype, picks)
            return dialog, self._dialog_view(dialog), d.revisions.get(tab)
        dialog, view, revision = await self._in_session(d, work)
        did = secrets.token_hex(8)
        table[did] = (d.id, dialog)
        return self._json(201, {"dialog": did, "document": d.id, "tab": tab, "feature": fid, "type": dialog.type,
                                "name": dialog.feature.get("name"), "revision": revision, **view, "active": None})

    async def _feature_types(self, d, tab, request):
        """GET .../tabs/<tab>/features/types[?picks=<JSON list of picks>]: 200 {"document", "tab", "revision",
        "types": [{"type", "label", "standard", "can_start"}]}. A query: no X-Client-Id, allowed read-only."""
        from . import picking
        query = request.raw_path.split("?", 1)[1] if "?" in request.raw_path else ""
        args = request.query
        if set(args) - {"picks"} or len(query) > MAX_PICKS_QUERY:
            return self._fail(400, "only ?picks=<JSON>, at most %d characters, is accepted" % MAX_PICKS_QUERY)
        picks = []
        if "picks" in args:
            try:
                picks = json.loads(args["picks"])
                if picks != []:                                  # [] is no selection
                    picking.parse_picks([dict(p, want=p.get("want", ["element"])) if isinstance(p, dict) else p
                                         for p in picks] if isinstance(picks, list) else picks)
            except (ValueError, RecursionError) as e:
                return self._fail(400, f"picks: {e}"[:200])
        def work():
            return d.session.startable_types(tab, picks), d.revisions.get(tab)
        can, revision = await self._in_session(d, work)
        from . import dialog_schema as DSC
        from .features import FEATURE_LABELS
        custom = set(DSC.custom_feature_types())
        types = [{"type": t, "label": FEATURE_LABELS.get(t) or t.replace("_", " ").capitalize(), "standard": t not in custom,
                  "can_start": bool(can.get(t))} for t in DSC.startable_feature_types()]
        return self._json(200, {"document": d.id, "tab": tab, "revision": revision, "types": types})

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
            d.seq += 1
            if self.data_dir is not None and d.file_lock is not None:
                AS.remove(self.data_dir, d.file_lock.instance)         # saved: nothing left to recover
            d.written = d.seq
            return saved, self._tab_view(d)
        try:
            saved, tabs = await self._in_session(d, work)
        except DocumentLocked:
            if d.lost:
                await self._lost(d)
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
            left = self._subs.pop(ws, None) or ()
            self.clients.detach(client)
            for doc_id in left:
                if doc_id in self.docs:
                    self._arm_idle(self.docs[doc_id])
            if not ws.closed:
                await ws.close()
        return ws

    async def _subscribe(self, ws, client, doc_id):
        """Subscribe `ws` to open Document `doc_id` and send its hello, or send an error when it is not open."""
        d = self.docs.get(doc_id) if isinstance(doc_id, str) and _DOC_ID.fullmatch(doc_id) else None
        if d is None:
            if isinstance(doc_id, str) and doc_id in self.closed_reasons:       # a client that comes back after the idle close
                await self._push(ws, {"type": "closed", "document": doc_id, "reason": self.closed_reasons[doc_id]})
                return
            await self._push(ws, {"type": "error", "document": doc_id if isinstance(doc_id, str) else None,
                                  "message": "the Document is not open"})
            return
        self._subs[ws].add(d.id)
        self._disarm_idle(d)
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
        except (ValueError, RecursionError):                     # a message nested past the parser's depth
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
            if doc_id in self.docs:
                self._arm_idle(self.docs[doc_id])
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
        elif isinstance(kind, str) and kind.startswith("sketch."):
            await self._sketch_message(ws, client, msg, doc_id)          # I7.3, see "Sketch sessions"
        else:
            await self._push(ws, {"type": "error", "message": "unknown message"})

    # ------------------------------------------------------------------ sketch sessions (I7.3)
    def _sketch_expiry(self, then):
        """The registry's on_expire: an expired client's unfinished sketch is kept for the autosave (on the event
        loop, then off it), then `then(entry)` (the caller's own callback) runs."""
        def expired(entry):
            sk = (entry.data or {}).pop("sketch", None)
            if sk is not None:
                self._spawn(self._keep_expired(sk))
            if then is not None:
                then(entry)
        return expired

    async def _keep_expired(self, sk):
        d = self.docs.get(sk.document)
        if d is None:
            return                                               # its Document closed, and kept it then
        kept = [self._kept_of(sk)]
        await self._in_session(d, lambda: self._add_kept(d, kept))
        await self._autosave_one(d)

    @staticmethod
    def _kept_of(sk):
        """What the autosave keeps of an unfinished sketch: its feature dict with the session's params."""
        with sk.lock:
            stored = sk.stored
            if stored is not None:
                f = json.loads(json.dumps(stored))
                f["params"] = {**(f.get("params") or {}), **sk.params()}
            else:
                f = {"id": None, "type": "sketch", "name": sk.name, "suppressed": False, "params": sk.params()}
        return {"tab": sk.tab, "feature": f}

    def _take_sketches(self, d):
        """Take every client's sketch on Document `d` out of the registry; what the autosave keeps of them. Event
        loop thread (or after the loop stopped)."""
        kept = []
        for client in self.clients.ids():
            data = self.clients.get(client).data
            sk = data.get("sketch")
            if sk is not None and sk.document == d.id:
                del data["sketch"]
                kept.append(self._kept_of(sk))
        return kept

    @staticmethod
    def _add_kept(d, kept):
        """Session lock held. Add `kept` to the sketches every later autosave of `d` writes into its tabs."""
        if kept:
            d.kept_sketches = list(getattr(d, "kept_sketches", None) or ()) + kept
            d.seq += 1

    async def _keep_sketches(self, d):
        """`d` is closing: its unfinished sketches go to the autosave its close writes."""
        kept = self._take_sketches(d)
        if kept:
            await self._in_session(d, lambda: self._add_kept(d, kept))

    async def _sketch_message(self, ws, client, msg, doc_id):
        """One `sketch.*` message; the answer goes to `ws` only. Every failure is an `error` message naming the
        request (`for`), never a closed socket."""
        from .sketch_session import SketchError
        kind = msg["type"]
        ident = msg.get("id")
        if not (ident is None or (isinstance(ident, int) and not isinstance(ident, bool) and abs(ident) < 2 ** 53)
                or (isinstance(ident, str) and len(ident) <= 64)):
            await self._push(ws, {"type": "error", "id": None, "for": kind[:40],
                                  "message": "id: must be a string of at most 64 characters or an integer"})
            return
        try:
            out = await self._sketch_do(client, kind, msg, doc_id)
        except DocumentLocked as e:
            await self._push(ws, {"type": "error", "id": ident, "for": kind, "document": e.document,
                                  "message": str(e), "locked": True, "holder": e.holder})
        except (self.session_error, SketchError) as e:
            await self._push(ws, {"type": "error", "id": ident, "for": kind[:40], "message": str(e)})
        except Exception as e:                                   # noqa: BLE001
            _log(f"sketch message {_clean(kind, 40)}: {type(e).__name__}: {_clean(str(e), 300)}")
            await self._push(ws, {"type": "error", "id": ident, "for": kind[:40], "message": "internal error"})
        else:
            await self._push(ws, {**out, "id": ident})

    MAX_SKETCHES = 64                                            # sketch sessions open on this server at once

    _SKETCH_FIELDS = {"sketch.open": {"tab", "feature", "plane"},
                      "sketch.add": {"shape", "picks", "construction", "radius", "sweep", "constraints"},
                      "sketch.delete": {"ids"}, "sketch.construction": {"ids", "value"},
                      "sketch.undo": set(), "sketch.redo": set(), "sketch.state": set(), "sketch.commit": set(),
                      "sketch.cancel": set()}

    async def _sketch_do(self, client, kind, msg, doc_id):
        from .sketch_session import SketchError
        fields = self._SKETCH_FIELDS.get(kind)
        if fields is None:
            raise SketchError(f"type: unknown sketch message (one of {', '.join(self._SKETCH_FIELDS)})")
        extra = sorted(str(k) for k in msg if k not in fields | {"type", "id", "document"})
        if extra:
            raise SketchError(f"{extra[0][:40]}: unknown field")
        entry = self.clients.get(client)
        if entry is None:                                        # the socket attached it; gone only after it closed
            raise SketchError("this client has no registry entry")
        if kind == "sketch.open":
            return await self._sketch_open(entry, msg, doc_id)
        sk = entry.data.get("sketch")
        if sk is None:
            raise SketchError("no sketch is open for this client")
        if "document" in msg and msg["document"] != sk.document:
            raise SketchError("document: the open sketch is on another Document")
        body = {k: v for k, v in msg.items() if k in fields}
        if kind == "sketch.state":
            return {"type": "sketch.state", **await self._off_loop(self._sketch_full, sk)}
        if kind == "sketch.cancel":
            if entry.data.get("sketch") is sk:
                del entry.data["sketch"]
            return {"type": "sketch.cancelled", "document": sk.document, "tab": sk.tab, "feature": sk.feature}
        if kind == "sketch.commit":
            return await self._sketch_commit(entry, sk, client)

        def step():
            with sk.lock:
                if sk.committing:
                    raise SketchError("the sketch is being committed")
                made = None
                if kind == "sketch.add":
                    made = sk.add(body)
                elif kind == "sketch.delete":
                    sk.delete(body)
                elif kind == "sketch.construction":
                    sk.construction(body)
                elif kind == "sketch.undo":
                    sk.undo()
                else:
                    sk.redo()
                out = {"type": "sketch.changed", **sk.view()}
                if made is not None:
                    out["made"] = made
                return out
        return await self._off_loop(step)

    @staticmethod
    def _sketch_full(sk):
        with sk.lock:
            return {**sk.view(), "frame": sk.frame, "edges": sk.edges}

    @classmethod
    def _json_plain(cls, x):
        """`x` with numpy arrays and numbers turned into lists and Python numbers, for a JSON message."""
        if isinstance(x, dict):
            return {str(k): cls._json_plain(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [cls._json_plain(v) for v in x]
        if hasattr(x, "tolist"):
            return cls._json_plain(x.tolist())
        return x

    @staticmethod
    def _plane_ref(value):
        """A plane reference from a message, checked for shape (the engine checks that it finds a plane)."""
        from .sketch_session import SketchError
        if not isinstance(value, dict) or len(json.dumps(value)) > 65536:
            raise SketchError("plane: must be a plane reference (an object)")
        kind = value.get("kind")
        if kind == "std":
            if set(value) != {"kind", "name"} or value["name"] not in ("Top", "Front", "Right"):
                raise SketchError("plane: a standard plane is {\"kind\": \"std\", \"name\": Top, Front or Right}")
        elif kind == "plane":
            if set(value) != {"kind", "fid"} or not isinstance(value["fid"], str) or not 1 <= len(value["fid"]) <= 64:
                raise SketchError("plane: a plane feature is {\"kind\": \"plane\", \"fid\": id}")
        elif kind == "face":
            if not isinstance(value.get("face"), dict) or set(value) - {"kind", "face", "origin", "xdir"}:
                raise SketchError("plane: a face is {\"kind\": \"face\", \"face\": a face reference}")
        else:
            raise SketchError("plane.kind: must be std, plane or face")
        return value

    async def _sketch_open(self, entry, msg, doc_id):
        from .sketch_session import SketchError, SketchSession
        if entry.data.get("sketch") is not None:
            raise SketchError("a sketch is already open for this client; commit or cancel it first")
        if sum(1 for c in self.clients.ids() if self.clients.get(c).data.get("sketch") is not None) \
                >= self.MAX_SKETCHES:
            raise SketchError("too many sketches are open")
        d = self.docs.get(doc_id) if isinstance(doc_id, str) else None
        if d is None:
            raise SketchError("document: the message must name an open Document")
        tab = msg.get("tab")
        if not isinstance(tab, str) or tab not in d.tab_ids:
            raise SketchError("tab: unknown tab")
        fid, plane = msg.get("feature"), msg.get("plane")
        if (fid is None) == (plane is None):
            raise SketchError("sketch.open takes either feature (an existing sketch) or plane (a new one)")
        if fid is not None and (not isinstance(fid, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", fid) is None):
            raise SketchError("feature: must be a feature id")
        if plane is not None:
            plane = self._plane_ref(plane)

        def work():
            if fid is not None:
                stored = d.session.sketch_feature(tab, fid)
                frame = d.session.sketch_frame(tab, fid)
                params = stored.get("params") or {}
                sk = SketchSession(d.id, tab, fid, params.get("plane"), params.get("sketch"), name=stored.get("name"))
                sk.stored = stored
            else:
                names = set(d.session.feature_names(tab).values())
                frame = d.session.sketch_frame(tab, plane)
                n = 1
                while f"Sketch {n}" in names:
                    n += 1
                sk = SketchSession(d.id, tab, None, plane, None, name=f"Sketch {n}")
            sk.frame = {k: [float(x) for x in frame[k]] for k in ("origin", "xdir", "ydir", "normal")}
            sk.edges = self._json_plain(frame["edges"])
            return sk, d.revisions.get(tab)
        sk, revision = await self._in_session(d, work)
        if entry.data.get("sketch") is not None:                 # another socket of this client opened one meanwhile
            raise SketchError("a sketch is already open for this client; commit or cancel it first")
        entry.data["sketch"] = sk
        return {"type": "sketch.opened", **await self._off_loop(self._sketch_full, sk), "revision": revision}

    async def _sketch_commit(self, entry, sk, client):
        from .sketch_session import SketchError
        d = self.docs.get(sk.document)
        if d is None:
            raise SketchError("the sketch's Document is not open")
        with sk.lock:
            if sk.committing:
                raise SketchError("the sketch is being committed")
            sk.committing = True
        try:
            feature = self._kept_of(sk)["feature"]
            if sk.feature is None:
                def names():
                    have = d.session.feature_names(sk.tab)
                    if feature["name"] in have.values():          # a sketch made since took the name
                        n = 1
                        while f"Sketch {n}" in have.values():
                            n += 1
                        feature["name"] = f"Sketch {n}"
                    return have
                before = await self._in_session(d, names)
                feature.pop("id", None)
                rebuilt = await self._edit(d, sk.tab, {"op": "add_feature", "feature": feature}, client, None)
                after = await self._in_session(d, lambda: d.session.feature_names(sk.tab))
                made = [k for k in after if k not in before]
                fid = next((k for k in made if after[k] == feature["name"]), made[0] if made else None)
            else:
                rebuilt = await self._edit(d, sk.tab, {"op": "replace_feature", "feature": feature}, client, None)
                fid = sk.feature
        finally:
            sk.committing = False
        if entry.data.get("sketch") is sk:
            del entry.data["sketch"]
        return {"type": "sketch.committed", "document": d.id, "tab": sk.tab, "feature": fid, "rebuilt": rebuilt,
                "revision": d.revisions.get(sk.tab)}


def make_server(session=None, port=0, allow_origins=(), client_ttl=CLIENT_TTL, client_expired=None, *,
                registry=None, root=None, folder=None, lock_program=None, data_dir=None,
                autosave_interval=AUTOSAVE_INTERVAL, autosave_burst=AUTOSAVE_BURST, lock_timeout=doc_lock.DEFAULT_TIMEOUT):
    """A server on 127.0.0.1:`port` (bound, not yet serving). Over `session`: a single Document (the old routes
    alias it); `folder` and `lock_program` (the CLI passes "browser") make it take the Document lock, so an
    in-process server given neither has no lock file. Over `registry` and `root` instead: a Documents root, where
    Documents are created under `root` and opened by id. Callers run `serve_forever` (it blocks) and, at the end,
    `shutdown`, `server_close` and close the session; `close_all` does the last three. `data_dir` is where the autosave
    lives (`<data_dir>/autosave-server`); without it there is none. `autosave_interval` and `autosave_burst` are the
    autosave timers in seconds, `lock_timeout` the age at which a heartbeat is stale; tests drive `autosave_now()`,
    `expire_idle_now()` and `refresh_locks_now()` instead of waiting for them."""
    return _Server(session, port, allow_origins, client_ttl, client_expired, registry, root, folder, lock_program,
                   data_dir, autosave_interval, autosave_burst, lock_timeout)


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
    ap.add_argument("--autosave-interval", type=float, default=AUTOSAVE_INTERVAL, metavar="SECONDS",
                    help="seconds between autosaves of an open Document that changed (default 30)")
    ap.add_argument("--lock-timeout", type=float, default=doc_lock.DEFAULT_TIMEOUT, metavar="SECONDS",
                    help="age of a Document lock's heartbeat after which it is stale (default 60)")
    args = ap.parse_args(argv)
    if not args.autosave_interval > 0 or not args.lock_timeout > 0:
        print("freeshape serve: --autosave-interval and --lock-timeout must be more than 0.", file=sys.stderr)
        return 2
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
    data = args.data_dir or doc_registry.data_dir()
    if os.path.isfile(os.path.join(args.document, doc_registry.MANIFEST_NAME)):
        from .session import DocumentSession, SessionError
        try:
            session = DocumentSession(args.document)
        except SessionError as e:
            print(f"freeshape serve: cannot open the Document: {e}", file=sys.stderr)
            return 1
        extra = {"folder": os.path.realpath(args.document), "lock_program": LOCK_PROGRAM}
    else:
        registry = doc_registry.DocRegistry(os.path.join(data, doc_registry.REGISTRY_NAME))
        problem = registry_mismatch(registry)
        if problem:
            print(f"freeshape serve: {problem}", file=sys.stderr)
            return 1
        extra = {"registry": registry, "root": args.document}
    extra.update(data_dir=data, autosave_interval=args.autosave_interval, lock_timeout=args.lock_timeout)
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
