# 15. Sketch solving stays on the server during a drag

#exclude-all-tags

Date: 2026-11-15

## Status

Accepted (Adam, 2026-11-15)

## Context

Dragging sketch geometry solves the constraints on every pointer move. In the desktop program the solver (`freeshape/sketch.py`, numpy only, no Qt) runs in the GUI process. In the browser client (ADR 0013) the model lives on the backend, so each move either crosses the network or needs a solver in the browser.

The solver was timed on 2026-11-15 in the gate image, simulating a drag: 0.6 ms for a rectangle, 2.9 ms for four linked rectangles (23 constraints), 7.3 ms for twelve rectangles and two circles (60 constraints), 9.2 ms for a twelve-sided polygon. The network round trip from the iPad through the tunnel was not measured.

Alternatives considered:

- **The same Python solver in the browser through Pyodide.** No network on drags and one code base. A download of roughly 10 MB, slower execution, and a runtime to keep working on iPad Safari.
- **A JavaScript port of the solver.** Fastest drags. Two solvers to keep in step.
- **FreeCAD's planegcs as WebAssembly** for drags, with the Python solver for the stored result. A different algorithm, so the sketch can jump when the server's answer replaces the browser's.

## Decision

The solver runs only on the backend. While dragging, the browser sends each pointer move over the WebSocket (ADR 0014) and draws the solved sketch that comes back. It shows the dragged item at the pen position at once, and sends only the latest position when moves arrive faster than answers.

## Consequences

- One solver: the iPad and the desktop cannot disagree about a sketch.
- Drag feel depends on the round trip. The first sketch slice measures it on the iPad. If it is too slow to use, the fallback is the same solver in the browser through Pyodide, which needs a new decision record.
- The backend needs a sketch editing session: open a sketch, apply a move with drag weights, add or remove geometry and constraints, and commit. Today a sketch can only be replaced whole.
- The client does not work offline.
