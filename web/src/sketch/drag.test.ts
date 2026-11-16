import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PointerModule } from '../input/pointer'
import { ViewCamera } from '../viewport/camera'
import { applyLive, predict } from './drag'
import { SketchMode } from './mode'
import { SketchStore } from './store'
import type { SketchTransport } from './store'
import { FRONT, openedMessage, rectAndCircle } from './testFixture'
import { constructionPlan, deletionIds, isMovable } from './select'
import type { Uv } from './wire'

// Moving in a sketch (card I7.9): a pen press that goes past 6 px on a movable item drags it, a short one is an Aim and
// selects, a finger never drags the item and moves it only by its Drag handle, and the item is drawn at the pointer
// before the answer and at the solved place after it.
type Msg = Record<string, unknown>
interface Rig {
  store: SketchStore
  mode: SketchMode
  pointer: PointerModule
  view: ViewCamera
  say: (m: Msg) => void
  sent: Msg[]
  fire: (type: string, init: PointerEventInit) => void
  at: (kind: 'point' | 'entity' | 'dimension', id: string) => { x: number; y: number }
}

let r: Rig
let parent: HTMLDivElement

function rig(): Rig {
  const view = new ViewCamera()
  view.resize(800, 600)
  const sent: Msg[] = []
  const listeners = new Set<(m: Msg) => void>()
  const transport: SketchTransport = {
    send: (m) => (sent.push(m), true),
    onSketch: (l) => (listeners.add(l), () => listeners.delete(l)),
    onHello: () => () => {},
    stamp: () => 1234,
  }
  const store = new SketchStore(transport)
  parent = document.createElement('div')
  const canvas = document.createElement('canvas')
  canvas.setPointerCapture = vi.fn() as unknown as typeof canvas.setPointerCapture
  parent.append(canvas)
  document.body.append(parent)
  const host = { view, requestRender: vi.fn(), pickPoint: () => null, modelCentre: () => view.target.clone(), fit: vi.fn() }
  const pointer = new PointerModule(canvas, host)
  const mode = new SketchMode({ view, pointer, overlayParent: parent, requestRender: vi.fn(), modelBounds: () => null }, store)
  const say = (m: Msg) => listeners.forEach((l) => l(m))
  say(openedMessage(rectAndCircle(), FRONT))
  mode.update()
  const fire = (type: string, init: PointerEventInit) => canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, ...init }))
  const at = (kind: 'point' | 'entity' | 'dimension', id: string) => mode.report()!.screenOf(kind, id)!
  return { store, mode, pointer, view, say, sent, fire, at }
}

beforeEach(() => {
  r = rig()
})
afterEach(() => {
  r.mode.dispose()
  r.pointer.dispose()
  parent.remove()
})

const pen = (x: number, y: number): PointerEventInit => ({ pointerId: 7, pointerType: 'pen', clientX: x, clientY: y, pressure: 0.5 })
const finger = (x: number, y: number): PointerEventInit => ({ pointerId: 3, pointerType: 'touch', clientX: x, clientY: y })
const types = (): unknown[] => r.sent.map((m) => m['type'])
const dragMessages = (): Msg[] => r.sent.filter((m) => String(m['type']).startsWith('sketch.drag.'))

/** A pen press at `from`, `moves` steps to `to`, a lift there. */
function penDrag(from: { x: number; y: number }, to: { x: number; y: number }, moves = 5): void {
  r.fire('pointerdown', pen(from.x, from.y))
  for (let i = 1; i <= moves; i++) r.fire('pointermove', pen(from.x + ((to.x - from.x) * i) / moves, from.y + ((to.y - from.y) * i) / moves))
  r.fire('pointerup', pen(to.x, to.y))
}

