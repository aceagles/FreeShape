# 1. Document is a folder under git

Date: 2026-11-12

## Status

Accepted

## Context

FreeShape has no Document. Each tab is a loose `.fshape`, `.fasm` or `.fdwg` file that finds the tabs it links to by uid and path. There are no versions, no branches, and nowhere to keep shared variables or pinned links. Onshape-style top-down design (Derived, Variable Studios, pinned cross-Document links) needs one unit that holds related tabs and their shared history. The owner wants that unit to stay self-hosted: a folder they own, which plain git and their own git server (Gitea) can handle.

Alternatives considered:

- **Single container file** (one archive holding every tab). Easy to move as one item, but every save rewrites the whole thing, git sees one opaque blob, and per-tab history, diff and merge are lost.
- **Loose files, as today.** No new format, but no Versions, no Workspaces, and no stable home for links; renaming or moving a file breaks its dependents.
- **Tab-level merge done by hand, with `-merge` attributes only as a hint.** Rejected in favour of using git's own merge machinery. If tab files merged as text, a merge done outside the app, on Gitea or in a terminal, could interleave two feature lists into a half-merged tab that still parses.

## Decision

A Document is a folder holding `document.json` (format, version, document id, name, and tabs with stable tab ids, kinds, file names and display names) plus one file per tab, with a git repository underneath.

- Save writes files only. "Create version" makes a commit plus an annotated tag carrying the version's name and note. A Workspace is a git branch.
- On init, the Document writes `.gitattributes` marking every tab file and `document.json` as `-merge`. Any merge, in the app or with stock git, then gives whole-file conflicts and never a text merge.
- The app merges with `git merge-tree --write-tree`. The user picks ours or theirs per conflicting tab. The app merges the manifest as a union by tab id and checks for dangling links before it commits.
- Every git call goes through one hardened wrapper. It overrides hooks, fsmonitor and protocol through `-c`, puts `--` before user-derived arguments, validates names with `git check-ref-format`, and needs git 2.38 or newer.
- Links within a Document are live and resolve by tab id. Links to another Document pin a Version.

## Consequences

- Versions, Workspaces, push and pull come from git. A Document folder can go to any git server with no FreeShape-specific tooling.
- Merges are always per tab, and never mix two feature lists. Feature-level merge is out of scope; if it is ever wanted, it needs a custom merge driver.
- The app now depends on a `git` binary (2.38 or newer). Packaged builds must bundle git or switch to dulwich; this is parked on the pre-launch checklist.
- A Document folder from someone else is untrusted input. The wrapper's overrides and the hostile-config test exist so that opening one cannot run code from its `.git/config`.
- Tab files gain stable tab ids. Links and the existing part-id migration share one ordered link-format migration chain.
- In current code, the class `Document` means a Part Studio. The class should be renamed once the folder-level Document lands, to avoid two meanings.
