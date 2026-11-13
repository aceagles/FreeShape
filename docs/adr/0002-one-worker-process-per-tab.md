# 2. One worker process per tab behind an engine interface

Date: 2026-11-12

## Status

Accepted

## Context

Every FreeShape tab rebuilds on the GUI thread, so a large Part Studio freezes the UI. Linked tabs notice an upstream change only when the user switches tabs, and a tab's rebuild cache keys only on its own feature list, ignoring inputs from other tabs. Live propagation, a responsive UI and a future self-hosted cloud all need tab regeneration separated from the GUI, behind a contract that does not assume shared memory.

Alternatives considered:

- **In-process Link graph first, worker processes later.** Gets live propagation sooner, but the GUI still blocks. It also delays the hard part, moving shapes across a process boundary, which is where the contract is most likely to be wrong.
- **Keep the tab-switch pull.** No new machinery, but dependents stay stale until viewed, and "only rebuild what changed" cannot be expressed.
- **Design the contract to become a service per tab from the start** (RPC, remote workers). Matches the cloud goal, but adds network, auth and deployment concerns that this work does not need yet.

## Decision

Each tab regenerates behind an engine interface: `submit_edit(tab, edit)` → `outputs(tab)`.

- Outputs are plain data: parts as BREP bytes plus a content hash, sketches, variables, mate connectors, and meshes as float arrays. No kernel objects cross the interface.
- A Link graph, recorded by the core LinkResolver, rebuilds only the dependents whose consumed input hashes changed. The rebuild cache key is hash(feature JSON prefix + consumed input hashes).
- An in-process executor comes first. The process executor that follows runs one `multiprocessing` spawn worker per tab, with crash detection and restart.
- The contract stays the same if a tab's worker later moves to another machine.

## Consequences

- The GUI never runs a rebuild, and a crashed worker takes down only its own tab.
- Shapes cross the boundary as serialised BREP, which costs a round trip per output. The process-executor issue includes a benchmark of that cost on the examples.
- Both executors run the same tests, so the in-process executor stays a debugging aid and does not become a second code path that drifts.
- Picking and selection in the viewport must map back through element names, because the GUI no longer holds the kernel shapes.
- Packaged builds need `multiprocessing.freeze_support` and a frozen-worker smoke test; this is parked on the pre-launch checklist.
- A remote or cloud executor is future work. It can reuse this contract, but it will still need transport, auth and deployment decisions.

## State of the implementation

The Part Studio tab is an engine client (`freeshape/ui/partstudio_engine.py`). Its `Document` keeps the feature list, undo and save, and sends a rebuild to the worker process. Three things still run on the GUI side, because the engine contract has no output for them yet:

- The cheap non-solid features (sketch, plane, variable) are re-run on the GUI thread against the parts the engine returned. The 2D solver and the region faces need this, so the sketcher and the plane list keep working.
- Feature dialogs build their previews with `Document.compute` on the GUI thread.
- Opening a file builds it once in `Document.load`, on the GUI thread.
