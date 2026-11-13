# 11. A Mate reaches sketch geometry by sketch entity id

Date: 2026-11-13

## Status

Accepted (Adam, grill of 2026-11-13). The signature check in the Decision is Claude's proposal and is open to overrule.

## Context

A Sketch instance places a Part Studio sketch in an Assembly so parts can be mated to it. A Mate to a point or line of that sketch must store a key that survives a rebuild of the sketch.

[ADR 0003](0003-element-naming-port-of-freecad-element-map.md) gives faces, edges and vertices an Element name built from feature history, and every other mate end uses one. A published sketch (`SketchOut` in `freeshape/engine/contract.py`) carries Element names only for its region compound, which is built from closed, non-construction geometry. Construction lines and free points have no Element name. A layout sketch is mostly construction lines and points.

The same published sketch already carries the whole solved sketch, in which every point and entity has an id the sketch assigned when it was drawn (`p4`, `l7`).

Alternatives considered:

- **Element names for all sketch geometry.** One naming scheme everywhere. It changes the engine contract and the naming code, and it changes the published hash of every sketch, which the frozen goldens guard.
- **Existing Element names only.** No new scheme, but construction geometry and lone points cannot be mated to, which rules out most layout sketches.

One fact limits the chosen key. `Sketch.from_dict` restarts its id counter at the highest id present plus one. Deleting the highest-numbered entity, saving, reopening and drawing a new entity gives the new entity the old id.

## Decision

A mate end on sketch geometry stores the Sketch instance, the sketch entity id and a role: point, end 1, end 2, midpoint or centre. The connector frame is computed from the published sketch: its origin at the role's position, its z axis along the sketch plane normal, its x axis along the line or, for a point, along the sketch's x direction.

The mate end also stores the entity type and a geometric signature. The id is tried first. If the entity is missing, or its type differs from the stored one, the Mate shows a named error and is left out of the solve; it is not deleted and not re-attached by guesswork.

Element names stay the only key for faces, edges and vertices of parts. Sketch entity ids are used for Sketch instances only.

## Consequences

- Construction lines and free points can be mated to with no change to the engine contract or to any sketch's published hash.
- FreeShape has two keys for a mate end. Code that reads a mate end must branch on its kind, as it already does for a named mate connector.
- An id reused by an entity of the same type, after the delete, reopen and redraw sequence above, attaches the Mate to the new entity without an error. The signature can warn but cannot prove which entity was meant. Storing the counter in the sketch would close this; it is deferred because it changes every sketch's hash.
- Bringing a sketch into another Part Studio by Derived does not carry these mate ends; that case is out of the Sketch instance run.
- If element naming is later extended to all sketch geometry, a new ADR supersedes this one and Assembly files need a migration from entity id to Element name.
