import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PointerModule } from '../input/pointer'
import { ViewCamera } from '../viewport/camera'
import { SketchMode } from './mode'
import { SketchStore } from './store'
import type { SketchTransport } from './store'
import { FRONT, openedMessage, rectAndCircle, TILTED } from './testFixture'
import type { Frame } from './wire'

const deg = (r: number) => (r * 180) / Math.PI
const rad = (d: number) => (d * Math.PI) / 180

interface Rig {
  view: ViewCamera
  store: SketchStore
  mode: SketchMode
  canvas: HTMLCanvasElement
  parent: HTMLDivElement
  pointer: PointerModule
  say: (m: Record<string, unknown>) => void
  sent: Record<string, unknown>[]
  fire: (type: string, init: PointerEventInit) => void
}

function rig(frame: Frame): Rig {
  const view = new ViewCamera()
  view.resize(800, 600)
  const sent: Record<string, unknown>[] = []
  let listener: (m: Record<string, unknown>) => void = () => {}
  const transport: SketchTransport = {
    send: (m) => (sent.push(m), true),
    onSketch: (l) => ((listener = l), () => {}),
    onHello: () => () => {},
  }
  const store = new SketchStore(transport)
  const parent = document.createElement('div')
  const canvas = document.createElement('canvas')
  canvas.setPointerCapture = vi.fn() as unknown as typeof canvas.setPointerCapture
  parent.append(canvas)
  document.body.append(parent)
  const host = { view, requestRender: vi.fn(), pickPoint: () => null, modelCentre: () => view.target.clone(), fit: vi.fn() }
  const pointer = new PointerModule(canvas, host)
  const mode = new SketchMode({ view, pointer, overlayParent: parent, requestRender: vi.fn(), modelBounds: () => null }, store)
  const say = (m: Record<string, unknown>) => listener(m)
  say(openedMessage(rectAndCircle(), frame))
  mode.update()
  const fire = (type: string, init: PointerEventInit) => canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, ...init }))
  return { view, store, mode, canvas, parent, pointer, say, sent, fire }
}

const touch = (id: number, x: number, y: number): PointerEventInit => ({ pointerId: id, pointerType: 'touch', clientX: x, clientY: y })
const dotN = (r: Rig, f: Frame): number => {
  const b = r.view.state().back
  return b[0] * f.normal[0] + b[1] * f.normal[1] + b[2] * f.normal[2]
}
const xAngle = (r: Rig): number => r.mode.report()!.xAxisAngle

let r: Rig
afterEach(() => {
  r.mode.dispose()
  r.pointer.dispose()
  r.parent.remove()
})

