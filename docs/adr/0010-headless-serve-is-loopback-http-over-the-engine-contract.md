# 10. Headless serve is loopback HTTP over the engine contract

Date: 2026-11-13

## Status

Proposed (written by Claude while planning the Parity Sweep; Adam to accept or change)

## Context

[ADR 0002](0002-one-worker-process-per-tab.md) put tab regeneration behind an engine contract and said the contract "stays the same if a tab's worker later moves to another machine". A scan of `master` on 2026-11-13 tested that claim:

- No module outside `freeshape/ui/` imports Qt, apart from the launcher.
- Outputs are plain data, but they cross the process boundary by pickle.
- The engine opens tabs by local file path, and the worker reopens the same path from its own disk.
- There is no entry point that opens a Document and returns outputs without the desktop app.
- The GUI still runs previews, sketch solving, picking, assembly solving and hidden-line removal on its own kernel shapes, so it is not yet a pure client of the engine.

Today "cloud" means the Qt window streamed from a container. The Roadmap lists cloud, collaboration and auth as not started. A first step is wanted that commits to none of them.

Alternatives considered:

- **WebSocket with pushed updates.** The right shape for a live client, but needs a dependency the gate image does not have, and there is no client yet to push to.
- **An existing RPC or serialisation library** (gRPC, msgpack). Same dependency problem; the outputs are a small fixed set of types.
- **Bind to the network with a token.** Hand-rolled access control on a port that can edit a Document is how boxes get owned. Access from other devices already has a route: the tunnel with its own access control.

## Decision

- Engine outputs get a wire format made with the standard library and numpy only: a JSON header plus raw buffers. Nothing is ever unpickled from the wire.
- The engine can open a tab from bytes and return a tab's bytes, so it needs no file paths. Opening by path becomes a wrapper.
- A `DocumentSession` opens a Document from a store ([ADR 0009](0009-tab-data-goes-through-a-document-store.md)) on a process engine with no Qt.
- `python -m freeshape serve <Document>` exposes one session over HTTP with the standard library's server: tab list, outputs in the wire format, mesh JSON, edits from the contract's vocabulary, and save.
- It binds to 127.0.0.1 only and refuses any other address. It has no authentication.
- A viewer page in plain WebGL, with no library and no outside URL, draws the meshes.

## Consequences

- A Document can be opened and looked at with no desktop app. This is a viewer, not a client: the GUI-side work listed above is not available over HTTP.
- Request and reply only: a browser must ask again to see a change. Pushed updates come with the first real client.
- One session serves one Document to one user. Concurrent edits are serialised by a lock, which is not collaboration.
- Reaching it from another device means a port forward or the tunnel. Network access with real authentication needs its own decision.
- ADR 0002's claim that the GUI never runs a rebuild is not yet true; the repo copy of ADR 0002 gains a note listing what still runs on the GUI side.
