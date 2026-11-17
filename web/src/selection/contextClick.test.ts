import { Vector3 } from 'three'
import { afterEach, expect, it, vi } from 'vitest'
import { PointerModule } from '../input/pointer'
import { ViewCamera } from '../viewport/camera'
import { wireSelection, CONTEXT_KINDS } from './aimCommit'
import { SelectionStore } from './store'

// A right click without movement asks for a context menu in Desktop mode (card I9.10); a right drag orbits and asks for none.
function rig(desktop: boolean) {
  const view = new ViewCamera()
  view.resize(800, 600)
  view.scale = 0.25
  const host = { view, pickPoint: () => new Vector3(1, 2, 3), modelCentre: () => new Vector3(), fit: vi.fn(), requestRender: vi.fn() }
  const canvas = document.createElement('canvas')
  document.body.append(canvas)
  const pointer = new PointerModule(canvas, host)
  pointer.setDesktop(desktop)
  const hit = { kind: 'face', part: 'F2/1', index: 0, name: null, point: [0, 0, 0], distancePx: null } as const
  const pick = vi.fn(() => hit)
  const context = vi.fn()
  wireSelection(pointer, new SelectionStore(), pick, undefined, context)
  const fire = (type: string, x: number, y: number) => canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 3, pointerType: 'mouse', button: 2, clientX: x, clientY: y }))
  return { canvas, view, pointer, pick, context, fire, hit }
}

let r: ReturnType<typeof rig>
afterEach(() => {
  r.pointer.dispose()
  r.canvas.remove()
})

it('a right press and release with no movement reports what is under it, picked with the desktop kinds, not the Select filter', () => {
  r = rig(true)
  r.fire('pointerdown', 200, 150)
  r.fire('pointerup', 202, 151)
  expect(r.context).toHaveBeenCalledTimes(1)
  expect(r.context.mock.calls[0]?.[0]).toBe(r.hit)
  expect(r.context.mock.calls[0]?.[1]).toMatchObject({ x: 202, y: 151 })
  expect(r.pick).toHaveBeenLastCalledWith(202, 151, 'mouse', CONTEXT_KINDS)
  expect(CONTEXT_KINDS).toEqual(['face', 'edge', 'plane', 'axis', 'region'])
})

it('a right drag orbits the view and opens no menu', () => {
  r = rig(true)
  const before = [...r.view.state().back]
  r.fire('pointerdown', 200, 150)
  for (let i = 1; i <= 10; i++) r.fire('pointermove', 200 + i * 8, 150 + i * 3)
  r.fire('pointerup', 280, 180)
  expect(r.context).not.toHaveBeenCalled()
  const after = r.view.state().back
  expect(after.some((v, i) => Math.abs(v - before[i]!) > 1e-3)).toBe(true)
})

it('Tablet mode: a right click opens nothing', () => {
  r = rig(false)
  r.fire('pointerdown', 200, 150)
  r.fire('pointerup', 200, 150)
  expect(r.context).not.toHaveBeenCalled()
})