describe.each([
  ['Front', FRONT],
  ['a tilted face', TILTED],
])('sketch mode on %s', (_name, frame) => {
  beforeEach(() => {
    r = rig(frame)
  })

  it('a sketch opens in 2D, the camera normal to the sketch plane with the sketch upright', () => {
    expect(r.store.getState().nav).toBe('2d')
    expect(dotN(r, frame)).toBeCloseTo(1, 9)
    const up = r.view.axes().up
    expect(up.x * frame.ydir[0] + up.y * frame.ydir[1] + up.z * frame.ydir[2]).toBeCloseTo(1, 9)
    expect(r.mode.report()!.tilt).toBeLessThan(1e-6)
    expect(r.mode.report()!.xAxisAngle).toBeCloseTo(0, 9)
  })

  it('draws the sketch into a layer over the canvas', () => {
    expect(r.parent.querySelectorAll('.sketch-layer path[data-kind="line"]')).toHaveLength(5)
    expect(r.parent.querySelectorAll('.sketch-layer g[data-kind="dimension"]')).toHaveLength(1)
  })

  it('in 2D a one-finger drag pans and the camera stays normal to the plane', () => {
    const before = r.view.target.clone()
    const at = r.mode.report()!.screenOf('point', 'p3')!
    r.fire('pointerdown', touch(1, 400, 300))
    for (let i = 1; i <= 10; i++) r.fire('pointermove', touch(1, 400 + i * 10, 300 + i * 4))
    r.fire('pointerup', touch(1, 500, 340))
    expect(r.view.target.distanceTo(before)).toBeGreaterThan(1)
    expect(dotN(r, frame)).toBeCloseTo(1, 9)
    const now = r.mode.report()!.screenOf('point', 'p3')!
    expect(now.x - at.x).toBeCloseTo(100, 6)
    expect(now.y - at.y).toBeCloseTo(40, 6)
    expect(r.mode.report()!.xAxisAngle).toBeCloseTo(0, 9)
  })

  it('in 2D a two-finger twist of 30 degrees rotates the drawn sketch 30 degrees on screen, and the camera stays normal', () => {
    const start = xAngle(r)
    const finger = (a: number): [PointerEventInit, PointerEventInit] => [touch(1, 400 - 100 * Math.cos(a), 300 - 100 * Math.sin(a)), touch(2, 400 + 100 * Math.cos(a), 300 + 100 * Math.sin(a))]
    const [a0, b0] = finger(0)
    r.fire('pointerdown', a0)
    r.fire('pointerdown', b0)
    for (let i = 1; i <= 15; i++) {
      const [a, b] = finger(rad(2 * i))
      r.fire('pointermove', a)
      r.fire('pointermove', b)
    }
    const turned = deg(xAngle(r) - start)
    // the sketch turns with the fingers (screen y points down, so a positive finger angle is clockwise on screen)
    expect(Math.abs(turned)).toBeCloseTo(30, 6)
    expect(Math.sign(turned)).toBe(1)
    expect(dotN(r, frame)).toBeCloseTo(1, 9)
  })

  it('a twist of 3 degrees also turns the sketch in 2D: there is no dead zone', () => {
    const start = xAngle(r)
    r.fire('pointerdown', touch(1, 300, 300))
    r.fire('pointerdown', touch(2, 500, 300))
    r.fire('pointermove', touch(1, 300 + 100 - 100 * Math.cos(rad(3)), 300 - 100 * Math.sin(rad(3))))
    r.fire('pointermove', touch(2, 400 + 100 * Math.cos(rad(3)), 300 + 100 * Math.sin(rad(3))))
    expect(Math.abs(deg(xAngle(r) - start))).toBeCloseTo(3, 6)
  })

  it('in 3D a one-finger drag orbits: the camera leaves the sketch normal', () => {
    r.store.setNav('3d')
    expect(r.store.getState().nav).toBe('3d')
    r.fire('pointerdown', touch(1, 400, 300))
    for (let i = 1; i <= 10; i++) r.fire('pointermove', touch(1, 400 + i * 8, 300 + i * 5))
    r.fire('pointerup', touch(1, 480, 350))
    expect(Math.abs(dotN(r, frame))).toBeLessThan(0.99)
    expect(r.mode.report()!.tilt).toBeGreaterThan(0.1)
  })

  it('in 3D a pick at a screen point still returns the item under it, and a point placed there lies in the sketch plane', () => {
    r.store.setNav('3d')
    r.fire('pointerdown', touch(1, 400, 300))
    for (let i = 1; i <= 10; i++) r.fire('pointermove', touch(1, 400 + i * 8, 300 + i * 5))
    r.fire('pointerup', touch(1, 480, 350))
    r.mode.update()
    const rep = r.mode.report()!
    const e1 = rep.screenOf('entity', 'e1')!
    expect(rep.pickAt(e1.x, e1.y, 'pen')).toEqual({ kind: 'entity', id: 'e1' })
    const p2 = rep.screenOf('point', 'p2')!
    expect(rep.pickAt(p2.x + 2, p2.y - 2, 'touch')).toEqual({ kind: 'point', id: 'p2' })
    for (const [x, y] of [[123, 456], [400, 300], [700, 80]] as const) {
      const uv = rep.uvAt(x, y)!
      const w = [0, 1, 2].map((i) => frame.origin[i]! + frame.xdir[i]! * uv[0] + frame.ydir[i]! * uv[1])
      const off = [0, 1, 2].reduce((s, i) => s + (w[i]! - frame.origin[i]!) * frame.normal[i]!, 0)
      expect(Math.abs(off)).toBeLessThan(1e-9)
      const back = r.view.project([w[0]!, w[1]!, w[2]!])
      expect(back.x).toBeCloseTo(x, 6)
      expect(back.y).toBeCloseTo(y, 6)
    }
  })

  it('switching from 3D back to 2D returns the camera to the sketch normal', () => {
    r.store.setNav('3d')
    r.fire('pointerdown', touch(1, 400, 300))
    for (let i = 1; i <= 10; i++) r.fire('pointermove', touch(1, 400 + i * 8, 300 + i * 5))
    r.fire('pointerup', touch(1, 480, 350))
    expect(Math.abs(dotN(r, frame))).toBeLessThan(0.99)
    r.store.setNav('2d')
    expect(dotN(r, frame)).toBeCloseTo(1, 9)
    expect(r.mode.report()!.tilt).toBeLessThan(1e-6)
  })

  it('Normal to returns the camera to the sketch normal and leaves the mode alone', () => {
    r.store.setNav('3d')
    r.fire('pointerdown', touch(1, 400, 300))
    for (let i = 1; i <= 10; i++) r.fire('pointermove', touch(1, 400 + i * 8, 300 + i * 5))
    r.fire('pointerup', touch(1, 480, 350))
    r.store.normalTo()
    expect(dotN(r, frame)).toBeCloseTo(1, 9)
    expect(r.store.getState().nav).toBe('3d')
  })

  it('coming back to 2D keeps the turn the sketch was given on screen', () => {
    // twist 30 degrees in 2D, go to 3D and orbit, come back: the sketch is square again and still turned by 30
    r.fire('pointerdown', touch(1, 300, 300))
    r.fire('pointerdown', touch(2, 500, 300))
    for (let i = 1; i <= 15; i++) {
      const a = rad(2 * i)
      r.fire('pointermove', touch(1, 400 - 100 * Math.cos(a), 300 - 100 * Math.sin(a)))
      r.fire('pointermove', touch(2, 400 + 100 * Math.cos(a), 300 + 100 * Math.sin(a)))
    }
    r.fire('pointerup', touch(1, 0, 0))
    r.fire('pointerup', touch(2, 0, 0))
    const turned = xAngle(r)
    r.store.setNav('3d')
    r.fire('pointerdown', touch(3, 400, 300))
    for (let i = 1; i <= 10; i++) r.fire('pointermove', touch(3, 400 + i * 8, 300 + i * 5))
    r.fire('pointerup', touch(3, 480, 350))
    r.store.setNav('2d')
    expect(xAngle(r)).toBeCloseTo(turned, 9)
    expect(dotN(r, frame)).toBeCloseTo(1, 9)
  })
})

