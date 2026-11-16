# freeshape serve

`python -m freeshape serve <folder> [--port N] [--data-dir DIR] [--allow-origin ORIGIN]...` serves Documents over HTTP
(ADR 0010, ADR 0014). It binds 127.0.0.1 only and has no authentication. `--host` exists only to refuse any other
address.

## One Document or a Documents root

- A folder that holds `document.json` is one Document. serve opens it at start, takes its lock as `browser` (see
  below) and answers the old routes (`/tabs`, `/tabs/<id>/...`, `/save`, `/ws`) for it, as well as the
  Document-keyed ones.
- Any other folder is a Documents root. serve lists the Documents in the registry, makes new ones inside the root
  (`POST /documents {"name": ...}`) and opens one at a time by id (`POST /documents/<id>/open`). The old routes do not
  exist there: every route names the Document, `/documents/<id>/tabs/<tab>/...`.

The route table is in the docstring of `freeshape/serve.py`.

## The registry, and the path rule

A Document is known by its id in the registry, `documents.json` in the data directory. That file is the desktop's
own: `--data-dir` (default: the desktop's, `$FREESHAPE_DATA_DIR` or the per-user data folder) makes serve read and
write the same one, so a Document made by either program shows up in the other.

The registry stores each Document as an absolute real path. serve must therefore see the Documents at the same path
as the desktop. When the desktop runs in a container, its Documents are under `/config`; a serve that reaches the same
folder as `/data/Documents` would find every Document missing. Mount the config folder at the same path in both
places, with the same home and the same data directory. If the registry lists Documents and none of their folders
exist, serve stops before it listens, prints the first missing path and this rule, and exits with status 1. When only
some are missing, serve starts and lists those with `"missing": true`; they cannot be opened.

Nothing in a request becomes a file path. A Document is addressed by its registry id, which is only ever a key. A new
Document's folder is `<root>/<name made safe>`: the name must be 1..100 characters with no path separator (`/`, `\`),
colon or control character; letters, digits, space, `.`, `_` and `-` are kept, anything else becomes `-`; leading and
trailing dots, dashes and spaces go; at most 80 characters remain. `../x`, an absolute path or an empty name is
refused with 400. An existing folder is never reused: the next free `<name> 2`, `<name> 3` is made.

## The Document lock

The desktop and serve share a Document through `.freeshape-lock` in the Document folder (`freeshape/doc_lock.py`).
serve holds it as `browser` while a Document is open and refreshes the heartbeat every 10 seconds. If another program
holds a live lock when serve opens the Document, it opens read-only: tabs and outputs work, and every edit and save
answers 423 with the holder's name. `POST /documents/<id>/take-over` takes the lock and reloads the Document from its
folder. If the lock is taken from serve while the Document is open, the next heartbeat (or the next edit) notices;
the Document turns read-only and its sockets get a `lock` message. Closing releases the lock. At most one Document is
open at a time; opening a second answers 409 naming the open one.

## Reaching it from the iPad

The iPad cannot reach a loopback port, so a tunnel sits in front of serve and gives it a public HTTPS name:

    iPad  --https-->  tunnel (https://cad.example.test)  --http-->  127.0.0.1:<port>  (serve)

The browser sends the public name as `Host` and as `Origin`. serve refuses both unless you name that origin:

    python -m freeshape serve examples/documents/bracket --port 3090 --allow-origin https://cad.example.test

- Each value is an exact `https://host[:port]` origin: no wildcard, no path, no query, no user name, no IP literal.
  Anything else stops serve at start with a message, before it listens.
- `--allow-origin` can be repeated. With none, only loopback is accepted.
- For each origin serve accepts that `Host` (with or without its port) and that `Origin`, on HTTP requests and on the
  WebSocket upgrade. Any other `Host` gets 421; any other `Origin` gets 403.
- `X-Forwarded-*` headers are never read for this decision.
- The bind address does not change. Only the tunnel's name is added to the allow-list.

This list is the defence against cross-site and DNS-rebinding requests on a port that can edit a Document. Name only
origins you control, and put access control (for example the tunnel's own login) in front of them, because serve has none.

## Export roots

`--export-root NAME=PATH` (repeatable) names a folder the client can save an export into, as well as download it, so a
program on the same machine (Bambu Studio) can read it without a round trip. File roots (`--file-root`) stay read-only;
an export root is separate. NAME is 1 to 32 of a-z, 0-9 and `-`, and differs from every file root's name.

- PATH must be an existing folder this user can write. serve refuses to start, with a message, when a root is equal to,
  inside or around the Documents root, a Document (served or in the registry), the `--data-dir`, a file root or another
  export root.
- `GET /files` lists them as `export_roots: [{"name", "max_bytes"}]`. They are not browsed: `GET /files/<name>` is 404.
- `POST /documents/<id>/tabs/<tab>/export` with `{"root", "format", "name"?, "parts"?, "preset"?, "defl"?, "angle"?,
  "ascii"?, "replace"?}` makes the export as `GET .../export` does and saves it as one file, directly in the root (no
  sub-folders). The name is the tab's name unless `name` is given, made safe by the same rule as a download's name,
  with the extension of the format. A name that exists is a 409 `exists` unless `replace` is true; a symlink, a folder
  or a FIFO in the way is a 409 either way. Reply 201 `{"root", "name", "bytes", "replaced", "skipped"}`. No reply holds
  a server path. An export over 256 MiB is a 413; a full folder is a 507 with no partial file left.
- The file is written under a temporary name in the same folder, then linked (or, with `replace`, renamed) into place
  relative to a directory descriptor of the root. A root swapped for a symlink after start is refused. Where the file
  system refuses hard links, serve creates the name with O_EXCL and copies instead; a reader can then see the file
  while it is being copied.
- The saved file has mode 0644, whatever the umask. In a setgid folder (the shared folder is `ncr:docker`, 2775) it gets
  the folder's group, so a program running as the same user and group reads it. serve and that program must run as the
  same user, or the folder must otherwise let both write.
- At start a root's own `.freeshape-export-*.tmp` files older than an hour (left by a crash) are removed; other files
  are never touched.

In the preview: `--export-root shared=/config/Shared`, a folder that is mounted read-write there.

## Running in a container

The tunnel's proxy runs on the host and connects to 127.0.0.1:<port> there. A container's loopback is its own, so
run it with host networking (`docker run --network host ...`). serve still binds 127.0.0.1, now the host's. Pick a
free port with `--port`.

## Running the built client

`npm --prefix web run build` writes the client to `freeshape/static/app/`, and serve hosts it at `/`. Behind the tunnel
nothing else is needed: the page reaches the API and the WebSocket on its own origin, which is the name given with
`--allow-origin`. `sh scripts/web/e2e.sh` runs the browser smoke against a throwaway copy of the example Documents.
