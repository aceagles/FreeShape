import { afterEach, describe, expect, it, vi } from 'vitest'
import { PointerModule } from '../input/pointer'
import type { NavHost } from '../input/pointer'
import { ViewCamera } from '../viewport/camera'
import type { Manipulator } from '../wire'
import { ManipulatorLayer, snapStep, viewSnapStep } from './manipulator'
import type { ManipulatorSink } from './manipulator'

const arrow = (over: Partial<Manipulator> = {}): Manipulator => ({
  field: 'depth',
  flip: 'flip',
  origin: [0, 0, 0],
  dir: [0, 0, 1],
  value: 10,
  symmetric: false,
  snap: '1',
  min: null,
  label: '10 mm',
  ...over,
})

/** A pen on a view of 800 x 600 at 1 mm per 4 px, looking along +Y so that Z is up the screen. */
function rig(m: Manipulator) {
  const view = new ViewCamera()
  view.resize(800, 600)
  view.scale = 0.25
  view.target.set(0, 0, 0)
  view.setBack([0, -1, 0])
  const parent = document.createElement('div')
  const canvas = document.createElement('canvas')
  canvas.setPointerCapture = (() => {}) as typeof canvas.setPointerCapture
  parent.append(canvas)
  document.body.append(parent)
  const sets: Record<string, unknown>[] = []
  const edits: string[] = []
  const sink: ManipulatorSink = { set: (v) => sets.push(v), edit: (f) => edits.push(f) }
  const layer = new ManipulatorLayer(parent, view, () => {}, () => sink)
  layer.set([m])
  const host: NavHost = { view, pickPoint: () => null, modelCentre: () => view.target.clone(), fit: vi.fn(), requestRender: vi.fn() }
  const module = new PointerModule(canvas, host)
  module.setHandleHandler(layer)
  const orbit = vi.spyOn(view, 'orbit')
  const fire = (type: string, x: number, y: number, pointerType = 'pen', id = 1) =>
    canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: id, pointerType, clientX: x, clientY: y, button: 0 }))
  const base = view.project([0, 0, 0])
  // the handle sits 26 px beyond the arrow head, up the screen
  const tip = view.project([0, 0, m.value])
  return { view, layer, sets, edits, fire, orbit, base, tip, handle: { x: tip.x, y: tip.y - 26 }, canvas, module, parent }
}

afterEach(() => {
  document.body.replaceChildren()
})

