// @vitest-environment node
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { decodeOutputs, isStudio } from '../wire'
import type { Part, PartMesh, StudioOutputs } from '../wire'
import { ViewCamera } from './camera'
import { STANDARD_VIEWS } from './camera'
import { PickIndex, rayPoint, viewRay } from './pick'
import { buildScene, ghostParts } from './scene'

// Card F3: while a feature dialog is open the view draws the dialog's base model (what its picks resolve against) and
// the preview over it as a translucent ghost. What the pen hits is the base: the ghost is drawn and never picked.

const bytes = readFileSync(new URL('../../fixtures/bracket.outputs.fsw', import.meta.url))
function bracket(): StudioOutputs {
  const out = decodeOutputs(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer)
  if (!isStudio(out)) throw new Error('fixture is not a studio')
  return out
}

/** A preview whose one part is the bracket's, moved by `dx` along x (another BREP: another hash). */
function movedPreview(base: StudioOutputs, dx: number): StudioOutputs {
  const part = base.parts[0]!
  const mesh = part.mesh as PartMesh
  const positions = Float32Array.from(mesh.positions, (v, i) => (i % 3 === 0 ? v + dx : v))
  const moved: Part = { ...part, hash: `${part.hash}-moved`, mesh: { ...mesh, positions } }
  return { ...base, parts: [moved] }
}

/** Screen position of the centroid of the part's first triangle. */
function firstTriangle(view: ViewCamera, mesh: PartMesh): { x: number; y: number } {
  const c = [0, 0, 0]
  for (let k = 0; k < 3; k++) for (let a = 0; a < 3; a++) c[a]! += mesh.positions[3 * mesh.indices[k]! + a]! / 3
  return view.project([c[0]!, c[1]!, c[2]!])
}

describe.each([
  [1024, 768],
  [1366, 1024],
])('the dialog view at %i x %i', (w, h) => {
  it('picks the base model; the preview ghost over it is drawn and never picked', () => {
    const base = bracket()
    const preview = movedPreview(base, -70)
    const built = buildScene(base, { ghost: preview })
    expect(built.stats.ghostParts).toBe(1)
    expect(built.root.getObjectByName('preview-ghost')?.children.length).toBe(1)
    const view = new ViewCamera()
    view.resize(w, h)
    view.setBack(STANDARD_VIEWS.front)
    view.fit(built.fitBounds)
    const index = new PickIndex(base, built)
    const part = base.parts[0]!
    // on the base: a face of the base part, with the base's Element name
    const on = firstTriangle(view, part.mesh as PartMesh)
    const hit = index.pick(view, on.x, on.y, 'mouse', ['face'])
    expect(hit?.kind).toBe('face')
    expect(hit && 'part' in hit ? hit.part : null).toBe(part.id)
    // on the ghost only (moved clear of the base): nothing is picked, and no ray stops on it
    const off = firstTriangle(view, preview.parts[0]!.mesh as PartMesh)
    expect(index.pick(view, off.x, off.y, 'mouse', ['face'])).toBeNull()
    expect(rayPoint(built.root, viewRay(view, off.x, off.y))).toBeNull()
    built.dispose()
  })
})

it('the ghost holds only what the preview changed: a part the base has as it is is not drawn twice', () => {
  const base = bracket()
  expect(ghostParts(base, base)).toEqual([])
  expect(ghostParts(movedPreview(base, 5), base).map((p) => p.id)).toEqual([base.parts[0]!.id])
  // a preview that adds a part (an Extrude on an empty base) ghosts it
  const empty = { ...base, parts: [] }
  expect(ghostParts(base, empty).length).toBe(base.parts.length)
  expect(buildScene(base).stats.ghostParts).toBe(0)
})
