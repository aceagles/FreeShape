# 8. The pieces of a severed solid share pass-through stamps

Date: 2026-11-13

## Status

Accepted. Refines ADR 0003.

## Context

Under ADR 0003 a feature such as a fillet names its result with a pass-through stamp: every element of the part it runs on gets the feature's op code and tag appended, including elements it does not touch. FreeShape gives each solid its own part, so a cut that severs a part leaves two parts, and a later stamping feature runs on one of them. Elements on the other piece then lack the stamp, and every reference picked after that feature fails with "is gone" although the element exists. Any model with two or more later features on different pieces hits this (`tests/test_severed_solid_refs.py`, found in the Foundations run as finding F1).

FreeCAD has no such case: a PartDesign body stays one shape with one element map through every feature. FreeShape's part per solid is a departure from it, in Onshape's direction.

Alternatives considered (`afk-run/findings/F1-plan.md`, card F1.1):

- **Tolerant lookup.** Leave names alone and, on a miss inside a severed solid, compare names with pass-through stamps removed. A matching rule with no FreeCAD counterpart, and it weakens "a name resolves to the element of that name and to nothing else".
- **Accept the error.** The user re-picks after a severing cut.

## Decision

When a stamping feature runs on one piece of a severed solid, the sibling pieces (the part id without its `.<i>` suffixes and every id that extends it) get the same pass-through stamp. For naming, the pieces behave as the one shape they are in FreeCAD.

## Consequences

- Names of untouched elements are identical with and without the severing cut, so stored references resolve. The layout-then-split workflow that top-down design depends on is dependable.
- A piece's names mention features that never touched it. Deleting a fillet on one piece changes names on its siblings, as deleting it changes names across a whole body in FreeCAD.
- The exact-match rule of ADR 0003 is unchanged.
- Not covered: nameless (legacy) references on a severed solid, an Assembly connector or Drawing dimension whose element lands on the other piece, and which piece keeps the bare id when the cut moves (plan cards F1.3, F1.4, F1.5).
