import { Vector3 } from 'three'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PointerModule } from '../input/pointer'
import type { NavHost } from '../input/pointer'
import { fixture } from '../test/fixtures'
import { ViewCamera, STANDARD_VIEWS } from '../viewport/camera'
import { PickIndex } from '../viewport/pick'
import type { PickHit } from '../viewport/pick'
import { buildScene } from '../viewport/scene'
import { wireSelection } from './aimCommit'
import { SelectionStore } from './store'

// The pointer module, the selection and a real picker on the Bracket, with a camera of a fixed size (1024 x 768,
// the iso view fitted) so the positions are exact. Events are synthetic Pointer Events on a canvas.

interface Rig {
  canvas: HTMLCanvasElement
  host: NavHost & { fit: ReturnType<typeof vi.fn> }
  view: ViewCamera
  selection: SelectionStore
  pointer: PointerModule
  index: PickIndex
  fire: (type: string, init: PointerEventInit & { t?: number }) => void
  /** Ids of the items the Aim was on, in order, one entry per change. */
  aimed: (string | null)[]
}

function rig(): Rig {
  const { outputs } = fixture('bracket')
  const built = buildScene(outputs)
  const view = new ViewCamera()
  view.resize(1024, 768)
  view.setBack(STANDARD_VIEWS['iso'])
  view.fit(built.fitBounds)
  const index = new PickIndex(outputs, built)
  const host = { view, pickPoint: () => null, modelCentre: () => new Vector3(), fit: vi.fn(), requestRender: vi.fn() }
  const canvas = document.createElement('canvas')
  canvas.setPointerCapture = (() => {}) as unknown as typeof canvas.setPointerCapture
  document.body.append(canvas)
  const pointer = new PointerModule(canvas, host)
  const selection = new SelectionStore()
  wireSelection(pointer, selection, (x, y, kind, filter) => index.pick(view, x, y, kind, filter))
  const aimed: (string | null)[] = []
  selection.subscribe(() => {
    const id = selection.getAimed()?.id ?? null
    if (aimed[aimed.length - 1] !== id) aimed.push(id)
  })
  const fire = (type: string, init: PointerEventInit & { t?: number }) => {
    const e = new PointerEvent(type, { bubbles: true, cancelable: true, ...init })
    if (init.t !== undefined) Object.defineProperty(e, 'timeStamp', { value: init.t })
    canvas.dispatchEvent(e)
  }
  return { canvas, host, view, selection, pointer, index, fire, aimed }
}

let r: Rig
beforeEach(() => {
  r = rig()
})
afterEach(() => {
  r.pointer.dispose()
  r.canvas.remove()
})

/** Spots on the view where this kind of pointer picks a face (and not an edge or a vertex), one per face, and an empty spot. */
function spots(rg: Rig, pointer: 'pen' | 'touch' | 'mouse' = 'pen'): { faces: { x: number; y: number; hit: PickHit }[]; empty: { x: number; y: number } } {
  const faces = new Map<number, { x: number; y: number; hit: PickHit }>()
  let empty: { x: number; y: number } | null = null
  for (let y = 10; y < 768; y += 14) {
    for (let x = 10; x < 1024; x += 14) {
      const hit = rg.index.pick(rg.view, x, y, pointer)
      if (hit === null) empty ??= { x, y }
      else if (hit.kind === 'face' && !faces.has(hit.index)) faces.set(hit.index, { x, y, hit })
    }
  }
  if (!empty) throw new Error('no empty spot in the view')
  return { faces: [...faces.values()], empty }
}

const pen = (type: string, x: number, y: number): [string, PointerEventInit] => [type, { pointerId: 7, pointerType: 'pen', clientX: x, clientY: y, pressure: type === 'pointerup' ? 0 : 0.5 }]
const finger = (id: number, x: number, y: number, t: number): PointerEventInit & { t: number } => ({ pointerId: id, pointerType: 'touch', clientX: x, clientY: y, t })
const mouse = (x: number, y: number, button = 0): PointerEventInit => ({ pointerId: 20, pointerType: 'mouse', clientX: x, clientY: y, button })

/** A pen stroke: down at `from`, one move to `to`, up at `to`. */
function stroke(rg: Rig, from: { x: number; y: number }, to: { x: number; y: number }) {
  rg.fire(...pen('pointerdown', from.x, from.y))
  rg.fire(...pen('pointermove', to.x, to.y))
  rg.fire(...pen('pointerup', to.x, to.y))
}
const touch = (rg: Rig, p: { x: number; y: number }) => stroke(rg, p, p)
const tap = (rg: Rig, p: { x: number; y: number }, id = 1, t = 1000) => {
  rg.fire('pointerdown', finger(id, p.x, p.y, t))
  rg.fire('pointerup', finger(id, p.x, p.y, t + 60))
}
const names = (rg: Rig) => rg.selection.getState().items.map((i) => i.name)

