# 9. Tab data goes through a Document store

Date: 2026-11-13

## Status

Proposed (written by Claude while planning the Parity Sweep; Adam to accept or change)

## Context

[ADR 0001](0001-document-is-a-folder-under-git.md) made a Document a folder under git. The glossary records that a self-hosted cloud version will keep Documents in a database, one record per Tab. A scan of `master` on 2026-11-13 found that nothing stands between the code and the folder: tab files and `document.json` are read and written directly in about eight modules (`doc_folder.py`, the four tab classes, `blobstore.py`, `doc_merge.py`, the engine, and about 17 sites in `ui/mainwindow.py`). Binary data is kept three ways: base64 inline in Assembly and Drawing files, an inline dict for imported STEP data, and a content-addressed blob folder for Python features. Swapping the folder for a database would mean editing every one of those sites.

Alternatives considered:

- **Leave it until the cloud work starts.** No cost now, but every feature added meanwhile adds call sites, and the live runs are adding them.
- **A virtual filesystem** (paths stay, the backend changes). Keeps every call site, but keeps paths as the identity of a tab, which ADR 0001 already replaced with tab ids.
- **Put git behind the interface too.** Versions and Workspaces would then need a second implementation before anything could ship. Too much for one step.

## Decision

All Document data goes through one interface, `DocumentStore`: read and write the manifest; list, read, write and delete a tab's data by tab id; read and write a blob by content hash.

- Tab classes gain `from_data(bytes)` and `to_data()`. `load(path)` and `save(path)` remain for loose files and are built on them.
- `FolderStore` holds today's on-disk behaviour unchanged, byte for byte. `MemoryStore` holds everything in memory and exists to prove the interface is complete.
- All binary data lives in the store's blobs, addressed by hash. Tab data holds hashes only.
- Git stays outside the interface. Versions, Workspaces and merge work on a `FolderStore`'s folder and report a named error on any other store.
- The main window reads and writes a Document's tabs through the Document, never by building paths.

## Consequences

- A database store is one new class, plus a decision on how Versions and Workspaces work without git. That decision is deferred and will need its own record, superseding part of ADR 0001 for that deployment.
- One file-format migration: Assembly and Drawing files stop carrying inline geometry. Old files still load.
- Loose files keep working, so there are two ways to hold a tab until loose files are retired.
- A blob that no tab references is not deleted. Clean-up is future work.
