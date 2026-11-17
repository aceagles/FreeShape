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
    POST /documents/<id>/tabs/<tab>/edit       body: a contract edit dict; reply {"rebuilt": [ids]}; an Assembly's
                                move_to also replies "placements": {instance id: 4x4} of the instances it changed
    POST /documents/<id>/tabs/<tab>/refs       body: {"picks": [pick, ...]}; reply {"document", "tab", "revision",
                                "items": [item, ...]}, one item per pick, in order (I4.3; see "Refs" below)
    POST /documents/<id>/tabs/<tab>/import[?name=<file>&new_tab=1]  body: the STEP or BREP bytes (application/octet-stream,
                                up to MAX_UPLOAD, this route only); one add_feature of an import feature (I11.1); reply
                                {"document", "tab", "feature", "parts", "rebuilt", "tabs"}; 415 not STEP/BREP, 413, 423;
                                or the body {"root", "entry"} (application/json): the entry of a file root (I11.2)
    GET  /files                                the file roots: {"roots": [{"name", "files", "max_upload"}],
                                "export_roots": [{"name", "max_bytes"}]} (I11.2, I11.13; see "File roots" below)
    GET  /files/<root>[?dir=<folder>&q=<words>&offset=&limit=]  one folder of the root, or the search results
    POST /files/<root>/rescan                  rebuild the root's index; reply {"root", "files"}
    GET  /documents/<id>/archive[?save=1]      the Document's saved folder as one .fsdoc download (I11.5; see "Archive"
                                below); 409 unsaved, 423, 404, 422
    POST /documents/archive?name=<file name>   body: the .fsdoc bytes (application/octet-stream, up to MAX_ARCHIVE, this
                                route and import only); a Documents root only; reply 201 {"id", "name"}; 422 refused,
                                413, 404 on a single-Document server
    GET  /documents/<id>/tabs/<tab>/export?format=step|stl[&parts=<ids>&preset=&defl=&angle=&ascii=]  the tab as a
                                STEP or STL download (I11.3; see "Export" below)
    POST /documents/<id>/tabs/<tab>/export  body: {"root", "format", ...}; saves the same export into an export root
                                (I11.13; see "Export roots" below); reply 201 {"root", "name", "bytes", "replaced",
                                "skipped"}; 404 unknown root, 409 name taken, 413, 507
    GET  /documents/<id>/tabs/<tab>/assembly/types   mate and relation types of an Assembly tab (I9.5; see "Assembly types")
    POST /documents/<id>/tabs/<tab>/features/<fid>/dialog        open a feature dialog (I5.3; see "Dialogs" below)
    POST /documents/<id>/tabs/<tab>/features/dialog              open a dialog for a NEW feature (I6.1; "Dialogs")
    GET  /documents/<id>/tabs/<tab>/features/types[?picks=<JSON>]  feature types, label, standard, can_start (I6.1)
    POST /documents/<id>/tabs/<tab>/dialog/<did>/update|accept|cancel
    GET  /documents/<id>/tabs/<tab>/dialog/<did>/preview         the dialog's newest preview (I5.4), wire format
    GET  /documents/<id>/tabs/<tab>/dialog/<did>/base            the model the dialog picks against (F3), wire format
    POST /documents/<id>/tabs/<tab>/mates/dialog                 open a mate dialog on an Assembly (I10.5; see
                                "Mate dialogs" below)
    POST /documents/<id>/tabs/<tab>/mates/dialog/<did>/update|accept|cancel
    POST /documents/<id>/save                  body: {} ; reply {"saved": [ids]}
    GET  /ws?client=<id>[&document=<id>]       the live channel (WebSocket, JSON text messages; `_Server._socket`)
  On a server over one Document folder the old un-prefixed routes stay as aliases for that Document:
    GET /tabs, GET /tabs/<id>/outputs, GET /tabs/<id>/mesh.json, POST /tabs/<id>/edit, POST /save
  An edit or save on a Document this server does not hold the lock of answers 423 {"error", "document", "holder"}.
  A Document id is a key into the open table and the registry; it is never joined to a path. A new Document's
  folder name is made from its name by `folder_name_for`.

Export (I11.3). GET .../export writes the tab as it is now (unsaved edits included) with the desktop's own
`PartStudioDoc.export` / `AssemblyDoc.export` and streams the file: Content-Type model/step or model/stl,
Content-Disposition attachment with the file name `<tab name>.step` or `.stl` (ASCII `filename` and an RFC 5987
`filename*`; `freeshape.export_options.export_file_name`), X-FreeShape-Skipped the percent-encoded names of surface bodies
left out of an STL, comma-separated (empty when none). Query: `format` (required), `parts` (comma-separated part ids, a Part
Studio only; absent is every part), and for an STL `preset` (Coarse, Medium, Fine or Custom; default Fine),
`defl` / `angle` (a Custom resolution, see `freeshape.export_options`) and `ascii` (1 or 0). A Drawing or Variable
Studio tab, a format that is not step or stl, an unknown or repeated parameter, an unknown part id and an option out of
range are a 400. A read-only Document exports too (nothing in it changes). The making of an export is one method,
`_Server._make_export`, which a route that saves the bytes elsewhere calls as well.

