import { Vector3 } from 'three'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PointerModule } from '../input/pointer'
import { wireSelection } from '../selection/aimCommit'
import { SelectionStore } from '../selection/store'
import { fixture } from '../test/fixtures'
import { picksOf } from '../tools/picks'
import { ViewCamera, STANDARD_VIEWS } from '../viewport/camera'
import type { StandardView } from '../viewport/camera'
import { PickIndex } from '../viewport/pick'
import { buildScene } from '../viewport/scene'
import type { StudioOutputs } from '../wire'
import { regionsOfSketch, visibleRegions } from './regions'

// The regions of a committed sketch in the Part Studio view (card I7.13): the Bracket profile's one region, found on its
// plane, picked by a pen Commit, and sent on as a pick of kind "region".

/**
 * The Bracket's profile sketch alone and shown: the part is left out (the Extrude built from the sketch is mid-plane, so
 * its solid hides the region from every side, as it should), and the eye is open on the sketch (the fixture has it
 * closed, as the app does once a feature uses it). It stands for a sketch committed in an empty Part Studio.
 */
function shown(): StudioOutputs {
  const { outputs } = fixture('bracket')
  return { ...outputs, parts: [], features: outputs.features.map((f) => (f.id === 'F1' ? { ...f, hidden: false } : f)) }
}

interface Rig {
  canvas: HTMLCanvasElement
  view: ViewCamera
  selection: SelectionStore
  pointer: PointerModule
  index: PickIndex
  out: StudioOutputs
  fire: (type: string, init: PointerEventInit) => void
}

function rig(view_: StandardView, out = shown()): Rig {
  const built = buildScene(out)
  const view = new ViewCamera()
  view.resize(1024, 768)
  view.setBack(STANDARD_VIEWS[view_])
  view.fit(built.fitBounds)
  const index = new PickIndex(out, built)
  const host = { view, pickPoint: () => null, modelCentre: () => new Vector3(), fit: vi.fn(), requestRender: vi.fn() }
  const canvas = document.createElement('canvas')
  canvas.setPointerCapture = (() => {}) as unknown as typeof canvas.setPointerCapture
  document.body.append(canvas)
  const pointer = new PointerModule(canvas, host)
  const selection = new SelectionStore()
  wireSelection(pointer, selection, (x, y, kind, filter) => index.pick(view, x, y, kind, filter))
  const fire = (type: string, init: PointerEventInit) => canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, ...init }))
  return { canvas, view, selection, pointer, index, out, fire }
}

let r: Rig
afterEach(() => {
  r.pointer.dispose()
  r.canvas.remove()
})

/** A spot inside the profile's L: the foot of the L, 30 mm along and 3 mm up (its centroid lies in the notch, outside it). */
const INSIDE = [30, 0, 3] as const

const pen = (rg: Rig, x: number, y: number) => {
  for (const type of ['pointerdown', 'pointermove', 'pointerup']) rg.fire(type, { pointerId: 7, pointerType: 'pen', clientX: x, clientY: y, pressure: type === 'pointerup' ? 0 : 0.5 })
}

describe('the regions of a sketch', () => {
  beforeEach(() => {
    r = rig('front')
  })

  it('are found from the sketch: the profile has one region, which agrees with the server on area and centroid', () => {
    const sk = r.out.sketches.find((s) => s.id === 'F1')!
    const shape = regionsOfSketch(sk)!.shapes
    expect(shape.map((s) => s.index)).toEqual([0])
    expect(visibleRegions(r.out).map((x) => x.sketch)).toEqual(['F1'])
    // a sketch the eye has closed has none: it is neither drawn nor picked
    expect(visibleRegions(fixture('bracket').outputs)).toEqual([])
  })

  it('a pen Commit inside the region selects that region', () => {
    const sk = r.out.sketches.find((s) => s.id === 'F1')!
    const at = r.view.project(INSIDE)
    pen(r, at.x, at.y)
    const items = r.selection.getState().items
    expect(items.map((i) => [i.kind, i.part, i.index, i.name])).toEqual([['region', 'F1', 0, sk.regionNames[0] ?? null]])
    // the pick a tool sends: the sketch's id and the region's number from 1
    expect(picksOf(items)).toEqual([{ part: 'F1', kind: 'region', index: 1 }])
    // and a pen lifted beside the region does not select it
    r.selection.clear()
    pen(r, 3, 3)
    expect(r.selection.getState().items).toEqual([])
  })

  it('a selected region stays selected over the rebuild that follows a commit, and goes when its sketch does', () => {
    const at = r.view.project(INSIDE)
    pen(r, at.x, at.y)
    r.selection.reconcile(r.out)
    expect(r.selection.getState().items).toHaveLength(1)
    r.selection.reconcile({ ...r.out, sketches: [] })
    expect(r.selection.getState().items).toEqual([])
  })
})
