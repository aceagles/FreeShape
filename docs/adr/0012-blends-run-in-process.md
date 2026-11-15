# 12. Blends run in process

Date: 2026-11-15

## Status

Accepted (corpus result, card I8.6).

## Context

Fillet and variable-radius fillet run in the app process. Card I8.5 built a corpus of 40 fillet cases on swept and lofted bodies, including radii at 120 and 300 percent of the largest radius, and ran each case, the largest-radius search and every retry rung (QuasiAngular, Polynomial, SetParams with doubled tolerances, LimitTolerance on a copy) in its own child process with a timeout. The result is in `scripts/spikes/op2_fillet_corpus.result.txt`, identical on OCP 7.9.3.1 and 8.0.1.1:

`DECISION fillet_corpus cases=40 fail=14 crashes=0 rescued_by=none crash_guard=not_needed`

No case, search or rung ended in a signal, a non-zero exit or a timeout. The 14 failed rows fail with a status text, not a crash. No retry rung rescued any of them.

## Decision

Blends run in process. No helper process (`freeshape/blend_worker.py`) and no retry ladder are added.

## Consequences

A fillet that OpenCascade cannot build reports its status text and the feature goes into error, as before.

Revisit if a crash is reported: add the helper on the pattern of `freeshape/hlr_worker.py` (BREP bytes, edge indices and radii in; working size or status text out), and re-run the corpus to see whether a retry rung helps the failed rows.
