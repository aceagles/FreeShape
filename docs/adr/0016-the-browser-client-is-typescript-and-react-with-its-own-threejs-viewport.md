# 16. The browser client is TypeScript and React with its own three.js viewport

Date: 2026-11-15

## Status

Accepted (Adam, 2026-11-15)

## Context

The repo has no JavaScript build and the gate image has no Node. The existing viewer is one page of plain WebGL with no library. The first milestone of the browser client (ADR 0013) is a full editor: a sketcher, feature dialogs generated from the dialog schema, a feature tree, a numeric pad and a tablet layout. Most of it will be written by agents, so it needs checks that fail when the code is wrong.

The desktop viewport is fixed-function OpenGL and does not port. The sketcher draws inside the 3D view, so the viewport has to own pointer handling for the pen and fingers.

Alternatives considered:

- **No build step**, plain ES modules with types checked from JSDoc comments. Nothing added to the image; weaker tooling for a client this size.
- **Vite and TypeScript with no framework.** Fewer dependencies; more hand-written plumbing for forty-odd dialogs and a tree.
- **Adopt three-cad-viewer** (MIT) for the 3D view. Quickest to a model view with a tree and clipping. Its toolbar and mouse controls may fight a touch interface and the sketcher, and whether its picks map to element names was not verified.
- **Extend the plain WebGL viewer.** No dependency; wide lines, fast picking and transparency all written by hand.

## Decision

The browser client is TypeScript, built with Vite, with React for panels and dialogs. The 3D viewport is our own, on three.js for drawing and three-mesh-bvh for picking, with navigation and picking ported from `freeshape/ui/viewport.py`.

## Consequences

- Node enters the project as a separate CI job for type checking, tests and the build. The Python gate image is unchanged.
- `freeshape serve` needs the built client. How the built files reach it (committed, or built at install) is settled in the first slice.
- We write and maintain the scene, navigation, picking and the sketch overlay ourselves. In exchange nothing in the view is outside our control.
- Ideas are taken from three-cad-viewer's data format and from Shapr3D's and Onshape's tablet interfaces. No code is taken from AGPL projects such as Chili3D.
