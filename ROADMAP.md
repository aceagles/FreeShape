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
| Editing in the browser | Milestone 1 done: Part Studio and sketches, Documents and tabs, Assembly view and move | ADR 0013 to 0016 |
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

This is the largest stage by a long way. Milestone 1 is done (see "Browser client: milestone 1" below); until the rest is, the
streamed desktop window covers the gap.

### 5. Working together

Sharing a Document with named people, with view and edit rights. Then several people in one Document at once,
each seeing the others' changes.

### 6. Easy to host

One container setup that a person can run at home or a small company can run on its own server, with backups
that are plain files. It should work with a git server you already run.

## Browser client: milestone 1

Milestone 1 lets one person make a part from nothing on an iPad with fingers and an Apple Pencil. `freeshape serve`
is one aiohttp server with a WebSocket for one user, and the Browser client is TypeScript and React under `web/`
(ADR 0013 to 0016). The slices are named as in the plan.

### Covered

- **A served model on the iPad.** The server binds to loopback and accepts one `--allow-origin`. The client draws a
  Part Studio, with the Pencil, one-finger orbit, two-finger pan, zoom and roll, and the tablet layout.
- **Documents and tabs.** List, create, open and close Documents; add, rename and delete tabs. One editor per
  Document through a lock file, with an explicit take-over; the desktop honours the same lock. The server autosaves
  and offers recovery.
- **The feature list.** Features with type icons, suppress, rename, delete, roll back, undo and redo, and a Parts
  list with colour and hide.
- **Selection with pen and finger.** The pen Aims while down and Commits on release; a finger tap selects. Single
  mode by default, Multi mode on request.
- **Edit a feature.** The dialog is drawn from the server's description of the feature, with a live preview, the
  on-screen number pad and Drag handles on the Extrude depth and Plane offset.
- **Create features.** Every feature whose dialog uses the standard field types (number, choice, check, selection)
  can be created from the tool strip, with pinned tools.
- **Sketch with the Pencil.** A sketch opens in a 2D view. Tools: line, corner and centre rectangle, circle, arc,
  point, construction toggle, corner fillet, trim and use edge. All constraint types and all dimension types.
  Geometry drags with the pen or a Drag handle, and the server solves each step. A part can be made from a new
  Document with no desktop step.
- **Assemblies: view and move.** An Assembly tab draws its instances, with an eye, a fixed toggle and delete per
  instance, and lists mates. A gizmo on a free instance moves it by Drag handle; a drag is one undo step.

Checks: `scripts/smoke/sw1_*.py` to `sw8_assembly.py` and `sh scripts/web/e2e.sh` (S1 to S8 under WebKit).

### Desktop only

- Variables, configurations, Versions and Workspaces.
- Assembly mates (adding or editing), inserting parts, standard parts and the component library.
- Drawings, nesting, the export profile editor, Compare Versions and merge.
- Sketch spline, ellipse, text, slot, polygon, offset, mirror, patterns and extend.
- Features whose dialogs need a hand-built field type (Derived, Variable Studio reference, split tools and similar);
  the strip shows them disabled.
- Login, sharing, permissions and several people in one Document (stages 1 and 5).
- Pen pressure, offline use, and any gesture that needs pen and finger together.

### Next

Known gaps in what milestone 1 does:

- The sketch strip has no pinned tools.
- Sketch entities cannot be picked in the Part Studio view.
- Crossing sketch loops do not form regions.
- A region can be picked only where it is visible.
- An Assembly move solves in about one second per step, so a drag lags behind the finger.
- Two browser pages editing one Document: the later edit wins with no warning.
- The desktop does not see a browser autosave.
- Sketch snapping in the client covers only the listed kinds (existing point, line or circle, origin, model edge,
  horizontal or vertical alignment); other desktop inference is not ported.

Still to do on the iPad: judge the feel of drags and the target sizes by hand, and measure the round trip through
the tunnel with the sketch solver under load.

Then the rest of this stage, in order: variables and configurations, mates and inserting parts, drawings.

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