describe('the pen drags the item it presses on', () => {
  it('a point: begin, moves and end, without having been selected first', () => {
    const p = r.at('point', 'p3')
    penDrag(p, { x: p.x + 30, y: p.y }, 5)
    const d = dragMessages()
    expect(d[0]).toMatchObject({ type: 'sketch.drag.begin', point: 'p3' })
    expect(d.filter((m) => m['type'] === 'sketch.drag.move').length).toBeGreaterThanOrEqual(1)
    expect(d.at(-1)).toEqual({ type: 'sketch.drag.end' })
    expect(types()).not.toContain('sketch.add')
    expect(r.store.getState().selected).toEqual([])
    // begun at the sketch point of the press, moves stamped with the connection's clock
    const at = d[0]!['at'] as Uv
    expect(at[0]).toBeCloseTo(60, 6)
    expect(at[1]).toBeCloseTo(48, 6)
    expect(d[1]!['t']).toBe(1234)
  })

  it('a line, and a circle, by the entity', () => {
    const l = r.at('entity', 'e1')
    penDrag(l, { x: l.x, y: l.y + 30 })
    expect(dragMessages()[0]).toMatchObject({ type: 'sketch.drag.begin', entity: 'e1' })
    expect(dragMessages().at(-1)).toEqual({ type: 'sketch.drag.end' })
    r.say({ ...openedMessage(), type: 'sketch.changed' })
    r.sent.length = 0
    const c = r.at('entity', 'e5')
    penDrag(c, { x: c.x + 30, y: c.y })
    expect(dragMessages()[0]).toMatchObject({ type: 'sketch.drag.begin', entity: 'e5' })
    expect(dragMessages().filter((m) => m['type'] === 'sketch.drag.move').length).toBeGreaterThanOrEqual(1)
  })

  it('a press that lifts within 6 px selects the point and sends no drag message', () => {
    const p = r.at('point', 'p3')
    r.fire('pointerdown', pen(p.x, p.y))
    r.fire('pointermove', pen(p.x + 3, p.y + 2))
    r.fire('pointerup', pen(p.x + 3, p.y + 2))
    expect(dragMessages()).toEqual([])
    expect(r.store.getState().selected).toEqual([{ kind: 'point', id: 'p3' }])
  })

  it('a press on empty space that moves far is not a drag', () => {
    penDrag({ x: 20, y: 20 }, { x: 80, y: 20 })
    expect(dragMessages()).toEqual([])
  })

  it('a pen press on a dimension label moves the label alone: one dimension.set at the lift, no drag messages', () => {
    const l = r.at('dimension', 'k3')
    penDrag(l, { x: l.x, y: l.y + 40 })
    expect(dragMessages()).toEqual([])
    const set = r.sent.at(-1)!
    expect(set).toMatchObject({ type: 'sketch.dimension.set', constraint: 'k3' })
    const to = r.mode.report()!.uvAt(l.x, l.y + 40)!
    const pos = set['pos'] as Uv
    expect(pos[0]).toBeCloseTo(to[0], 6)
    expect(pos[1]).toBeCloseTo(to[1], 6)
  })
})

describe('a finger moves an item only by its Drag handle', () => {
  it('a finger drag that starts on a point sends no drag message and pans the view', () => {
    const p = r.at('point', 'p3')
    const o = r.at('point', 'origin')
    r.fire('pointerdown', finger(p.x, p.y))
    r.fire('pointermove', finger(p.x + 30, p.y))
    r.fire('pointerup', finger(p.x + 30, p.y))
    expect(dragMessages()).toEqual([])
    expect(r.at('point', 'origin').x - o.x).toBeCloseTo(30, 3)
  })

  it('a selected point shows a handle; a finger press on it and a move send begin, moves and end', () => {
    expect(r.mode.report()!.handleOf('point', 'p3')).toBeNull()
    r.store.select({ kind: 'point', id: 'p3' })
    r.mode.update()
    expect(document.querySelectorAll('.sketch-layer [data-handle="point"]').length).toBe(1)
    const h = r.mode.report()!.handleOf('point', 'p3')!
    r.fire('pointerdown', finger(h.x, h.y))
    r.fire('pointermove', finger(h.x - 30, h.y + 10))
    r.fire('pointermove', finger(h.x - 60, h.y + 20))
    r.fire('pointerup', finger(h.x - 60, h.y + 20))
    const d = dragMessages()
    expect(d[0]).toMatchObject({ type: 'sketch.drag.begin', point: 'p3' })
    expect(d.filter((m) => m['type'] === 'sketch.drag.move').length).toBe(2)
    expect(d.at(-1)).toEqual({ type: 'sketch.drag.end' })
    // the view did not move
    expect(r.at('point', 'origin')).toEqual(r.at('point', 'origin'))
  })

  it('a press and lift on the handle moves nothing and sends nothing', () => {
    r.store.select({ kind: 'entity', id: 'e1' })
    r.mode.update()
    const h = r.mode.report()!.handleOf('entity', 'e1')!
    r.fire('pointerdown', finger(h.x, h.y))
    r.fire('pointerup', finger(h.x, h.y))
    expect(r.sent.filter((m) => String(m['type']).startsWith('sketch.'))).toEqual([])
  })

  it('the pen takes the handle too, and the origin shows none', () => {
    r.store.setSelection([{ kind: 'point', id: 'origin' }, { kind: 'point', id: 'p2' }])
    r.mode.update()
    expect(r.mode.report()!.handleOf('point', 'origin')).toBeNull()
    const h = r.mode.report()!.handleOf('point', 'p2')!
    penDrag(h, { x: h.x + 25, y: h.y })
    expect(dragMessages()[0]).toMatchObject({ type: 'sketch.drag.begin', point: 'p2' })
  })
})

