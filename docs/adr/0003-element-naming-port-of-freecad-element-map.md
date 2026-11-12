# ADR 0003 — Element naming is a port of FreeCAD's element map

* **Status:** accepted
* **Date:** 2026-11-12

## Context

A downstream feature — a fillet, a pocket, a mate — has to say which face or edge of an upstream
shape it acts on. Storing the kernel's index (`Face7`) breaks as soon as anything upstream changes,
because OCCT renumbers freely. Storing a geometric signature and re-finding the face by its shape
survives renumbering but cannot distinguish faces that look alike, and when it guesses wrong it
does so silently: the fillet lands on the wrong edge and the model is quietly incorrect.

FreeCAD 1.0 solved this with its *element map*: a shape carries, alongside its topology, a map from
each face, edge and vertex to a name built from the history of the operations that produced it. The
rules are written up in [docs/reference/element-naming.md](../reference/element-naming.md), read
from the reference source at `/opt/freecad-src` (tag 1.0.2, commit 256fc7ef).

## Decision

Port the approach of FreeCAD 1.0's element map.

* A core naming module holds a shape plus a map from each face, edge and vertex to an element name.
* Names are built from OCCT operation history — `Generated`, `Modified` and `IsDeleted` — with the
  feature id as the operation tag.
* There is **no string hasher**. Names are stored in full, and recomputed on every rebuild.
* A reference stores `{element name, signature}`:
  * a name that resolves in the rebuilt map **resolves**, and the signature is ignored;
  * a name that is present on the reference but **missing** from the rebuilt map is an **error**
    that names the lost element;
  * a reference with **no name** — a legacy reference saved before this change — falls back to the
    signature matcher and the result is flagged as **recovered**.
* Part ids become `<feature id>/<output index>`, and studios persist an old→new alias table so
  existing references keep resolving.

## Consequences

Dropping the string hasher means we never persist, reload or garbage-collect a hash table, and a
saved file never depends on one. The cost is longer stored names and a full renaming pass on every
rebuild.

Making a named miss an error rather than a fallback means an edit that destroys a referenced face
stops the rebuild and says which element was lost, instead of producing a plausible-looking wrong
result. Some edits that FreeCAD completes with a warning will fail for us until the user re-picks.

## Alternatives rejected

* **OCCT `TNaming` / OCAF.** The kernel's own topological naming framework requires an OCAF
  document per studio, which is a structural change to how a studio is stored. We have not probed
  it; passing it over without a probe is an accepted risk.
* **Signatures only, with loud failures.** Keeping the geometric matcher and merely making every
  ambiguous match an error does not work: a signature cannot tell two similar faces apart after a
  topology change, so the common case becomes a failure rather than a resolution.
* **A named miss falling back to the geometric matcher.** This looks like the forgiving option, but
  it reintroduces exactly the silent wrong match the whole change exists to remove.