describe('the pen', () => {
  it('down on face A, move to face B, up: B is selected and A is not, and the Aim was on A, then B', () => {
    const { faces } = spots(r)
    const [a, b] = [faces[0]!, faces[1]!]
    expect(a.hit.index).not.toBe(b.hit.index)
    r.fire(...pen('pointerdown', a.x, a.y))
    expect(r.selection.getAimed()?.name).toBe(a.hit.name)
    r.fire(...pen('pointermove', b.x, b.y))
    expect(r.selection.getAimed()?.name).toBe(b.hit.name)
    expect(r.selection.getState().items).toEqual([]) // nothing is committed until the pen lifts
    r.fire(...pen('pointerup', b.x, b.y))
    expect(names(r)).toEqual([b.hit.name])
    expect(r.aimed).toEqual([`face|${a.hit.part}|${a.hit.name}`, `face|${b.hit.part}|${b.hit.name}`, null])
    expect(r.selection.getAimed()).toBeNull()
  })

  it('single mode: A then B leaves B; B again leaves nothing; with A selected, a Commit over empty space clears', () => {
    const { faces, empty } = spots(r)
    const [a, b] = [faces[0]!, faces[1]!]
    touch(r, a)
    touch(r, b)
    expect(names(r)).toEqual([b.hit.name])
    touch(r, b)
    expect(names(r)).toEqual([])
    touch(r, a)
    expect(names(r)).toEqual([a.hit.name])
    touch(r, empty)
    expect(names(r)).toEqual([])
  })

  it('Multi mode: A and B select both; A again leaves B; down on a face and up over empty space changes nothing; Clear empties it', () => {
    const { faces, empty } = spots(r)
    const [a, b] = [faces[0]!, faces[1]!]
    r.selection.setMulti(true)
    touch(r, a)
    touch(r, b)
    expect(names(r)).toEqual([a.hit.name, b.hit.name])
    touch(r, a)
    expect(names(r)).toEqual([b.hit.name])
    stroke(r, a, empty)
    expect(names(r)).toEqual([b.hit.name])
    touch(r, a)
    r.selection.clear()
    expect(names(r)).toEqual([])
  })

  it('a pointercancel never commits and never clears: the Aim ends and the selection is as it was', () => {
    const { faces } = spots(r)
    const [a, b] = [faces[0]!, faces[1]!]
    touch(r, b)
    r.fire(...pen('pointerdown', a.x, a.y))
    expect(r.selection.getAimed()).not.toBeNull()
    r.fire(...pen('pointercancel', a.x, a.y))
    expect(r.selection.getAimed()).toBeNull()
    expect(names(r)).toEqual([b.hit.name])
    // and in Multi mode too
    r.selection.setMulti(true)
    r.fire(...pen('pointerdown', a.x, a.y))
    r.fire(...pen('pointercancel', a.x, a.y))
    expect(names(r)).toEqual([b.hit.name])
  })

  it('an Aim move that stays on the same item changes nothing (no redraw is asked for); a new item changes it once', () => {
    const { faces } = spots(r)
    const [a, b] = [faces[0]!, faces[1]!]
    const seen = vi.fn()
    r.fire(...pen('pointerdown', a.x, a.y))
    r.selection.subscribe(seen)
    for (let i = 0; i < 5; i++) r.fire(...pen('pointermove', a.x + i, a.y))
    expect(seen).not.toHaveBeenCalled()
    r.fire(...pen('pointermove', b.x, b.y))
    expect(seen).toHaveBeenCalledTimes(1)
  })

  it('a pen that is not down does not Aim (no hover)', () => {
    const { faces } = spots(r)
    r.fire(...pen('pointermove', faces[0]!.x, faces[0]!.y))
    expect(r.selection.getAimed()).toBeNull()
  })

  it('an edge and a vertex are picked: the pen reaches 10 px', () => {
    const { outputs } = fixture('bracket')
    const mesh = outputs.parts[0]!.mesh!
    const len = (p: Float32Array) => Math.hypot(p[3]! - p[0]!, p[4]! - p[1]!, p[5]! - p[2]!)
    const p = [...mesh.edges].filter((q) => q.length === 6).sort((a, b) => len(b) - len(a))[0]!
    const at = r.view.project([(p[0]! + p[3]!) / 2, (p[1]! + p[4]!) / 2, (p[2]! + p[5]!) / 2])
    touch(r, { x: at.x + 6, y: at.y })
    expect(r.selection.getState().items[0]?.kind).toBe('edge')
    const v = r.view.project([mesh.vertices[0]!, mesh.vertices[1]!, mesh.vertices[2]!])
    touch(r, { x: v.x + 4, y: v.y })
    expect(r.selection.getState().items[0]?.kind).toBe('vertex')
  })
})

