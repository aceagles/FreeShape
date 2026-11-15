# freeshape serve

`python -m freeshape serve <Document folder> [--port N] [--allow-origin ORIGIN]...` serves one Document over HTTP
(ADR 0010, ADR 0014). It binds 127.0.0.1 only and has no authentication. `--host` exists only to refuse any other
address.

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

## Running in a container

The tunnel's proxy runs on the host and connects to 127.0.0.1:<port> there. A container's loopback is its own, so
run it with host networking (`docker run --network host ...`). serve still binds 127.0.0.1, now the host's. Pick a
free port with `--port`.

## Running the built client

`npm --prefix web run build` writes the client to `freeshape/static/app/`, and serve hosts it at `/`. Behind the tunnel
nothing else is needed: the page reaches the API and the WebSocket on its own origin, which is the name given with
`--allow-origin`. `sh scripts/web/e2e.sh` runs the browser smoke against a throwaway copy of the example Documents.
