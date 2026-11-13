# 3. Element naming is a port of FreeCAD's element map

Date: 2026-11-12

## Status

Accepted

## Context

FreeShape finds picked faces and edges again after a rebuild by geometric similarity alone. After an upstream change, a reference can silently attach to the wrong face. Part ids come from a replay-wide counter, so inserting an upstream feature renumbers later parts. Top-down design (Derived, mate connectors) is unusable unless references either land on the same element or fail loudly.

Alternatives considered:

- **OCCT TNaming / OCAF.** Kernel-native naming, maintained upstream. It needs an OCAF document per studio and a different data model from FreeShape's feature replay. It was rejected without a probe, as an accepted known risk: FreeCAD, the closest comparable project, chose its own element map over TNaming. The adversarial plan review asked for a timeboxed TNaming probe first; the owner declined.
- **Signatures only, with loud failures.** Keep geometric matching but turn low-confidence matches into errors. This is cheap, but it cannot tell two similar faces apart after a topology change, so it either fails too often or still guesses.
- **Named miss falls back to the geometric matcher.** Rejected as a policy. If a name misses and the signature matcher takes over, the silent wrong match comes back. So the rule is: a named miss is an error, and the signature matcher is used only for legacy references that carry no name.

## Decision

Port the approach of FreeCAD 1.0's element map.

- A core naming module holds a shape plus a map from each face, edge and vertex to an element name. Each runner hands its OCCT maker to a naming step, which builds names from the maker's history (`Generated`, `Modified`, `IsDeleted`) with the feature id as the operation tag.
- There is no string hasher. Names are recomputed on every rebuild.
- References store `{element name, signature}`. A name hit resolves. A name that is present but missing is an error: the feature turns red and names the lost element. A reference with no name (legacy) uses the signature matcher and is flagged as recovered.
- Part ids become `<feature id>/<output index>`, which cannot collide with the old `<fid>:<counter>`. Studios persist an old→new alias table so unopened assemblies and drawings keep resolving.

## Consequences

- Upstream edits no longer move references silently. A reference either lands on the element with the same name or the feature goes red.
- The port is large (FreeCAD's element map is about 10k lines of C++), so it is wired in by feature family, one issue each. Scope should be watched per family, and any family that ships signature-only should be recorded; this is on the pre-launch checklist.
- Existing files still open: legacy references recover through the signature matcher with a visible marker, and old part ids resolve through the alias table.
- Assembly connectors and drawing dimensions use the same resolver and policy, which removes the duplicate matcher in the assembly code.
- If the port proves unworkable for a family, revisiting TNaming means a new ADR that supersedes this one. No probe data exists yet to inform that choice.
