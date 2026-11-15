# 13. iPad editing uses a browser client, not the streamed desktop

#exclude-all-tags

Date: 2026-11-15

## Status

Accepted (Adam, 2026-11-15)

## Context

Adam wants to edit models on an iPad with fingers and an Apple Pencil. The first plan (decision D1 in the Drive decision log) streamed the Qt desktop window from a Selkies container and assumed Selkies would pass real touch and stylus input to Qt.

The iPad input spike (`docs/spike/ipad-input-findings.md` on `spike/ipad-input`) found:

- The iPad browser tells pen from finger and reports pen pressure. It reports no pen hover, and never reports pen and finger at the same time.
- Selkies sends finger, pen and mouse as the same mouse message in both of its touch modes. It has no touch or pen message, so Qt can only see a mouse. This was read from the Selkies code; the Qt side was not measured.

Three routes were weighed:

- **Selkies with mouse mapping.** A new navigation style maps the mouse events Selkies already sends (two-finger scroll, pinch as Ctrl+scroll) to pan and zoom, plus a compact Qt layout. Days of work and the whole existing UI. Pen and finger stay indistinguishable and the video stream stays soft and laggy.
- **Selkies with a side channel**, or another streamer with pen passthrough, to get real pen and touch events into Qt. The side channel is browser pointer code plus a socket into the app, which is the input half of a browser client, and Qt would still need its own gesture layer.
- **A browser client** over the engine contract and `freeshape serve` (ADR 0002, ADR 0010). Native pen and touch input and sharp rendering, at the cost of rebuilding the interface: `freeshape/ui/` is about 27,600 lines.

`ROADMAP.md` already names editing in the browser as stage 4 and says the streamed desktop "is not the goal".

## Decision

The iPad client is a browser client: a Python backend holding the model and a browser front end, for one user. Adam chose it over the cheaper Selkies mouse-mapping route, knowing the cost.

The first milestone is a full part from nothing on the iPad: sketch, create features, and work the feature tree, plus creating and opening Documents and tabs, and viewing and moving assemblies.

The Qt desktop program stays the full modeller and is left as it is, apart from honouring the Document lock.

## Consequences

- The work is roadmap stage 4 done for one user, ahead of stages 1 to 3. Creating and opening Documents brings in the first part of stage 1 without login.
- Everything the Qt window computes itself has to be offered by the backend before the browser can do it: feature previews, turning a pick into a stored reference, interactive sketch solving, and assembly editing.
- Two interfaces exist until the browser reaches parity. Variables, configurations, Versions, Workspaces, mates, drawings and the less common sketch tools stay desktop-only after the first milestone.
- The same Document must not be edited by both programs at once. A lock file in the Document folder allows one editor at a time, with an explicit take-over.
- Design follows the measured input limits: the pen aims while down and commits on release, and nothing relies on hover or on pen and finger together.
- The streamed desktop remains available for anything the browser client cannot yet do.