describe('selecting in a sketch', () => {
  beforeEach(() => {
    r = rig(FRONT)
  })

  it('a tap on an item selects it; a tap on nothing clears; Multi toggles', () => {
    const rep = r.mode.report()!
    const p = rep.screenOf('point', 'p2')!
    const rect = r.canvas.getBoundingClientRect()
    const tap = (x: number, y: number, t: number) => {
      const down = new PointerEvent('pointerdown', { bubbles: true, cancelable: true, ...touch(1, rect.left + x, rect.top + y) })
      Object.defineProperty(down, 'timeStamp', { value: t })
      r.canvas.dispatchEvent(down)
      const up = new PointerEvent('pointerup', { bubbles: true, cancelable: true, ...touch(1, rect.left + x, rect.top + y) })
      Object.defineProperty(up, 'timeStamp', { value: t + 50 })
      r.canvas.dispatchEvent(up)
    }
    tap(p.x, p.y, 1000)
    expect(r.store.getState().selected).toEqual([{ kind: 'point', id: 'p2' }])
    r.mode.update()
    expect(r.parent.querySelector('circle[data-selected]')).not.toBeNull()
    tap(700, 80, 5000)
    expect(r.store.getState().selected).toEqual([])
    r.store.setMulti(true)
    tap(p.x, p.y, 9000)
    const q = rep.screenOf('point', 'p4')!
    tap(q.x, q.y, 12000)
    expect(r.store.getState().selected).toHaveLength(2)
  })
})