describe('what is drawn while dragging', () => {
  it('the item is at the pointer before the reply and at the solved place after it', () => {
    const p = r.at('point', 'p3')
    r.fire('pointerdown', pen(p.x, p.y))
    r.fire('pointermove', pen(p.x + 40, p.y + 10))
    r.mode.update()
    // before any reply: where the pointer is
    const there = r.at('point', 'p3')
    expect(there.x).toBeCloseTo(p.x + 40, 3)
    expect(there.y).toBeCloseTo(p.y + 10, 3)
    expect(r.store.getState().live?.points['p3']).toBeDefined()
    // the solve put it somewhere else
    const solved = r.mode.report()!.uvAt(p.x + 25, p.y)!
    r.say({ type: 'sketch.drag.moved', t: 1234, solve_ms: 3, points: { p3: solved }, radii: {}, held: false })
    r.mode.update()
    const after = r.at('point', 'p3')
    expect(after.x).toBeCloseTo(p.x + 25, 3)
    expect(after.y).toBeCloseTo(p.y, 3)
    r.fire('pointerup', pen(p.x + 40, p.y + 10))
    // the end's answer is the sketch, and the drag is over
    r.say({ ...openedMessage(), type: 'sketch.changed' })
    expect(r.store.getState().live).toBeNull()
    expect(r.store.drag.active).toBe(false)
  })

  it('a dimension on the dragged point is drawn from the live points: its label follows the geometry before the reply', () => {
    const label = r.at('dimension', 'k3')
    const p = r.at('point', 'p2')
    r.fire('pointerdown', pen(p.x, p.y))
    r.fire('pointermove', pen(p.x + 40, p.y))
    r.mode.update()
    // the 60 mm dimension spans p1 to p2: p2 is 40 px further right, so the label centre is 20 px further right
    expect(r.at('point', 'p2').x).toBeCloseTo(p.x + 40, 3)
    expect(r.at('dimension', 'k3').x).toBeCloseTo(label.x + 20, 3)
    expect(r.at('dimension', 'k3').y).toBeCloseTo(label.y, 3)
  })

  it('a held answer (the constraints would not let the point go) puts the point and its dimension back where they were', () => {
    const label = r.at('dimension', 'k3')
    const p = r.at('point', 'p2')
    const before = rectAndCircle().points
    r.fire('pointerdown', pen(p.x, p.y))
    r.fire('pointermove', pen(p.x + 40, p.y + 10))
    r.say({ type: 'sketch.drag.moved', t: 1234, solve_ms: 3, points: before, radii: { e5: 10 }, held: true })
    r.mode.update()
    expect(r.at('point', 'p2').x).toBeCloseTo(p.x, 3)
    expect(r.at('point', 'p2').y).toBeCloseTo(p.y, 3)
    expect(r.at('dimension', 'k3').x).toBeCloseTo(label.x, 3)
    expect(r.at('dimension', 'k3').y).toBeCloseTo(label.y, 3)
  })

  it('a refused begin ends the drag at once', () => {
    const p = r.at('point', 'p3')
    r.fire('pointerdown', pen(p.x, p.y))
    r.fire('pointermove', pen(p.x + 40, p.y))
    r.say({ type: 'error', for: 'sketch.drag.begin', message: 'a drag is in progress' })
    expect(r.store.drag.active).toBe(false)
    expect(r.store.getState().live).toBeNull()
  })

  it('predict follows the server rule: a point and an entity move by the offset, a circle takes the pointer radius', () => {
    const data = rectAndCircle()
    expect(predict(data, { kind: 'point', id: 'p2' }, [0, 0], [3, 4]).points).toEqual({ p2: [63, 4] })
    const line = predict(data, { kind: 'entity', id: 'e1' }, [0, 0], [1, 2]).points
    expect(line).toEqual({ p1: [1, 2], p2: [61, 2] })
    expect(predict(data, { kind: 'entity', id: 'e5' }, [40, 24], [45, 24]).radii).toEqual({ e5: 15 })
    expect(applyLive(data, predict(data, { kind: 'entity', id: 'e5' }, [40, 24], [45, 24])).entities['e5']!.r).toBe(15)
    expect(applyLive(data, null)).toBe(data)
  })
})