describe('a finger', () => {
  it('a tap selects the item under it', () => {
    const { faces } = spots(r, 'touch')
    tap(r, faces[0]!)
    expect(names(r)).toEqual([faces[0]!.hit.name])
  })

  it('a drag of 40 px moves the camera and selects nothing', () => {
    const { faces } = spots(r, 'touch')
    const before = JSON.stringify(r.view.state())
    const a = faces[0]!
    r.fire('pointerdown', finger(1, a.x, a.y, 1000))
    for (let i = 1; i <= 8; i++) r.fire('pointermove', finger(1, a.x + i * 5, a.y, 1000 + i * 10))
    r.fire('pointerup', finger(1, a.x + 40, a.y, 1100))
    expect(JSON.stringify(r.view.state())).not.toBe(before)
    expect(names(r)).toEqual([])
  })

  it('a touch of 250 ms or more, or one that moved more than 10 px, selects nothing', () => {
    const { faces } = spots(r, 'touch')
    const a = faces[0]!
    r.fire('pointerdown', finger(1, a.x, a.y, 1000))
    r.fire('pointerup', finger(1, a.x, a.y, 1260))
    expect(names(r)).toEqual([])
    r.fire('pointerdown', finger(2, a.x, a.y, 3000))
    r.fire('pointermove', finger(2, a.x + 12, a.y, 3020))
    r.fire('pointerup', finger(2, a.x, a.y, 3050))
    expect(names(r)).toEqual([])
    r.fire('pointerdown', finger(3, a.x, a.y, 5000))
    r.fire('pointermove', finger(3, a.x + 9, a.y, 5020))
    r.fire('pointerup', finger(3, a.x + 9, a.y, 5100))
    expect(names(r)).toEqual([a.hit.name])
  })

  it('two fingers select nothing', () => {
    const { faces } = spots(r, 'touch')
    const [a, b] = [faces[0]!, faces[1]!]
    r.fire('pointerdown', finger(1, a.x, a.y, 1000))
    r.fire('pointerdown', finger(2, b.x, b.y, 1010))
    r.fire('pointerup', finger(1, a.x, a.y, 1050))
    r.fire('pointerup', finger(2, b.x, b.y, 1060))
    expect(names(r)).toEqual([])
    expect(r.selection.getAimed()).toBeNull()
  })

  it('a tap acts at once; the second tap of a double tap fits the view and takes the first tap back', () => {
    const { faces } = spots(r, 'touch')
    const a = faces[0]!
    tap(r, a, 1, 1000)
    expect(names(r)).toEqual([a.hit.name]) // no wait for a second tap
    expect(r.host.fit).not.toHaveBeenCalled()
    tap(r, a, 2, 1200)
    expect(r.host.fit).toHaveBeenCalledTimes(1)
    expect(names(r)).toEqual([]) // the selection is what it was before the double tap
  })

  it('a double tap on empty space with a face selected leaves the face selected (the first tap cleared it, the second takes that back)', () => {
    const { faces, empty } = spots(r, 'touch')
    tap(r, faces[0]!, 1, 1000)
    tap(r, empty, 2, 5000)
    expect(names(r)).toEqual([])
    tap(r, empty, 3, 5200)
    expect(r.host.fit).toHaveBeenCalledTimes(1)
    expect(names(r)).toEqual([faces[0]!.hit.name])
  })

  it('a tap in Multi mode adds, and a tap over empty space does nothing', () => {
    const { faces, empty } = spots(r, 'touch')
    r.selection.setMulti(true)
    tap(r, faces[0]!, 1, 1000)
    tap(r, faces[1]!, 2, 3000)
    tap(r, empty, 3, 6000)
    expect(names(r)).toEqual([faces[0]!.hit.name, faces[1]!.hit.name])
  })
})

describe('the mouse', () => {
  it('moving highlights, a click commits, and moving off the view ends the Aim', () => {
    const { faces } = spots(r, 'mouse')
    const a = faces[0]!
    r.fire('pointermove', mouse(a.x, a.y))
    expect(r.selection.getAimed()?.name).toBe(a.hit.name)
    r.fire('pointerdown', mouse(a.x, a.y))
    r.fire('pointerup', mouse(a.x, a.y))
    expect(names(r)).toEqual([a.hit.name])
    expect(r.selection.getAimed()?.name).toBe(a.hit.name)
    r.fire('pointerleave', mouse(a.x, a.y))
    expect(r.selection.getAimed()).toBeNull()
  })

  it('the right button still orbits and selects nothing', () => {
    const { faces } = spots(r, 'mouse')
    const a = faces[0]!
    const before = JSON.stringify(r.view.state())
    r.fire('pointerdown', mouse(a.x, a.y, 2))
    r.fire('pointermove', mouse(a.x + 40, a.y))
    r.fire('pointerup', mouse(a.x + 40, a.y, 2))
    expect(JSON.stringify(r.view.state())).not.toBe(before)
    expect(names(r)).toEqual([])
    expect(r.selection.getAimed()).toBeNull()
  })
})
