# 4. A Context stores captured shapes, not a pointer into history

Date: 2026-11-12

## Status

Accepted

## Context

Edit in context shows a part's neighbours from an Assembly inside its Part Studio, frozen until the user updates. Under ADR 0001 a save writes files only and only "Create version" makes a commit, so the Assembly's state at capture time usually exists nowhere in git. A frozen Context must either carry its geometry or force a commit to exist. Under ADR 0002 a tab's outputs are already plain data: BREP bytes, a content hash and element names.

Alternatives considered:

- **Pointer to a Version.** The Context stores Assembly tab id, Version and instance; neighbours are rebuilt from that Version on open. Small files, but every capture adds a Version to the list and opening needs workers that rebuild old tab states.
- **Pointer to a hidden commit.** As above under a private ref the Versions list does not show. No clutter, but a second kind of snapshot to explain and garbage-collect, capture must commit unsaved Assembly edits, and open still rebuilds.
- **Pointer plus an untracked shape cache.** Fast when warm, with the most machinery of the options.

## Decision

A Context stores, for each neighbour, its BREP, element names and position relative to the edited part instance. It also stores the Assembly tab id, the instance id it was captured from, and the content hashes of the sources.

- "Update context" reads the Assembly's current published output and overwrites the stored shapes, positions and hashes. Nothing else changes a Context.
- A Context is out of date when a captured neighbour's shape hash, or its position relative to the edited instance, differs from the stored one. The edited part's own shape is ignored, so editing in context does not mark the Context stale. Staleness is shown, never acted on.
- References to Context geometry follow ADR 0003: a name hit resolves, a missing name turns the feature red.

This is FreeCAD's Frozen bind mode (`src/Mod/PartDesign/App/ShapeBinder.cpp`).

## Consequences

- Capturing needs no commit and opening needs no rebuild. A Context still displays after its Assembly is deleted or broken.
- Binary geometry is committed with the Document and grows with each update that is followed by a Version.
- Stored element names are those produced at capture. A change to the naming format needs an update of each Context to refresh them; a pointer would have rebuilt them.
- The Context format is a Document file format and joins the link-format migration chain.
