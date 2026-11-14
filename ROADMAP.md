# Roadmap

This is the roadmap for this fork of FreeShape. It says where the fork is going, not when. Nothing here is a
promise, and the cloud stages in particular are aims with open questions.

## The aim: full circle, on your own server

Onshape showed what cloud CAD is good for: Documents that hold everything, versions and branches, links between
parts that update on their own, and work you can open from any machine. Its cost is that the server, the
account and your designs belong to someone else.

FreeShape took that way of working offline. This fork's aim is to bring it back to the cloud without giving up
ownership: **FreeShape Cloud**, a server you run yourself, with the desktop program still working on its own,
offline.

Two rules hold throughout:

- **The desktop program stays complete.** No feature needs a server, an account or a network.
- **Your data stays in formats you can read.** A Document is a folder of files under git today. A hosted
  Document must export to that folder and import from it without loss.

## Where things stand

The groundwork for a server is already in the desktop program. Each item has a decision record in
[docs/adr](docs/adr).

| Piece | State | Record |
|---|---|---|
| Document: a folder of tabs under git, with Versions and Workspaces | Done | ADR 0001 |
| One worker process per tab behind an engine contract, with no shared memory assumed | Done | ADR 0002 |
| Stable element names, so links survive edits upstream of them | Done | ADR 0003 |
| Links between tabs and between Documents, pinned to a Version | Done | ADR 0001, 0007 |
| Tab data read and written through a Document store, with folder and in-memory stores | Done | ADR 0009 |
| Engine outputs in a plain wire format (a JSON header plus raw buffers) | Done | ADR 0010 |
| `python -m freeshape serve <Document>`: one Document over HTTP on this machine only, no login | Done | ADR 0010 |
| A browser viewer for a served Document | Done, view only | ADR 0010 |
| Editing in the browser | Not started | |
| Login, sharing and permissions | Not started | |
| Several people in one Document | Not started | |

Today "FreeShape in the cloud" means the desktop window streamed from a container. It works for one person and
is not the goal.

## Stages toward FreeShape Cloud

Each stage is useful by itself, and each leaves the desktop program as it was.

### 1. A server for your Documents

Turn the single-Document `serve` command into a service that holds many Documents: list them, open them, view
them and download them from a browser. Add login, since it will no longer be bound to one machine.

Open questions: the login method, and whether the server keeps Documents as git folders or in a database.

### 2. A database store

Keep each tab as a record in a database, through the Document store the program already uses. The folder store
stays for the desktop.

Open question: how Versions and Workspaces work without git. This needs its own decision record and will
replace part of ADR 0001 for hosted Documents.

### 3. Desktop and server in step

Open a hosted Document in the desktop program, work on it offline, and send the changes back. Workspaces and
the per-tab merge the desktop already has are the starting point.

Open question: what happens when two people change the same tab while apart.

### 4. Editing in the browser

Grow the viewer into an editor, in the order that gives the most use for the least interface: variables and
configurations first, then the feature list and feature dialogs, then assemblies and mates, then the sketcher,
then drawings. The engine already does the rebuilding; the browser sends edits and draws the results.

This is the largest stage by a long way. Until it is done, the streamed desktop window covers the gap.

### 5. Working together

Sharing a Document with named people, with view and edit rights. Then several people in one Document at once,
each seeing the others' changes.

### 6. Easy to host

One container setup that a person can run at home or a small company can run on its own server, with backups
that are plain files. It should work with a git server you already run.

## Modelling work alongside

The cloud stages do not stop work on the CAD program. Known gaps, in no fixed order:

- **Mates:** tangent and width mates, per-axis offsets, subassemblies.
- **Axes:** construction axes in Derived, in published output and in Assemblies.
- **Variable Studio:** units, a better editor.
- **Packaged builds** of this fork for Windows, Mac and Linux. Today it runs from source.
- **Imported Documents:** cleanup and write-back.
- **Fixes carried as known issues:** drawing refresh for a non-active configuration; countersink sizes at M10
  and M12 against ISO 10642.

## How work is chosen

- **Take FreeCAD's answer before designing a new one.** Element naming, fasteners and gears are ports of
  FreeCAD code. This fork's own contribution is polish and top-down design.
- **Stay close to upstream.** Changes from [richiewillmott/FreeShape](https://github.com/richiewillmott/FreeShape)
  are merged in, and upstream's version of a feature is taken unless it is a noticeable step down.
- **Follow Onshape where it has an answer.** Names and behaviour match Onshape's, so what you know there
  carries over. The glossary is in [CONTEXT.md](CONTEXT.md).
