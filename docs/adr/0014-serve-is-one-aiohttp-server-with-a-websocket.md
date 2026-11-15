# 14. Serve is one aiohttp server with a WebSocket

Date: 2026-11-15

## Status

Accepted (Adam, 2026-11-15). Supersedes the standard-library-only part of ADR 0010.

## Context

ADR 0010 built `freeshape serve` on the standard library's HTTP server, request and reply only, because the gate image had no web or WebSocket package and there was no client to push to. It said pushed updates "come with the first real client".

The browser client (ADR 0013) is that client. It needs results pushed after every edit, and it sends a message per pointer move while a sketch is dragged (ADR 0015). The iPad reaches the server through a tunnel that exposes one port, so the routes, the static front end and the live channel should share one server.

Alternatives considered:

- **Standard library, Server-Sent Events plus POST.** Keeps ADR 0010 whole. Each pointer move is a separate HTTP request through the tunnel, with more overhead and possible reordering.
- **Standard library, hand-written WebSocket.** No dependency, but protocol code of our own on a port that can edit a Document.
- **FastAPI with uvicorn.** Well known, typed schemas, a natural base for the multi-Document server with login. More packages and structure than the current seven routes need.

## Decision

`freeshape serve` becomes one aiohttp server that serves the HTTP routes, the built front end and a WebSocket on one port.

These parts of ADR 0010 stay: it binds to 127.0.0.1 only and refuses any other address, it has no authentication of its own, nothing is unpickled from the wire, and outputs use the existing wire format. Access from other devices is through the tunnel and its access control.

## Consequences

- aiohttp is the first web dependency. Its pin goes in `pyproject.toml`, `requirements.txt`, the Docker requirements and `Dockerfile.harness`, and the gate image is rebuilt.
- The session is synchronous and guarded by one lock. Calls into it run off the event loop so a slow rebuild does not stall the socket.
- The existing routes and their tests are ported. The check that serve imports no Qt stays.
- The request and reply viewer page is replaced by the browser client.
- If the multi-Document server with login is built later, moving to another framework means porting these routes again.