describe('Delete and Construct', () => {
  it('Delete sends the selected ids and clears the selection', () => {
    r.store.setSelection([{ kind: 'entity', id: 'e1' }, { kind: 'point', id: 'p3' }, { kind: 'dimension', id: 'k3' }])
    r.store.deleteSelected()
    expect(r.sent.at(-1)).toEqual({ type: 'sketch.delete', ids: ['e1', 'p3', 'k3'] })
    expect(r.store.getState().selected).toEqual([])
    r.sent.length = 0
    r.store.deleteSelected()
    expect(r.sent).toEqual([])
  })

  it('Construct turns the selected entities to construction, and back when all are', () => {
    r.store.setSelection([{ kind: 'entity', id: 'e1' }, { kind: 'point', id: 'p3' }])
    r.store.toggleConstruction()
    expect(r.sent.at(-1)).toEqual({ type: 'sketch.construction', ids: ['e1'], value: true })
    r.store.setSelection([{ kind: 'entity', id: 'e6' }])
    r.store.toggleConstruction()
    expect(r.sent.at(-1)).toEqual({ type: 'sketch.construction', ids: ['e6'], value: false })
    r.store.setSelection([{ kind: 'entity', id: 'e6' }, { kind: 'entity', id: 'e1' }])
    r.store.toggleConstruction()
    expect(r.sent.at(-1)).toEqual({ type: 'sketch.construction', ids: ['e6', 'e1'], value: true })
    r.sent.length = 0
    // a selection of a point has no entity to flag: it sets the construction mode, as the desktop does
    r.store.setSelection([{ kind: 'point', id: 'p3' }])
    r.store.toggleConstruction()
    expect(r.sent).toEqual([])
    expect(r.store.getState().construction).toBe(true)
  })

  it('the helpers', () => {
    const data = rectAndCircle()
    expect(deletionIds([{ kind: 'point', id: 'origin' }, { kind: 'entity', id: 'e1' }])).toEqual(['e1'])
    expect(constructionPlan(data, [])).toBeNull()
    expect(isMovable(data, { kind: 'constraint', id: 'k1' })).toBe(false)
    expect(isMovable(data, { kind: 'point', id: 'origin' })).toBe(false)
    expect(isMovable(data, { kind: 'entity', id: 'e5' })).toBe(true)
    expect(isMovable(data, { kind: 'dimension', id: 'k3' })).toBe(true)
  })
})

describe('a circle dragged by its rim (F14)', () => {
  const data = rectAndCircle()
  const start: Uv = [40, 24]
  const at: Uv = [45, 27]
  it('resizes when its radius is free', () => {
    const live = predict(data, { kind: 'entity', id: 'e5' }, start, at)
    expect(live.points).toEqual({})
    expect(live.radii['e5']).toBeCloseTo(Math.hypot(15, 3), 6)
  })
  it('grows by the distance gone from the centre when the press was off the rim (a Drag handle)', () => {
    const live = predict(data, { kind: 'entity', id: 'e5' }, [46, 24], [48, 24])
    expect(live.radii['e5']).toBeCloseTo(12, 6)
  })
  it('moves whole when a diameter dimension holds the radius, with no radius drawn', () => {
    const held = { ...data, constraints: [...data.constraints, { id: 'k9', type: 'diameter', refs: ['e5'], value: 20 }] }
    const live = predict(held, { kind: 'entity', id: 'e5' }, start, at)
    expect(live.radii).toEqual({})
    expect(live.points['c1']).toEqual([35, 27])
  })
})

describe('a mouse drag in the sketch (F14)', () => {
  it('drags a circle by its rim past the threshold and sends sketch.drag.begin for the entity', () => {
    const rim = r.at('entity', 'e5')
    r.fire('pointerdown', { pointerId: 20, pointerType: 'mouse', button: 0, buttons: 1, clientX: rim.x, clientY: rim.y })
    r.fire('pointermove', { pointerId: 20, pointerType: 'mouse', button: -1, buttons: 1, clientX: rim.x + 30, clientY: rim.y + 10 })
    expect(r.sent.find((m) => m['type'] === 'sketch.drag.begin')).toMatchObject({ entity: 'e5' })
    expect(r.sent.some((m) => m['type'] === 'sketch.drag.move')).toBe(true)
    r.fire('pointerup', { pointerId: 20, pointerType: 'mouse', button: 0, buttons: 0, clientX: rim.x + 30, clientY: rim.y + 10 })
    expect(r.sent.some((m) => m['type'] === 'sketch.drag.end')).toBe(true)
  })
})