Archive (I11.5; freeshape/doc_archive.py, ADR 0017). A Document travels as one `.fsdoc` file.
  GET  /documents/<id>/archive[?save=1]  the Document as it is saved in its folder (never the unsaved state of the open
                         session), Content-Type application/octet-stream (so Safari does not unzip it), Content-Disposition
                         attachment with `<Document name>.fsdoc` (built as the export's is; the name is the registry's, made
                         safe by `export_options.export_file_name`). Works on an open or a closed Document, on a Documents
                         root and on a single-Document server. The only query key is `save` (1 or 0), given once. When the
                         Document is open here with unsaved edits the reply is 409 {"error": "unsaved", "document"} and
                         nothing is saved; with save=1 the server saves first by the route of POST .../save (the lock holder's
                         route: `save=1` is refused with 423 {"error", "document", "holder"} when this server no longer
                         holds the Document's lock, even when nothing is unsaved) and then packs. The archive is packed under
                         the Document's session lock, so a save cannot change the folder while it is read. 404 for an id
                         nothing knows or whose folder is gone; 422 {"error": reason} when `doc_archive.pack` refuses the
                         folder (a tab file name with a colon or a leading dot, a blob that is not its key, ...).
  POST /documents/archive?name=<name>  a Documents-root server only (404 on a single-Document server, before the body is
                         read). Body application/octet-stream, Content-Length required (411), over MAX_ARCHIVE (256 MiB) is
                         413 from the header, before any of it is read; the whole body must arrive within ARCHIVE_DEADLINE
                         (300 s, else 408). Query: `name` (required, once; a trailing `.fsdoc` is dropped) names the folder
                         by `folder_name_for` (400 for a name it refuses; `<name> 2`, `<name> 3` on a clash) and nothing
                         else is accepted. `doc_archive.unpack` runs off the event loop under the server's create lock,
                         into the Documents root, with a fresh Document id, registered as one this app made. 201 {"id",
                         "name": the Document's name from its manifest}; the Document then shows in GET /documents. 422
                         {"error": reason} for an archive `unpack` refuses (not a zip, zip-slip, too many or too large
                         entries, a bad manifest, ...); a refused archive leaves nothing in the root or the registry.
                         507 when the disk is full. 415 for another Content-Type. No reply names a server path.

Assembly types (I9.5). GET .../tabs/<tab>/assembly/types answers what the Assembly toolbar draws, read from
freeshape/assembly.py: 200 {"document", "tab", "mates": [{"id", "label", "help", "freedoms": [name, ...]}],
"relations": [{"id", "label", "help"}]}. The mates are MATE_TYPES in its order (`freedoms` is MATE_VALUE_DOFS: the
names of the Mate values the type has, translations tx, ty, tz in mm and rotations rz in degrees, [] for fastened
and ball); the relations are RELATION_TYPES in its order. A query: no X-Client-Id, allowed read-only. A tab that is
not an Assembly is a 400 naming its kind.

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
  The body may also carry "dialog": did (F3), the id of a feature dialog open on this tab, with X-Client-Id naming
  the client that owns it (a 400 without the header; a 404 for a dialog that is not that client's, not on this tab
  or gone, as the dialog routes answer). The picks then read the model that dialog edits on, the one GET
  .../dialog/<did>/base sends (the Part Studio before the edited feature; for a new feature, at the rollback bar),
  instead of the last build: the desktop's dialog picks against that model too, and an element the feature itself
  made (a fillet's own edges) is not there to pick. Indices, items and codes are as below.
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
  base    (F3) GET .../dialog/<did>/base  (X-Client-Id as for every dialog route) 200 the StudioOutputs of the model
          the dialog picks against, in the engine wire format (the GET .../outputs decoder reads it), with header
          X-Revision: the committed revision it was built from. It is the Part Studio built up to just before the
          edited feature (for a new feature: up to the rollback bar), contract.Engine.base_outputs; its `errors` are
          empty and its feature list is the committed one. A client draws it under the preview while the dialog is
          open and resolves picks on it (refs with "dialog": did), so what the pen hits is what the feature can use.
          It is built on each request, from the committed model; fetch it again after a `changed` push.

Mate dialogs (I10.5, freeshape/mate_session.py, the desktop's MateDialog). A mate dialog adds a mate to an Assembly
tab or edits one. X-Client-Id is required, ownership, expiry and the limits (counted together with the feature
dialogs) are as for "Dialogs". The dialog itself (a MateSession) lives in the Assembly tab's engine worker
(contract.Engine.mate_dialog_open / _update / _result / _close); a worker that is restarted loses it, and its next
update is a 400. Every reply but cancel's is a preview: the session previews the mate as the desktop does
(place_for_mate, then solve) on the committed Assembly and puts it back, so nothing changes until accept (no
revision, undo step or dirty flag; a read-only Document may open and update one). Bodies take no unknown field.
  open    POST .../tabs/<tab>/mates/dialog  body {"type": a mate type ("fastened", "revolute", "slider", "cylindrical",
          "planar", "ball", "pin_slot", "parallel", ...: assembly.MATE_TYPES), "picks": [side pick, at most 2]
          (optional; the first fills a, the next on another instance b; one that cannot be used is dropped)} for a
          new mate, or {"mate": mate id} to edit a stored one. 201 {"dialog": did, "document", "tab", "revision",
          ...the view below}. Fewer than two parts in the Assembly is a 400 "Insert at least two parts before adding a
          mate"; so is an unknown type or mate, or a tab that is not an Assembly.
  view    {"mate": the mate id (a new mate's is taken at open), "new": bool, "type", "name", "flip": bool,
           "offset": mm, "angle": degrees, "a": side or null, "b": side or null,
           "placements": {instance id: 4x4 nested lists}, every instance as previewed (a mate with an empty side
                         previews as the Assembly solves without it),
           "values": [{"dof": "rz" | "tz" | "tx" | "ty", "label", "value", "unit": "°" | "mm"}], the mate's Mate
                     values as previewed (empty for fastened and ball),
           "error": null, or the message of a pick the update refused, else the previewed mate's solver error,
           "dof": the previewed Assembly's degrees of freedom}
          side = {"instance", "kind": "face" | "edge" | "connector" | "named" | ..., "index": the 1-based element
                  index (null for a connector), "connector": the connector's id (null for a face or edge),
                  "what": "circular edge", "flat face", ..., "world": {"o", "z", "x"} the connector frame placed by
                  the previewed placement of its instance}
  side pick  {"instance": id, "kind": "face" | "edge", "index": 1-based element index (PartOut.names counts),
          "point": [x, y, z] world click point (optional; picks the end of a round face; ignored for an edge)},
          through AssemblyDoc.connector; or {"instance": id, "connector": id} for the instance's own connector
          (a Part Studio's published mate connector or a Standard part's named one, I10.3), through named_connector.
  update  POST .../mates/dialog/<did>/update  body any of {"type", "name": text, "a": side pick or null (clears it),
          "b": the same, "flip": bool, "offset": number (mm), "angle": number (degrees), "turn90": true (the angle
          goes up 90 degrees, modulo 360, after any "angle")}, applied in that order. 200 {"dialog", "document",
          "tab", "revision", ...the view}. A pick that cannot be used, or a side on the instance the other side is
          on ("Pick the second connector on a different part."), leaves that side as it was and its message is the
          reply's "error"; a new mate whose name is still a default one is renamed with its type. An unknown key or
          a value of the wrong type is a 400 naming it and nothing is applied.
  accept  POST .../mates/dialog/<did>/accept  body {}; sends add_mate (new) or replace_mate (edited; it keeps its
          id), one undo step, closes the dialog, 200 {"rebuilt", "revision", "mate": its id}, and the `changed` push.
          A side still empty is a 400 "Pick a mate connector on each of the two parts first." and the dialog stays
          open; 423 on a read-only Document. Limits are not edited here: an edited mate keeps the limits its type
          still has. Rename, suppress and delete of mates are the edit route's rename, set_suppressed, delete_mates.
  cancel  POST .../mates/dialog/<did>/cancel  body {}; closes it, changes nothing, 200 {"cancelled": true}.

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
                or {"kind": "face", "face", "origin", "xdir"}, which is the refs route's "plane" item for a face sent
                as it is, not wrapped again; a face that is not planar is an error naming it. Refused while this client has a sketch open, and past MAX_SKETCHES.
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
Dragging in a sketch (I7.4; Sketcher.mouse_drag is the model). The sketch stays as it is while a drag lasts; a drag
is one undo step. Other steps, undo, redo and commit are refused while one is open.
  sketch.drag.begin {"type": "sketch.drag.begin", "point": id | "entity": id, "at": [u, v]}  start dragging a point
                (not the origin) or an entity (a circle's radius follows `at`; a line or arc moves by its points) from
                `at`, sketch coordinates. Answer {"type": "sketch.drag.begun"}.
  sketch.drag.move  {"type": "sketch.drag.move", "at": [u, v], "t"?: number}  restore the original, move the items by
                the offset from the start, weight them heavily and solve. `t` is the client's send time. The answer
                {"type": "sketch.drag.moved", "t": echoed, "solve_ms": the server's solve time, "points": {id: [u, v]},
                "radii": {entity id: r}, "held": bool} comes from a solve off the event loop; `held` is true when the
                constraints refuse that position and the original is returned. Latest wins: a move that arrives
                while one is solving replaces any waiting move, and the replaced move gets no answer. The sketch itself
                is unchanged until the end.
  sketch.drag.end   {"type": "sketch.drag.end", "cancel"?: bool}  waits for the waiting move to be solved, then keeps
                the last solved result as one undo step and answers sketch.changed (as the steps above); a drag
                whose last move was held or left everything where it was, or that never moved, adds no step; `cancel` drops the result.
  sketch.constrain {"type": "sketch.constrain", "tool", "picks": [point or entity id, ...]}  apply a constraint tool as
                the desktop does, through freeshape.sketch_ops.constraints_for (I7.5). `tool` is one of coincident,
                horizontal, vertical, parallel, perpendicular, tangent, equal, midpoint, concentric, symmetric, fix,
                normal; picks are ids of the sketch's points and entities (not constraints), in the order clicked
                (symmetric: the axis is the third). A tool that cannot use the picks is an error naming "picks". The
                client never maps picks to constraint types itself. Answer {"type": "sketch.constrained",
                "accepted": bool, "made": [new constraint ids], "conflicts": [new constraint ids the solve could not
                meet or skipped (no equation for those items)], ...the fields of sketch.changed}. accepted false: the sketch,
                its status and the undo stack are exactly as before (no step); accepted true: one undo step. When
                every relation the picks would make is already in the sketch (same kind, same items), nothing changes
                (no step, same revision) and the answer carries "already": true and "kind": the tool, with accepted
                true and made []; when only some exist, the new ones are added and the answer is an ordinary add.
  sketch.unconstrain {"type": "sketch.unconstrain", "constraint": constraint id}  remove that one constraint, one step
                (the field is not "id": that stays the request id); answers sketch.changed.
  sketch.fits   {"type": "sketch.fits", "selection": [point or entity id, ...]}  no change, allowed during a drag;
                answers {"type": "sketch.fitted", "selection", "tools": [tool names that take it, in the order above]}.
Dimensions, trim, fillet and use edge (I7.6). Each answers {"type": "sketch.changed", ...the fields above, "made": {...}}
and is one undo step; a refusal is the error message and changes nothing. A number is a JSON number or the text of an
expression (freeshape.expr: units, 2*10, #variable, evaluated with the Document's variables as the Part Studio has them
when the sketch opened; a bare number in an angle is degrees). An expression that names a #variable is stored on the
dimension as its "expr", as the desktop does.
  sketch.dimension {"type": "sketch.dimension", "picks": [id] or [id, id], "pos"?: [u, v], "kind"?, "value"?}  add a
                dimension as the desktop's Dimension tool does (sketch_ops.dim_spec). The client sends picks, never the
                constraint type: one line gives its length (distance), one circle its diameter, one arc its radius, two
                lines the angle (degrees) or, when parallel, their distance; a point or a circle or arc centre with a
                line, or two points or centres, a distance. Two points far apart in both directions give an hdistance
                or vdistance when `pos` (where the number sits, kept at that offset as the geometry moves) lies beside
                their box; "kind" (distance, hdistance or vdistance; the field is not "type") picks one for two points.
                "value" defaults to the current measure; given, the geometry follows. A dimension the others already
                fix, or that cannot be met with them, is an error naming "picks" or "value". made {"constraint": id,
                "type": distance | hdistance | vdistance | radius | diameter | angle, "value", "expr"?}.
  sketch.dimension.set {"type": "sketch.dimension.set", "constraint": a dimension's id, "value"?, "pos"?: [u, v]}  change
                the value (any dimension: those above and an offset) and / or move the label (one of the two is
                needed). A value the other dimensions refuse (or 0 or less, but for an angle) is an error naming
                "value" and the sketch is as it was. made {"constraint", "value", "expr"?}.
  sketch.fillet {"type": "sketch.fillet", "point": a corner point's id, "radius": number or expression}  round the corner
                where exactly two lines meet (sketch_ops.fillet_corner). made {"arc": id, "constraint": the arc's radius
                dimension, which sketch.dimension.set can change}. A radius too large for the lines, or a point that is
                not such a corner, is an error.
  sketch.trim   {"type": "sketch.trim", "entity": an entity's id, "at": [u, v]}  remove the piece under `at` as the
                desktop's Trim tool (sketch_ops.trim): a line is cut back to its neighbouring crossings with other lines,
                circles and arcs; a circle or arc goes whole. made {"removed": id, "made": [ids of the lines left]}.
  sketch.use_edge {"type": "sketch.use_edge", "pick": {"part", "index"}: an `edges[].pick` of sketch.opened,
                "construction"?: bool (default true)}  bring a model edge in as fixed geometry through the engine's
                project_edge, with its reference stored as `src` so it follows the part (the desktop's Use tool). An
                edge that is neither straight nor circular on the plane is an error. An edge already in the sketch is
                not added again (no step; "construction" given sets the flag of the one there). made {"entity": id
                (a point id for an edge seen end-on), "points": [its point ids], "reused": bool}.
Mirror and patterns (I14.1). The desktop's three copy tools (sketch_ops.mirror, linear_pattern, circular_pattern); each
answers sketch.changed with "made" and is one undo step; nothing is stored but the copies (no mirror or pattern object).
Ids are entity ids (a point id is refused); a count, spacing or angle is a number or an expression (not kept); every
refusal is the error message naming the field, and the sketch, its status and both stacks are as they were.
  sketch.mirror {"type": "sketch.mirror", "ids": [entity id, ...], "line": a line's id that is not in ids,
                "preview"?: bool}  copies linked by one symmetric constraint per point pair naming the line, equal for
                circles; points on the line are shared. made {"entities": [the new ids]}.
  sketch.linear_pattern {"type": "sketch.linear_pattern", "ids", "line": the direction line, "at": [u, v] (the tapped
                point: the copies go towards its side of the line), "count"?: 2 to 500, default 3, "spacing"?: default
                1.25 times the selection's extent along the direction, at least "floor" (0 if left out), zero and
                negative allowed, "floor"?: number, "preview"?}  count - 1 plain copies keeping the shape constraints
                among the copied items. made {"entities", "count", "spacing", "direction": [du, dv]}.
  sketch.circular_pattern {"type": "sketch.circular_pattern", "ids", "centre": [u, v] or {"at", "point"} (that point's
                position is used, not a link), "count"?: 2 to 500, default 6, "angle"?: degrees, default 360, zero and
                negative allowed, "preview"?}  360 divides by the count, any other angle by count - 1. made
                {"entities", "count", "angle", "centre": [u, v]}.
  "preview": true runs the same call on a copy of the sketch and answers {"type": "sketch.previewed", "for": the message
                type, "made": as above, "outlines": [[[u, v], ...], ...]} (Sketch.polylines(eid, 24) of each new entity
                in made.entities order; text gives one per glyph outline); no step, no revision change, no broadcast.
                The same message without preview makes the same entity ids. Refused during a drag like the steps.
Limits: count at most 500, and the copies must fit 5000 points and entities (an error naming "count").
When the client's registry entry expires, or the Document closes or the server stops gracefully, an unfinished sketch is
written into the Document's autosave (I2.6) before the session is dropped: the tab's autosaved data holds the sketch
feature (an existing one with the session's params, a new one added at the rollback bar), the open session is not
changed, and recover brings it back as an unsaved edit. It stays in every later autosave of that Document while it is
open on this server.

Export roots (I11.13). `--export-root NAME=PATH` (repeatable; NAME as a file root's, and different from every file root's)
names a writable folder an export can be saved into, for a program on the same machine that reads it (Bambu Studio);
see freeshape/export_roots.py. PATH must be an existing folder this user can write. Serve refuses to start when a root
is equal to, inside or around the Documents root, a served or registered Document, the data folder, a file root or
another export root, so a saved export can never replace a Document or a library file. An export root is not listed or
browsed (GET /files/<export root> is 404) and a file root is never written to.
  POST .../tabs/<tab>/export  body (application/json, at most MAX_BODY): {"root": an export root's name, "name": file
                         name (optional; the tab's name), "format": "step" | "stl", "parts", "preset", "defl", "angle",
                         "ascii" (as the GET route; "parts" a list of ids), "replace": true | false (default false)}.
                         The export is made by `_make_export`, the GET route's own code, then saved as `<name>.step` or
                         `.stl`, one path component made by `export_options.export_file_name`, written by
                         `ExportRoot.write` off the event loop. 201 {"root", "name", "bytes", "replaced", "skipped": [names
                         of surface bodies left out of an STL]}; 400 as the GET route, and for a body key that is not
                         one of those; 404 for a root that is not an export root; 409 {"error": "exists", "name"} when the
                         name is taken and "replace" is not set, 409 when it is a symlink, a folder or another thing that
                         is not a regular file; 413 for an export over MAX_EXPORT_SAVE; 507 when the folder is full.
                         No reply names a server path. A read-only Document can save an export.

File roots (I11.2). `--file-root NAME=PATH` (repeatable; NAME is 1 to 32 of a-z 0-9 -; PATH an existing folder) names a
read-only folder of STEP files; see freeshape/file_roots.py. Nothing writes into a root, a request value is never joined
to a path (`dir` is looked up in the index's folders, files are reached by opaque entry ids) and symlinks are left out.
  GET /files             {"roots": [{"name", "files", "max_upload"}]}; builds each root's index on first use, off the loop
  GET /files/<root>      {"root", "dir", "q", "dirs": [{"name", "path", "files"}], "entries": [{"id", "name", "file",
                         "path", "size", "description", "tags", "licence", "authors", "url"}], "total", "offset",
                         "limit"}. `dir` is a folder path from a "dirs" item ("" or absent: the root; 404 for any other
                         value); `q` searches by name, tags and path (below `dir` when given; "dirs" is then empty);
                         offset (0 or more) and limit (1 to 500, default 200; more is cut to 500) page "entries".
                         "licence", "authors" and "url" are as the root's manifest.csv wrote them, or null.
  POST /files/<root>/rescan  body {}; rebuilds the index; {"root", "files"}
  POST .../tabs/<tab>/import with {"root", "entry"}: the entry's file is read through the index (409 if it is no longer a
                         regular file inside the root, 413 over MAX_UPLOAD, 404 for an unknown root or entry) and imported
                         as an upload named after the file; `new_tab` works as for an upload.
"""
from __future__ import annotations

import argparse
import asyncio
import errno
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
from . import export_roots as ER
from . import file_roots as FR

BIND_HOST = "127.0.0.1"
MAX_BODY = 1024 * 1024
MAX_EXPORT_SAVE = 256 * 1024 * 1024     # most bytes an export saved into an export root may hold (I11.13)
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
                       r"(?:/(open|close|take-over|save|tabs|recover|discard-autosave|archive)"
                       r"|/tabs/([A-Za-z0-9_-]{1,64})(?:/(outputs|mesh\.json|edit|rename|refs|assembly/types))?)$")
_DOC_VERBS = {"open": "POST", "close": "POST", "take-over": "POST", "save": "POST", "tabs": "GET", "recover": "POST",
              "discard-autosave": "POST", "archive": "GET"}
_DIALOG_PATH = re.compile(r"^/documents/([A-Za-z0-9_-]{1,64})/tabs/([A-Za-z0-9_-]{1,64})"
                          r"(?:/features/(?:([A-Za-z0-9_.-]{1,64})/(dialog)|(dialog|types))"
                          r"|/dialog/([0-9a-f]{16})/(update|accept|cancel|preview|base))$")
_MATE_PATH = re.compile(r"^/documents/([A-Za-z0-9_-]{1,64})/tabs/([A-Za-z0-9_-]{1,64})/mates/dialog"
                        r"(?:/([0-9a-f]{16})/(update|accept|cancel))?$")
MAX_PICKS_QUERY = 8000          # longest query string of GET .../features/types
MAX_DIALOGS = 8                 # open dialogs one client may hold (feature and mate dialogs together)
MATE_UPDATE_KEYS = ("type", "name", "a", "b", "flip", "offset", "angle", "turn90")
MAX_DIALOGS_TOTAL = 256         # and all clients together
_FIXED = {"/health": ("GET",), "/tabs": ("GET",), "/save": ("POST",)}
_EDGE_METHODS = {"outputs": "GET", "mesh.json": "GET", "edit": "POST", "rename": "POST", "refs": "POST",
                 "assembly/types": "GET"}
MAX_TABS = 200                  # tabs one Document may hold through POST /documents/<id>/tabs
_CLIENT_CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
               "manifest-src 'self'; img-src 'self' data: blob:; font-src 'self'; worker-src 'self' blob:; "
               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_CLIENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".json": "application/json; charset=utf-8", ".map": "application/json; charset=utf-8",
                 ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".webp": "image/webp",
                 ".woff2": "font/woff2", ".woff": "font/woff", ".wasm": "application/wasm",
                 ".webmanifest": "application/manifest+json",
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


def _placements(out):
    """{instance id: 4x4 placement as lists} of an Assembly's outputs; {} for any other kind of tab."""
    return {str(i.id): [[float(x) for x in row] for row in i.T] for i in (getattr(out, "instances", None) or ())}


def _same_placement(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for ra, rb in zip(a, b) for x, y in zip(ra, rb))


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


MAX_UPLOAD = 64 * 1024 * 1024   # the body limit of POST .../import alone (the component library's largest file is 52 MB)
UPLOAD_DEADLINE = 120.0         # seconds for the whole body of that upload
_IMPORT_PATH = re.compile(r"^/documents/([A-Za-z0-9_-]{1,64})/tabs/([A-Za-z0-9_-]{1,64})/import$")
MAX_ARCHIVE = 256 * 1024 * 1024     # the body limit of POST /documents/archive (I11.5), the other one besides MAX_UPLOAD
ARCHIVE_DEADLINE = 300.0        # seconds for the whole body of that upload
_FILES_PATH = re.compile(r"^/files(?:/([a-z0-9-]{1,32})(/rescan)?)?\Z")       # I11.2
_EXPORT_PATH = re.compile(r"^/documents/([A-Za-z0-9_-]{1,64})/tabs/([A-Za-z0-9_-]{1,64})/export$")      # I11.3
MAX_EXPORT_PARTS = 1000         # part ids one export may name


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
                 lock_timeout=doc_lock.DEFAULT_TIMEOUT, file_roots=(), export_roots=()):
        from .session import SessionError
        self.session_error = SessionError
        self.file_roots = {}                                     # I11.2: name -> FileRoot, read-only, named at start
        for name, path in file_roots:
            if name in self.file_roots:
                raise ValueError(f"file root {name!r} is named twice")
            self.file_roots[name] = FR.FileRoot(name, path)
        self._check_export_roots(export_roots, file_roots, folder, root, data_dir, registry)
        self.export_roots = {name: ER.ExportRoot(name, path) for name, path in export_roots}     # I11.13: writable
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
        if path == "/documents/archive":
            return "archive.upload", None, None, "POST"      # I11.5: the body is the archive's bytes, not JSON
        m = _MATE_PATH.match(path)
        if m is not None:
            return "mates." + (m.group(4) or "open"), m.group(1), m.group(2), "POST"
        m = _IMPORT_PATH.match(path)
        if m is not None:
            return "import", m.group(1), m.group(2), "POST"  # the body is the file's bytes, not JSON
        m = _EXPORT_PATH.match(path)
        if m is not None:
            return "export", m.group(1), m.group(2), "GET"
        m = _FILES_PATH.match(path)
        if m is not None:
            return "files." + ("rescan" if m.group(2) else "root" if m.group(1) else "list"), None, None, \
                "POST" if m.group(2) else "GET"
        m = _DIALOG_PATH.match(path)
        if m is not None:
            verb = "open" if m.group(4) else ("new" if m.group(5) == "dialog" else m.group(5) or m.group(7))
            return "dialog." + verb, m.group(1), m.group(2), "GET" if verb in ("preview", "types", "base") else "POST"
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
            if route == "export" and method == "POST":             # I11.13: POST saves it into an export root
                route, want = "export.save", "POST"
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
        if method == "POST" and route not in ("import", "archive.upload"):
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
        if route.startswith("files."):
            return await self._files(route[6:], body, request)
        if route == "archive.upload":
            return await self._archive_upload(request)
        if route == "archive":
            return await self._archive(doc_id, request)
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
            return await self._refs(d, tab, body, request)
        if route == "import":
            return await self._import(d, tab, request)
        if route == "export":
            return await self._export(d, tab, request)
        if route == "export.save":
            return await self._export_save(d, tab, body)
        if route == "assembly/types":
            return await self._assembly_types(d, tab)
        if route.startswith("dialog."):
            return await self._dialog(d, tab, route[7:], body, request)
        if route.startswith("mates."):
            return await self._mate_dialog(d, tab, route[6:], body, request)
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
            reply = {}
            rebuilt = await self._edit(d, tab, body, client, base, reply)
            return self._json(200, {"rebuilt": rebuilt, **reply})
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

    async def _edit(self, d, tab, edit, client, base, reply=None):
        """Apply one edit, record who made it, push `changed` (and `tabs` when states moved); the rebuilt ids.
        `reply` (a dict, optional): for an Assembly's move_to it gets "placements", {instance id: 4x4} of only the
        instances the move changed (F17), so a drag can be drawn from the edit's answer without fetching the outputs."""
        moving = reply is not None and isinstance(edit, dict) and edit.get("op") == "move_to"

        def work():
            d.check_writable()
            before = _placements(d.session.outputs(tab)) if moving else None
            rebuilt = d.session.submit_edit(tab, edit)
            d.seq += 1
            if moving:
                after = _placements(d.session.outputs(tab))
                reply["placements"] = {k: T for k, T in after.items()
                                       if k not in before or not _same_placement(before[k], T)}
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

    async def _refs(self, d, tab, body, request=None):
        """POST /documents/<id>/tabs/<tab>/refs {"picks": [...], "dialog"?: did}: 200 {"document", "tab", "revision",
        "items": [...]}. See "Refs" in the module docstring. A query, so it is allowed on a read-only Document and
        never touches the lock, the revision or the dirty flag. With "dialog" the picks read that dialog's base
        model (F3)."""
        from . import picking
        extra = sorted(str(k) for k in body if k not in ("picks", "dialog"))
        if extra:
            return self._fail(400, f"{extra[0][:40]}: unknown field")
        try:
            picks = picking.parse_picks(body.get("picks"))
        except ValueError as e:
            return self._fail(400, str(e))
        dialog = None
        if "dialog" in body:
            did = body["dialog"]
            if not isinstance(did, str) or not re.fullmatch(r"[0-9a-f]{16}", did):
                return self._fail(400, "dialog: must be a dialog id")
            client = self._dialog_client(request)
            table = self._client_dialogs(client)
            hit = None if table is None else table.get(did)
            if hit is None or hit[0] != d.id or hit[1].tab != tab:
                return self._fail(404, "not found")                  # another client's dialog is not found either
            dialog = hit[1]
        items, revision = await self._in_session(d, lambda: (d.session.refs(tab, picks, dialog),
                                                            d.revisions.get(tab)))
        return self._json(200, {"document": d.id, "tab": tab, "revision": revision, "items": items})

    async def _import(self, d, tab, request):
        """POST /documents/<id>/tabs/<tab>/import?name=<file name>[&new_tab=1], the body the file's bytes
        (application/octet-stream, at most MAX_UPLOAD): one add_feature of an import feature at the roll-back bar of
        the Part Studio `tab`, or of a new Part Studio named after the file with new_tab=1. 200 {"document", "tab",
        "feature": {"id", "name", "error"}, "parts": [{"id", "name"}], "rebuilt", "tabs"}. 415 unless the body starts
        with ISO-10303-21; (STEP) or DBRep_DrawableShape (BREP); 413 over MAX_UPLOAD, from the Content-Length, before
        any of it is read; 423 on a read-only Document. `name` is shown only (see `step_import.clean_file_name`)."""
        from . import step_import
        ctype = (request.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        by_ref = ctype == "application/json"                         # I11.2: {root, entry} instead of the bytes
        if ctype != "application/octet-stream" and not by_ref:
            return self._fail(415, "Content-Type must be application/octet-stream or application/json")
        new_tab = request.query.get("new_tab", "0")
        if new_tab not in ("0", "1"):
            return self._fail(400, "new_tab must be 1 or 0")
        if by_ref:
            ref = await self._file_ref(request)
            if not isinstance(ref, tuple):
                return ref                                           # the refusal response
            root, eid = ref
            n = 0
        else:
            raw = request.headers.get("Content-Length")
            if request.headers.get("Transfer-Encoding") is not None or raw is None:
                return self._fail(411, "Content-Length is required")
            raw = raw.strip()
            if not raw.isascii() or not raw.isdigit():
                return self._fail(400, "Content-Length is not a number")
            n = int(raw)
            if n > MAX_UPLOAD:
                return self._fail(413, f"body over {MAX_UPLOAD} bytes")

        def check():                                                 # refused before the body is read
            d.check_writable()
            return next((t["kind"] for t in d.session.tabs() if t["id"] == tab), None)
        kind = await self._in_session(d, check)
        if new_tab == "0" and kind != "studio":
            return self._fail(400, "the tab is not a Part Studio")
        name = request.query.get("name")
        if by_ref:
            try:
                name, data = await self._off_loop(root.read, eid, MAX_UPLOAD)
            except FR.FileRootError as e:
                return self._fail(e.status, str(e))
        else:
            try:
                data = await asyncio.wait_for(request.content.readexactly(n), UPLOAD_DEADLINE) if n else b""
            except asyncio.TimeoutError:
                return self._fail(408, "request body too slow")
            except asyncio.IncompleteReadError:
                return self._fail(400, "body shorter than Content-Length")
        fmt = step_import.sniff(data)
        if fmt is None:
            return self._fail(415, "the body is not a STEP (ISO-10303-21;) or BREP (DBRep_DrawableShape) file")
        fname = step_import.clean_file_name(name, fmt)
        client, _ = self._edit_meta(request.headers.get("X-Client-Id"), None)
        if new_tab == "1":
            if len(d.tab_ids) >= MAX_TABS:
                return self._fail(409, f"a Document holds at most {MAX_TABS} tabs")
            stem = os.path.splitext(fname)[0]
            tab, _ = await self._tabs_changed(d, lambda: d.session.add_tab("studio", stem))
        before = await self._in_session(d, lambda: d.session.outputs(tab))
        feature = step_import.import_feature(fname, data)
        del data
        rebuilt = await self._edit(d, tab, {"op": "add_feature", "feature": feature}, client, None)
        after, tabs = await self._in_session(d, lambda: (d.session.outputs(tab), self._tab_view(d)))
        seen = {f.id for f in before.features}
        made = next((f for f in after.features if f.id not in seen), None)
        held = {p.id for p in before.parts}
        return self._json(200, {
            "document": d.id, "tab": next(t for t in tabs if t["id"] == tab), "rebuilt": rebuilt, "tabs": tabs,
            "feature": None if made is None else {"id": made.id, "name": made.name, "error": made.error},
            "parts": [{"id": p.id, "name": p.name} for p in after.parts if p.id not in held]})

    # ------------------------------------------------------------------ archive (I11.5)
    def _archive_source(self, doc_id, d):
        """Pool thread. `(folder, Document name)` of the Document `doc_id` (`d`: its `_Open`, or None when it is not
        open here), or None when nothing knows it or its folder is gone. The name is the registry's."""
        if d is not None:
            folder = d.folder
        else:
            folder = self.registry.trusted_path(doc_id) if self.registry is not None else None
        if not folder or not os.path.isdir(folder):
            return None
        name = d.name if d is not None else None
        if self.registry is not None:
            row = next((r for r in self.registry.list_documents() if r["id"] == doc_id), None)
            name = row["name"] if row is not None else name
        return folder, name or os.path.basename(folder.rstrip(os.sep))

    async def _archive(self, doc_id, request):
        """GET /documents/<id>/archive[?save=1] (see "Archive" in the module docstring)."""
        from urllib.parse import quote
        from . import doc_archive as DA
        from . import export_options as EO
        from . import kernel as K

        def pack(folder):                       # the store's own refusals are KernelErrors, not ArchiveErrors
            try:
                return DA.pack(folder)
            except DA.ArchiveError:
                raise
            except K.KernelError as e:
                raise DA.ArchiveError(str(e)) from None
        q = request.query
        for k in q:
            if k != "save" or len(q.getall(k)) != 1:
                return self._fail(400, f"query {k!r}: only save=1 or save=0, once")
        save = q.get("save", "0")
        if save not in ("0", "1"):
            return self._fail(400, "save must be 1 or 0")
        client, _ = self._edit_meta(request.headers.get("X-Client-Id"), None)
        d = self.docs.get(doc_id)
        found = await self._off_loop(self._archive_source, doc_id, d)
        if found is None:
            return self._fail(404, "not found")
        folder, name = found
        try:
            if d is None:
                data = await self._off_loop(pack, folder)
            else:
                if save == "1":                                    # the save route's own check, then its own save
                    def check():
                        d.check_writable()
                        return d.session.has_unsaved()
                    try:
                        unsaved = await self._in_session(d, check)
                    except DocumentLocked:
                        if d.lost:
                            await self._lost(d)
                        raise
                    if unsaved:
                        await self._save(d, client)                # raises DocumentLocked itself: 423

                def work():                                        # check and pack under one hold of the session lock
                    if save != "1" and d.session.has_unsaved():
                        return None
                    return pack(folder)
                data = await self._in_session(d, work)
                if data is None:
                    return self._fail(409, "unsaved", document=doc_id)
        except DA.ArchiveError as e:
            return self._fail(422, str(e))
        fname = EO.export_file_name(name, "step")[:-len(".step")] + DA.EXTENSION
        ascii_name = "".join(c if c.isascii() and (c.isalnum() or c in " ._-") else "_" for c in fname)
        return self._send(200, data, "application/octet-stream", [
            ("Content-Disposition", f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(fname, safe='')}")])

    async def _archive_upload(self, request):
        """POST /documents/archive?name=<name>, the body the .fsdoc bytes (see "Archive" in the module docstring)."""
        from . import doc_archive as DA
        if self.registry is None:
            return self._fail(404, "this server serves a single Document; it cannot open an archive")
        q = request.query
        for k in q:
            if k != "name" or len(q.getall(k)) != 1:
                return self._fail(400, f"query {k!r}: only name, once")
        name = q.get("name")
        if name is None:
            return self._fail(400, "name is required")
        if name.lower().endswith(DA.EXTENSION):
            name = name[:-len(DA.EXTENSION)]
        try:
            folder_name_for(name)
        except ValueError as e:
            return self._fail(400, str(e))
        ctype = (request.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/octet-stream":
            return self._fail(415, "Content-Type must be application/octet-stream")
        raw = request.headers.get("Content-Length")
        if request.headers.get("Transfer-Encoding") is not None or raw is None:
            return self._fail(411, "Content-Length is required")
        raw = raw.strip()
        if not raw.isascii() or not raw.isdigit():
            return self._fail(400, "Content-Length is not a number")
        n = int(raw)
        if n > MAX_ARCHIVE:
            return self._fail(413, f"body over {MAX_ARCHIVE} bytes")
        try:
            data = await asyncio.wait_for(request.content.readexactly(n), ARCHIVE_DEADLINE) if n else b""
        except asyncio.TimeoutError:
            return self._fail(408, "request body too slow")
        except asyncio.IncompleteReadError:
            return self._fail(400, "body shorter than Content-Length")

        def work():
            with self._create_lock:
                folder = DA.unpack(data, self.root, name, registry=self.registry)
                row = next((r for r in self.registry.list_documents() if r["path"] == folder), None)
                if row is None:
                    raise DA.ArchiveError("the new Document could not be recorded in the registry")
                return row["id"], row["name"]
        try:
            doc_id, doc_name = await self._off_loop(work)
        except DA.ArchiveError as e:
            return self._fail(422, str(e))
        except OSError as e:
            if e.errno == errno.ENOSPC:
                return self._fail(507, "the Documents folder is full")
            raise
        return self._json(201, {"id": doc_id, "name": doc_name})

    # ------------------------------------------------------------------ file roots (I11.2)
    def _file_root(self, name):
        """The configured root `name` (the path regex already limited it to the name alphabet), or None."""
        return self.file_roots.get(name)

    async def _file_ref(self, request):
        """The `(FileRoot, entry id)` of an import body {"root", "entry"}, or the refusal response: 400 for a body
        that is not that shape, 404 for a root nothing configured. The entry id is checked when it is read."""
        body = await self._body(request)
        if not isinstance(body, dict):
            return body
        name, eid = body.get("root"), body.get("entry")
        if not isinstance(name, str) or not isinstance(eid, str):
            return self._fail(400, 'the body must be {"root": name, "entry": id}')
        root = self._file_root(name)
        if root is None:
            return self._fail(404, "not found")
        return root, eid

    @staticmethod
    def _page_number(request, key, default, low, high):
        """An integer query value in `low`..`high` (a larger one is cut to `high`), or None when it is not a number."""
        raw = request.query.get(key)
        if raw is None:
            return default
        if not raw.isascii() or not raw.isdigit() or len(raw) > 9:
            return None
        n = int(raw)
        return None if n < low else min(n, high)

    async def _files(self, verb, body, request):
        """GET /files, GET /files/<root>, POST /files/<root>/rescan. Index work runs on the session pool."""
        if verb == "list":
            roots = []
            for r in self.file_roots.values():
                index = await self._off_loop(r.index)
                roots.append({"name": r.name, "files": len(index.items), "max_upload": MAX_UPLOAD})
            return self._json(200, {"roots": roots, "export_roots": [
                {"name": r.name, "max_bytes": MAX_EXPORT_SAVE} for r in self.export_roots.values()]})
        root = self._file_root(_FILES_PATH.match(request.raw_path.split("?", 1)[0]).group(1))
        if root is None:
            return self._fail(404, "not found")
        if verb == "rescan":
            index = await self._off_loop(root.rescan)
            return self._json(200, {"root": root.name, "files": len(index.items)})
        offset = self._page_number(request, "offset", 0, 0, 10 ** 9)
        limit = self._page_number(request, "limit", FR.DEFAULT_LIMIT, 1, FR.MAX_LIMIT)
        if offset is None or limit is None:
            return self._fail(400, f"offset must be 0 or more and limit 1 to {FR.MAX_LIMIT}")
        folder, q = request.query.get("dir", ""), request.query.get("q", "")
        if len(q) > 200:
            return self._fail(400, "q is too long")
        index = await self._off_loop(root.index)
        if q.strip():
            reply = await self._off_loop(index.search, q, folder, offset, limit)
        else:
            reply = await self._off_loop(index.listing, folder, offset, limit)
        if reply is None:
            return self._fail(404, "not found")                  # `dir` is no folder of the index
        return self._json(200, {"root": root.name, "q": q, **reply, "offset": offset, "limit": limit})

    # ------------------------------------------------------------------ export (I11.3)
    _EXPORT_KEYS = ("format", "parts", "preset", "defl", "angle", "ascii")

    async def _make_export(self, d, tab, spec):
        """The one place an export is made: `(bytes, file name, skipped names)` of the tab `tab` of the open Document
        `d` as it is now. `spec` is {"format": "step" | "stl", "parts": a list of part ids or None, and for an STL
        "preset", "defl", "angle", "ascii" (the values of GET .../export, strings or JSON numbers; absent or None
        leaves one out)}. Raises the session's error (a 400) for a format that is not step or stl, a tab that is not
        a Part Studio or Assembly, `parts` on an Assembly, an unknown part id, an option out of range, or nothing
        left to write. The file name is `<tab name>.step` / `.stl` made safe by `export_options.export_file_name`.
        The engine call runs off the event loop under the Document's session lock; no Document state changes, so a
        read-only Document can export."""
        from . import export_options as EO
        fmt = str(spec.get("format") or "").lower()
        if fmt not in ("step", "stl"):
            raise self.session_error("format must be step or stl")
        parts = spec.get("parts")
        if parts is not None:
            if (not isinstance(parts, (list, tuple)) or not parts or len(parts) > MAX_EXPORT_PARTS
                    or not all(isinstance(p, str) and 0 < len(p) <= 64 for p in parts)):
                raise self.session_error(f"parts must be 1 to {MAX_EXPORT_PARTS} part ids")
        try:
            options = EO.stl_options(*(spec.get(k) for k in ("preset", "defl", "angle", "ascii")))
        except ValueError as e:
            raise self.session_error(str(e)) from e

        def work():
            row = next((t for t in d.session.tabs() if t["id"] == tab), None)
            if row is None:
                raise self.session_error(f"no tab {tab!r} in this Document")
            if row["kind"] not in ("studio", "assembly"):
                raise self.session_error(f"a {row['kind']} tab cannot be exported to STEP or STL")
            if parts is not None and row["kind"] != "studio":
                raise self.session_error("parts is for a Part Studio; an Assembly exports its shown instances")
            got = d.session.export(tab, fmt, parts, options)
            return got["data"], EO.export_file_name(row["name"], fmt), got["skipped"]
        return await self._in_session(d, work)

    async def _export(self, d, tab, request):
        """GET /documents/<id>/tabs/<tab>/export (see "Export" in the module docstring): `_make_export`, then the
        bytes as a download."""
        from urllib.parse import quote
        q = request.query
        for k in q:
            if k not in self._EXPORT_KEYS or len(q.getall(k)) != 1:
                return self._fail(400, f"query {k!r}: not one of {', '.join(self._EXPORT_KEYS)}, or given twice")
        spec = {k: q.get(k) for k in self._EXPORT_KEYS}
        if spec["parts"] is not None:
            spec["parts"] = spec["parts"].split(",")
        data, name, skipped = await self._make_export(d, tab, spec)
        ascii_name = "".join(c if c.isascii() and (c.isalnum() or c in " ._-") else "_" for c in name)
        return self._send(200, data, "model/stl" if name.endswith(".stl") else "model/step", [
            ("Content-Disposition", f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"),
            ("X-FreeShape-Skipped", ", ".join(quote(n, safe=" ") for n in skipped))])

    # ------------------------------------------------------------------ export roots (I11.13)
    _SAVE_KEYS = ("root", "name", "replace") + _EXPORT_KEYS

    @staticmethod
    def _check_export_roots(export_roots, file_roots, folder, root, data_dir, registry):
        """ER.OverlapError (a ValueError) for an export root that shares a name with a file root or another export
        root, or overlaps a Document, the Documents root, the data folder or a file root."""
        if not export_roots:
            return
        names = [n for n, _ in export_roots]
        if len(set(names)) != len(names) or set(names) & {n for n, _ in file_roots}:
            raise ER.OverlapError("an export root's name must differ from every other root's name")
        protected = [("the Document folder", folder), ("the Documents root", root), ("the data folder", data_dir)]
        protected += [(f"file root {n!r}", p) for n, p in file_roots]
        if registry is not None:
            protected += [("a Document in the registry", row["path"]) for row in registry.list_documents()]
        ER.check_overlaps(export_roots, protected)

    async def _export_save(self, d, tab, body):
        """POST .../export (see "Export roots" in the module docstring): `_make_export`, then `ExportRoot.write` off
        the event loop. The reply and every error name the root and the file name only."""
        for k in body:
            if k not in self._SAVE_KEYS:
                return self._fail(400, f"body key {k!r}: not one of {', '.join(self._SAVE_KEYS)}")
        name, replace, root_name = body.get("name"), body.get("replace", False), body.get("root")
        if not isinstance(root_name, str):
            return self._fail(400, 'the body must name an export root: {"root": name, ...}')
        if (name is not None and not isinstance(name, str)) or not isinstance(replace, bool):
            return self._fail(400, "name must be a string and replace true or false")
        root = self.export_roots.get(root_name)
        if root is None:
            return self._fail(404, "not found")
        data, made, skipped = await self._make_export(d, tab, {k: body.get(k) for k in self._EXPORT_KEYS})
        fname = made if name is None else ER.clean_export_name(name, "stl" if made.endswith(".stl") else "step")
        if len(data) > MAX_EXPORT_SAVE:
            return self._fail(413, f"the export is over {MAX_EXPORT_SAVE} bytes")
        try:
            replaced = await self._off_loop(root.write, fname, data, replace)
        except ER.ExportError as e:
            if e.code == "exists":
                return self._fail(409, "exists", name=fname, root=root.name)
            return self._fail(e.status, str(e))
        return self._json(201, {"root": root.name, "name": fname, "bytes": len(data), "replaced": replaced,
                                "skipped": list(skipped)})

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
            mates = (self.clients.get(client).data or {}).get("mate_dialogs") or {}
            for did in [k for k, (doc, _) in mates.items() if doc == doc_id]:
                del mates[did]

    # ------------------------------------------------------------------ mate dialogs (I10.5)
    def _client_mate_dialogs(self, client, make=False):
        """The client's {dialog id: (Document id, tab id)} table of mate dialogs (the dialogs themselves live in the
        Assembly tab's engine worker, freeshape/mate_session.py), or None. `make` as in `_client_dialogs`."""
        entry = self.clients.get(client)
        if entry is None and make:
            self._client_dialogs(client, make=True)
            entry = self.clients.get(client)
        return None if entry is None else entry.data.setdefault("mate_dialogs", {})

    def _open_dialog_count(self, client=None):
        """Feature and mate dialogs open for `client`, or for every client when None."""
        def of(c):
            data = self.clients.get(c).data or {}
            return len(data.get("dialogs") or {}) + len(data.get("mate_dialogs") or {})
        return of(client) if client is not None else sum(of(c) for c in self.clients.ids())

    async def _close_mates(self, items):
        """Tell the workers to forget mate dialogs [(Document id, tab, dialog id)] (an expired client's)."""
        for doc_id, tab, did in items:
            d = self.docs.get(doc_id)
            if d is None:
                continue
            try:
                await self._in_session(d, lambda: d.session.close_mate_dialog(tab, did))
            except Exception:                                    # noqa: BLE001  (the tab or its worker went away)
                pass

    async def _mate_dialog(self, d, tab, verb, body, request):
        """The mate dialog routes; see "Mate dialogs" in the module docstring."""
        client = self._dialog_client(request)
        allowed = {"open": ("type", "picks", "mate"), "update": MATE_UPDATE_KEYS}.get(verb, ())
        extra = sorted(str(k) for k in body if k not in allowed)
        if extra:
            return self._fail(400, f"{extra[0][:40]}: unknown field")
        if verb == "open":
            if "mate" in body and ("type" in body or "picks" in body):
                return self._fail(400, "mate: give either a mate to edit or a type and picks, not both")
            table = self._client_mate_dialogs(client, make=True)
            if self._open_dialog_count(client) >= MAX_DIALOGS:
                return self._fail(409, f"a client may hold at most {MAX_DIALOGS} open dialogs")
            if self._open_dialog_count() >= MAX_DIALOGS_TOTAL:
                return self._fail(409, "too many open dialogs")

            def work():
                did, got = d.session.open_mate_dialog(tab, body.get("type"), body.get("mate"), body.get("picks", []))
                return did, got, d.revisions.get(tab)
            did, got, revision = await self._in_session(d, work)
            table[did] = (d.id, tab)
            return self._json(201, {"dialog": did, "document": d.id, "tab": tab, "revision": revision, **got})
        did = _MATE_PATH.match(request.raw_path.split("?", 1)[0]).group(3)
        table = self._client_mate_dialogs(client)
        if table is None or table.get(did) != (d.id, tab):
            return self._fail(404, "not found")                  # another client's dialog is not found either
        if verb == "update":
            got = await self._in_session(d, lambda: (d.session.update_mate_dialog(tab, did, body),
                                                     d.revisions.get(tab)))
            return self._json(200, {"dialog": did, "document": d.id, "tab": tab, "revision": got[1], **got[0]})
        if verb == "cancel":
            table.pop(did, None)
            await self._close_mates([(d.id, tab, did)])
            return self._json(200, {"cancelled": True})
        edit = await self._in_session(d, lambda: d.session.mate_dialog_edit(tab, did))       # accept
        rebuilt = await self._edit(d, tab, edit, client, None)
        table.pop(did, None)
        await self._close_mates([(d.id, tab, did)])
        return self._json(200, {"rebuilt": rebuilt, "revision": d.revisions.get(tab), "mate": edit["mate"]["id"]})

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
        if verb == "base":                                       # F3: the model the dialog picks against
            data, revision = await self._in_session(d, lambda: (d.session.dialog_base(dialog), d.revisions.get(tab)))
            return self._send(200, data, "application/octet-stream", [("X-Revision", str(revision))])
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

    async def _assembly_types(self, d, tab):
        """GET .../tabs/<tab>/assembly/types: the mate and relation types of freeshape/assembly.py (see "Assembly
        types" in the module docstring). 400 for a tab that is not an Assembly."""
        kind = await self._in_session(d, lambda: next((t["kind"] for t in d.session.tabs() if t["id"] == tab), None))
        if kind != "assembly":
            return self._fail(400, f"tab {tab!r} is a {kind}, not an assembly")
        from . import assembly as ASM
        mates = [{"id": t, "label": label, "help": tip, "freedoms": list(ASM.MATE_VALUE_DOFS[t])}
                 for t, (label, tip) in ASM.MATE_TYPES.items()]
        relations = [{"id": t, "label": spec["label"], "help": spec["help"]} for t, spec in ASM.RELATION_TYPES.items()]
        return self._json(200, {"document": d.id, "tab": tab, "mates": mates, "relations": relations})

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
            mates = (entry.data or {}).pop("mate_dialogs", None) or {}
            if mates:
                self._spawn(self._close_mates([(doc, tab, did) for did, (doc, tab) in mates.items()]))
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
            out = await self._sketch_do(client, kind, msg, doc_id, ws)
        except DocumentLocked as e:
            await self._push(ws, {"type": "error", "id": ident, "for": kind, "document": e.document,
                                  "message": str(e), "locked": True, "holder": e.holder})
        except (self.session_error, SketchError) as e:
            await self._push(ws, {"type": "error", "id": ident, "for": kind[:40], "message": str(e)})
        except Exception as e:                                   # noqa: BLE001
            _log(f"sketch message {_clean(kind, 40)}: {type(e).__name__}: {_clean(str(e), 300)}")
            await self._push(ws, {"type": "error", "id": ident, "for": kind[:40], "message": "internal error"})
        else:
            if out is not None:                                  # a drag move is answered by its solve, later
                await self._push(ws, {**out, "id": ident})

    MAX_SKETCHES = 64                                            # sketch sessions open on this server at once

    _SKETCH_FIELDS = {"sketch.open": {"tab", "feature", "plane"},
                      "sketch.add": {"shape", "picks", "construction", "radius", "sweep", "constraints"},
                      "sketch.delete": {"ids"}, "sketch.construction": {"ids", "value"},
                      "sketch.undo": set(), "sketch.redo": set(), "sketch.state": set(), "sketch.commit": set(),
                      "sketch.cancel": set(), "sketch.constrain": {"tool", "picks"}, "sketch.unconstrain": {"constraint"},
                      "sketch.fits": {"selection"}, "sketch.dimension": {"picks", "pos", "kind", "value"},
                      "sketch.dimension.set": {"constraint", "value", "pos"}, "sketch.fillet": {"point", "radius"},
                      "sketch.trim": {"entity", "at"}, "sketch.use_edge": {"pick", "construction"},
                      "sketch.mirror": {"ids", "line", "preview"},
                      "sketch.linear_pattern": {"ids", "line", "at", "count", "spacing", "floor", "preview"},
                      "sketch.circular_pattern": {"ids", "centre", "count", "angle", "preview"},
                      "sketch.drag.begin": {"point", "entity", "at"},
                      "sketch.drag.move": {"at", "t"}, "sketch.drag.end": {"cancel"}}

    async def _sketch_do(self, client, kind, msg, doc_id, ws=None):
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
            if sk.drag is not None:
                raise SketchError("a drag is in progress; end it first")
            return await self._sketch_commit(entry, sk, client)
        if kind.startswith("sketch.drag."):
            return await self._sketch_drag(sk, kind, msg, body, ws)

        got = None
        if kind == "sketch.use_edge":                            # the engine answers which entity the edge gives
            d = self.docs.get(sk.document)
            if d is None:
                raise SketchError("the sketch's Document is not open")

            def project():
                with sk.lock:
                    if sk.committing:
                        raise SketchError("the sketch is being committed")
                    sk._not_dragging()
                    pick, plane = sk.use_edge_pick(body), sk.plane
                return d.session.project_edge(sk.tab, plane, pick)
            got = await self._in_session(d, project)

        def step():
            with sk.lock:
                if sk.committing:
                    raise SketchError("the sketch is being committed")
                made = None
                if kind == "sketch.fits":
                    return {"type": "sketch.fitted", "selection": body.get("selection"), "tools": sk.fits(body)}
                if kind == "sketch.constrain":
                    res = sk.constrain(body)
                    return {"type": "sketch.constrained", **res, **sk.view()}
                if kind == "sketch.add":
                    made = sk.add(body)
                elif kind == "sketch.dimension":
                    made = sk.dimension(body)
                elif kind == "sketch.dimension.set":
                    made = sk.dimension_set(body)
                elif kind == "sketch.fillet":
                    made = sk.fillet(body)
                elif kind == "sketch.trim":
                    made = sk.trim(body)
                elif kind == "sketch.use_edge":
                    made = sk.use_edge(body, got)
                elif kind in ("sketch.mirror", "sketch.linear_pattern", "sketch.circular_pattern"):
                    made = getattr(sk, kind[7:])(body)
                    if body.get("preview"):                      # nothing changed: only this client is answered
                        return {"type": "sketch.previewed", "for": kind, **made}
                elif kind == "sketch.unconstrain":
                    sk.unconstrain(body)
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

    async def _sketch_drag(self, sk, kind, msg, body, ws):
        """sketch.drag.*: begin and end run off the loop under the session's lock. A move is not answered here: it
        replaces the one waiting move (latest wins) and a worker task solves it off the loop, then answers the
        socket that sent it. drag.end first waits for the waiting move to be solved, so the result is the last move."""
        from .sketch_session import SketchError
        if kind == "sketch.drag.begin":
            def begin():
                with sk.lock:
                    if sk.committing:
                        raise SketchError("the sketch is being committed")
                    sk.drag_begin(body)
            await self._off_loop(begin)
            return {"type": "sketch.drag.begun"}
        if kind == "sketch.drag.move":
            if sk.drag is None:
                raise SketchError("no drag is in progress")
            sk.drag_pending = (ws, msg.get("id"), body)
            if sk.drag_worker is None:
                sk.drag_worker = asyncio.ensure_future(self._drag_worker(sk))
            return None
        cancel = body.get("cancel", False)
        if not isinstance(cancel, bool):
            raise SketchError("cancel: must be true or false")
        if cancel:
            sk.drag_pending = None
        while sk.drag_worker is not None:
            await asyncio.shield(sk.drag_worker)

        def end():
            with sk.lock:
                sk.drag_end(cancel)
                return {"type": "sketch.changed", **sk.view()}
        return await self._off_loop(end)

    async def _drag_worker(self, sk):
        """Solve the waiting move, then the one that arrived meanwhile, until none waits."""
        from .sketch_session import SketchError

        def solve(body):
            with sk.lock:
                return sk.drag_move(body)
        try:
            while sk.drag_pending is not None:
                ws, ident, body = sk.drag_pending
                sk.drag_pending = None
                try:
                    out = {"type": "sketch.drag.moved", **await self._off_loop(solve, body)}
                except SketchError as e:
                    await self._push(ws, {"type": "error", "id": ident, "for": "sketch.drag.move", "message": str(e)})
                except Exception as e:                           # noqa: BLE001
                    _log(f"drag move: {type(e).__name__}: {_clean(str(e), 300)}")
                    await self._push(ws, {"type": "error", "id": ident, "for": "sketch.drag.move",
                                          "message": "internal error"})
                else:
                    await self._push(ws, {**out, "id": ident})
        finally:
            sk.drag_worker = None

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
            sk.variables = {k: v for k, v in dict(frame.get("variables") or {}).items()
                            if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)}
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
                autosave_interval=AUTOSAVE_INTERVAL, autosave_burst=AUTOSAVE_BURST, lock_timeout=doc_lock.DEFAULT_TIMEOUT, file_roots=(),
                export_roots=()):
    """A server on 127.0.0.1:`port` (bound, not yet serving). `file_roots` is a list of (name, folder) pairs, the
    read-only roots of GET /files (I11.2); `export_roots` the writable ones an export is saved into (I11.13;
    `export_roots.OverlapError` for one that overlaps a Document, the data folder or a file root). Over `session`: a single Document (the old routes
    alias it); `folder` and `lock_program` (the CLI passes "browser") make it take the Document lock, so an
    in-process server given neither has no lock file. Over `registry` and `root` instead: a Documents root, where
    Documents are created under `root` and opened by id. Callers run `serve_forever` (it blocks) and, at the end,
    `shutdown`, `server_close` and close the session; `close_all` does the last three. `data_dir` is where the autosave
    lives (`<data_dir>/autosave-server`); without it there is none. `autosave_interval` and `autosave_burst` are the
    autosave timers in seconds, `lock_timeout` the age at which a heartbeat is stale; tests drive `autosave_now()`,
    `expire_idle_now()` and `refresh_locks_now()` instead of waiting for them."""
    return _Server(session, port, allow_origins, client_ttl, client_expired, registry, root, folder, lock_program,
                   data_dir, autosave_interval, autosave_burst, lock_timeout, file_roots, export_roots)


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
    ap.add_argument("--file-root", action="append", default=[], metavar="NAME=PATH",
                    help="offer a read-only folder of STEP files to the client's file explorer (repeatable; NAME is "
                         "a-z, 0-9 and -, up to 32 characters)")
    ap.add_argument("--export-root", action="append", default=[], metavar="NAME=PATH",
                    help="a writable folder an export can be saved into from the client (repeatable; NAME as for "
                         "--file-root and different from every file root's; PATH an existing folder this user can "
                         "write, apart from the Documents, the data folder and the file roots)")
    args = ap.parse_args(argv)
    export_roots = []
    for value in args.export_root:
        try:
            export_roots.append(ER.parse_spec(value))
        except ValueError as e:
            print(f"freeshape serve: refusing --export-root {_clean(value, 120)!r}: {e}.", file=sys.stderr)
            return 2
    file_roots = []
    for value in args.file_root:
        try:
            file_roots.append(FR.parse_spec(value))
        except ValueError as e:
            print(f"freeshape serve: refusing --file-root {_clean(value, 120)!r}: {e}.", file=sys.stderr)
            return 2
    if len({n for n, _ in file_roots}) != len(file_roots):
        print("freeshape serve: --file-root names must be different.", file=sys.stderr)
        return 2
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
    extra.update(data_dir=data, autosave_interval=args.autosave_interval, lock_timeout=args.lock_timeout,
                 file_roots=file_roots, export_roots=export_roots)
    try:
        server = make_server(session, args.port, args.allow_origin, args.client_expiry, **extra)
    except ER.OverlapError as e:
        if session is not None:
            session.close()
        print(f"freeshape serve: refusing --export-root: {e}.", file=sys.stderr)
        return 2
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
