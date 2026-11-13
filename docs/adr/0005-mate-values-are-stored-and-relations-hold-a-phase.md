# 5. Mate values are stored in the Assembly, and a Relation holds a phase

Date: 2026-11-13

## Status

Accepted

## Context

FreeShape is adding Relations (gear, rack and pinion, screw, linear) and Mate limits to its own assembly solver, instead of swapping to OndselSolver (see the Roadmap, decided 2026-11-13). A Mate today stores two connector frames, `flip`, `offset` and `angle`; the pose lives only in each instance's transform.

A gear Relation cannot be checked from a pose. With a 20-tooth gear driving a 40-tooth gear, one full turn of the input puts the output at 180°, and the input's frames are identical to the start, where the output was at 0°. The solver needs to know how far each Mate has travelled, counted past a full turn. A limit of 0 to 720° on a handle needs the same number, and so does a headless test that turns a Mate to 450°.

A second problem: gears are placed by hand so the teeth mesh, for example input 0°, output 7.5°. A pure ratio would pull the output back to 0° when the Relation is added.

Alternatives considered:

- **Value in memory, recaptured on load.** No file change. Turn counts reset on every open, limits past one turn cannot work, and a ratio edit has no absolute value to apply to.
- **No value; relate velocities while dragging.** Simplest. A solve that is not a drag cannot restore gear sync, and limits and Animate have nothing to build on.
- **No phase; related value = ratio × value.** Nothing hidden in the file. Adding a Relation moves parts, and mesh has to be set through a Mate's angle setting.
- **Phase as an editable dialog field.** More control than Onshape offers; put aside until someone needs it.

## Decision

Each Mate stores one Mate value per degree of freedom it leaves free. Zero is the pose where its two connectors are aligned. After every solve the value is measured from the pose, and a rotation is unwrapped to the turn nearest the stored value.

A Relation stores a phase and holds: related value = ratio × value + phase. Adding, editing or unsuppressing a Relation captures the phase from the current values and moves nothing. The phase is not shown.

Editing a Mate's connectors, flip or angle resets its values to the measured pose and recaptures the phase of its Relations.

A limit is the opposite case and is always enforced: it has no phase to absorb the current pose, so setting one moves the part to the nearest limit.

## Consequences

- The Assembly file format gains stored state that cannot be rebuilt from the pose alone. It joins the assembly migration chain; older files load with values measured from their saved pose, inside one turn.
- The stored value and the pose can disagree only after a jump of more than half a turn in one solve. Drive to value and Animate therefore move in bounded steps.
- Two files with the same pose can behave differently, because their turn counts or phases differ. The values must be visible in the UI so this can be seen.
- A Relation's phase is hidden state. If meshing by hand proves too coarse, making it an editable field is an addition, not a format change.
- A solver swap later would have to carry Mate values and phases, or lose turn counts.