describe('the drag arrow', () => {
  it('sends increasing values while the handle moves 50 px along the arrow, then one final update on release', () => {
    const r = rig(arrow())
    r.fire('pointerdown', r.handle.x, r.handle.y)
    for (const dy of [10, 20, 30, 40, 50]) r.fire('pointermove', r.handle.x, r.handle.y - dy)
    const moving = r.sets.map((s) => s['depth'] as number)
    expect(moving.length).toBeGreaterThan(1)
    expect(moving).toEqual([...moving].sort((a, b) => a - b))
    expect(moving[0]).toBeGreaterThan(10)
    // 50 px at 4 px per mm is 12.5 mm more; snapped to whole mm
    expect(moving.at(-1)).toBe(23)
    const before = r.sets.length
    r.fire('pointerup', r.handle.x, r.handle.y - 50)
    expect(r.sets.length).toBe(before + 1)
    expect(r.sets.at(-1)).toEqual({ depth: 23, flip: false })
    expect(r.orbit).not.toHaveBeenCalled()
  })

  it('does nothing for a drag that starts off the handle', () => {
    const r = rig(arrow())
    r.fire('pointerdown', 40, 40)
    r.fire('pointermove', 40, 0)
    r.fire('pointerup', 40, 0)
    expect(r.sets).toEqual([])
    expect(r.layer.valueOf(0)).toBe(10)
  })

  it('keeps a finger drag on the handle from orbiting the view', () => {
    const r = rig(arrow())
    r.fire('pointerdown', r.handle.x, r.handle.y, 'touch', 7)
    r.fire('pointermove', r.handle.x, r.handle.y - 20, 'touch', 7)
    r.fire('pointerup', r.handle.x, r.handle.y - 20, 'touch', 7)
    expect(r.sets.length).toBeGreaterThan(0)
    expect(r.orbit).not.toHaveBeenCalled()
  })

  it('sends whole millimetres under a snap rule of 1 mm', () => {
    const r = rig(arrow({ snap: '1' }))
    r.fire('pointerdown', r.handle.x, r.handle.y)
    for (let dy = 1; dy <= 61; dy += 3) r.fire('pointermove', r.handle.x, r.handle.y - dy)
    r.fire('pointerup', r.handle.x, r.handle.y - 61)
    expect(r.sets.length).toBeGreaterThan(2)
    for (const s of r.sets) expect(Number.isInteger(s['depth'])).toBe(true)
  })

  it('takes the sign of a drag past the origin as the flip and never sends zero', () => {
    const r = rig(arrow({ snap: '1' }))
    r.fire('pointerdown', r.handle.x, r.handle.y)
    r.fire('pointermove', r.handle.x, r.base.y + 40)
    r.fire('pointerup', r.handle.x, r.base.y + 40)
    // the grab is 6.5 mm beyond the head, so 10 mm below the origin is -16.5, snapped to -16
    expect(r.sets.at(-1)).toEqual({ depth: 16, flip: true })
    expect(r.layer.valueOf(0)).toBe(-16)
  })

  it('doubles the value of a symmetric arrow, keeps the floor, and sends no flip', () => {
    const r = rig(arrow({ symmetric: true, value: 5, min: 1e-3, label: '10 mm', snap: '0' }))
    const tip = r.view.project([0, 0, 5])
    r.fire('pointerdown', tip.x, tip.y - 26)
    r.fire('pointermove', tip.x, tip.y - 26 + 20)
    // 20 px down is 5 mm less along the arrow: half-depth 5 -> 0, floored at 1e-3 mm, so the field is 0.002
    expect(r.sets.at(-1)).toEqual({ depth: 0.002 })
    r.fire('pointerup', tip.x, tip.y - 26 + 20)
    expect(Object.keys(r.sets.at(-1)!)).toEqual(['depth'])
  })

  it('opens the pad on a tap of the value label, and does not send a value', () => {
    const r = rig(arrow())
    r.fire('pointerdown', r.handle.x + 70, r.handle.y)
    r.fire('pointerup', r.handle.x + 70, r.handle.y)
    expect(r.edits).toEqual(['depth'])
    expect(r.sets).toEqual([])
  })

  it('draws the handle and the label, and the label follows the value', () => {
    const r = rig(arrow())
    expect(r.parent.querySelector('[data-part="handle"]')).not.toBeNull()
    expect(r.parent.querySelector('[data-part="label"]')?.textContent).toBe('10 mm')
    r.fire('pointerdown', r.handle.x, r.handle.y)
    r.fire('pointermove', r.handle.x, r.handle.y - 20)
    expect(r.parent.querySelector('[data-part="label"]')?.textContent).toBe('15 mm')
  })

  it('keeps the dragged value when a preview arrives mid-drag', () => {
    const r = rig(arrow())
    r.fire('pointerdown', r.handle.x, r.handle.y)
    r.fire('pointermove', r.handle.x, r.handle.y - 20)
    r.layer.set([arrow({ value: 10 })])
    expect(r.layer.valueOf(0)).toBe(15)
    r.fire('pointerup', r.handle.x, r.handle.y - 20)
    r.layer.set([arrow({ value: 15, label: '15 mm' })])
    expect(r.layer.valueOf(0)).toBe(15)
  })
})

describe('the snap rule', () => {
  it('picks the step from the zoom as the desktop does', () => {
    expect(viewSnapStep(0.25)).toBe(2)
    expect(viewSnapStep(0.1)).toBe(1)
    expect(viewSnapStep(1)).toBe(10)
    expect(snapStep('view', 0.25)).toBe(2)
    expect(snapStep('0.5', 1)).toBe(0.5)
    expect(snapStep('none', 1)).toBe(0)
  })
})
