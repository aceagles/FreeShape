import { Vector3 } from 'three'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ViewCamera } from '../viewport/camera'
import { PointerModule, TWIST_DEAD_ZONE } from './pointer'
import type { NavHost, PenHandler } from './pointer'

const deg = (r: number) => (r * 180) / Math.PI
const rad = (d: number) => (d * Math.PI) / 180

interface Rig {
  canvas: HTMLCanvasElement
  host: NavHost & { fit: ReturnType<typeof vi.fn> }
  view: ViewCamera
  module: PointerModule
  capture: ReturnType<typeof vi.fn>
  fire: (type: string, init: PointerEventInit & { t?: number }) => void
}

const PIVOT = new Vector3(10, 5, 3)

function rig(pick: Vector3 | null = PIVOT): Rig {
  const view = new ViewCamera()
  view.resize(800, 600)
  view.scale = 0.25
  const host = {
    view,
    pickPoint: () => pick,
    modelCentre: () => new Vector3(),
    fit: vi.fn(),
    requestRender: vi.fn(),
  }
  const canvas = document.createElement('canvas')
  const capture = vi.fn()
  canvas.setPointerCapture = capture as unknown as typeof canvas.setPointerCapture
  document.body.append(canvas)
  const module = new PointerModule(canvas, host)
  const fire = (type: string, init: PointerEventInit & { t?: number }) => {
    const e = new PointerEvent(type, { bubbles: true, cancelable: true, ...init })
    if (init.t !== undefined) Object.defineProperty(e, 'timeStamp', { value: init.t })
    canvas.dispatchEvent(e)
  }
  return { canvas, host, view, module, capture, fire }
}

const state = (v: ViewCamera) => ({ ...v.state() })
const touch = (id: number, x: number, y: number, t?: number): PointerEventInit & { t?: number } => ({
  pointerId: id,
  pointerType: 'touch',
  clientX: x,
  clientY: y,
  t,
})

let r: Rig
beforeEach(() => {
  r = rig()
})
afterEach(() => {
  r.module.dispose()
  r.canvas.remove()
})

describe('one finger', () => {
  it('orbits and keeps the pivot where it was on screen', () => {
    const before = r.view.project([PIVOT.x, PIVOT.y, PIVOT.z])
    const back = state(r.view).back
    r.fire('pointerdown', touch(1, 400, 300))
    for (let i = 1; i <= 12; i++) r.fire('pointermove', touch(1, 400 + i * 10, 300 + i * 4))
    r.fire('pointerup', touch(1, 520, 348))
    const after = r.view.project([PIVOT.x, PIVOT.y, PIVOT.z])
    expect(Math.hypot(after.x - before.x, after.y - before.y)).toBeLessThan(1)
    const now = state(r.view).back
    const dot = back[0] * now[0] + back[1] * now[1] + back[2] * now[2]
    expect(deg(Math.acos(dot))).toBeGreaterThan(10)
    expect(r.view.scale).toBe(0.25)
  })

  it('orbits about the model centre when the touch misses the model', () => {
    r.module.dispose()
    r.canvas.remove()
    r = rig(null)
    const before = r.view.project([0, 0, 0])
    r.fire('pointerdown', touch(1, 100, 100))
    r.fire('pointermove', touch(1, 180, 140))
    const after = r.view.project([0, 0, 0])
    expect(Math.hypot(after.x - before.x, after.y - before.y)).toBeLessThan(1)
  })

  it('never turns the view past straight up or down', () => {
    r.fire('pointerdown', touch(1, 400, 300))
    r.fire('pointermove', touch(1, 400, 3000))
    expect(r.view.back.z).toBeLessThan(1)
    expect(Math.abs(r.view.back.z)).toBeGreaterThan(0.99)
    expect(r.view.axes().up.z).toBeGreaterThan(0)
  })

  it('fits on a double tap, and not on two slow or distant taps', () => {
    r.fire('pointerdown', touch(1, 400, 300, 1000))
    r.fire('pointerup', touch(1, 400, 300, 1060))
    r.fire('pointerdown', touch(2, 405, 302, 1200))
    r.fire('pointerup', touch(2, 405, 302, 1250))
    expect(r.host.fit).toHaveBeenCalledTimes(1)
    r.fire('pointerdown', touch(3, 100, 100, 5000))
    r.fire('pointerup', touch(3, 100, 100, 5050))
    r.fire('pointerdown', touch(4, 100, 100, 5900))
    r.fire('pointerup', touch(4, 100, 100, 5950))
    r.fire('pointerdown', touch(5, 700, 500, 9000))
    r.fire('pointerup', touch(5, 700, 500, 9050))
    r.fire('pointerdown', touch(6, 100, 100, 9150))
    r.fire('pointerup', touch(6, 100, 100, 9200))
    expect(r.host.fit).toHaveBeenCalledTimes(1)
  })

  it('does not count a drag as a tap', () => {
    r.fire('pointerdown', touch(1, 400, 300, 1000))
    r.fire('pointermove', touch(1, 460, 300, 1020))
    r.fire('pointerup', touch(1, 460, 300, 1050))
    r.fire('pointerdown', touch(2, 460, 300, 1100))
    r.fire('pointerup', touch(2, 460, 300, 1150))
    expect(r.host.fit).not.toHaveBeenCalled()
  })
})

/** Two fingers around (cx, cy), `radius` from it each, at `angle` radians (screen angle of finger 2 from finger 1). */
function twoFingers(angle: number, radius: number, cx: number, cy: number): [PointerEventInit, PointerEventInit] {
  return [
    touch(1, cx - Math.cos(angle) * radius, cy - Math.sin(angle) * radius),
    touch(2, cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius),
  ]
}

function pointUnder(v: ViewCamera, x: number, y: number): [number, number, number] {
  const p = v.unproject(x, y)
  return [p.x, p.y, p.z]
}

describe('two fingers', () => {
  it('pan without changing the zoom when the spacing is constant', () => {
    const [a, b] = twoFingers(0, 100, 400, 300)
    const p = pointUnder(r.view, 400, 300)
    r.fire('pointerdown', a)
    r.fire('pointerdown', b)
    const scale = r.view.scale
    for (let i = 1; i <= 10; i++) {
      const [a2, b2] = twoFingers(0, 100, 400 + i * 8, 300 - i * 5)
      r.fire('pointermove', a2)
      r.fire('pointermove', b2)
    }
    expect(r.view.scale).toBeCloseTo(scale, 9)
    const at = r.view.project(p)
    expect(at.x).toBeCloseTo(480, 3)
    expect(at.y).toBeCloseTo(250, 3)
    expect(state(r.view).back).toEqual(r.view.state().back)
    expect(r.view.roll).toBe(0)
  })

  it('zoom about the midpoint when the spacing changes', () => {
    const p = pointUnder(r.view, 350, 280)
    r.fire('pointerdown', touch(1, 250, 280))
    r.fire('pointerdown', touch(2, 450, 280))
    const scale = r.view.scale
    for (let i = 1; i <= 10; i++) {
      r.fire('pointermove', touch(1, 250 - i * 10, 280))
      r.fire('pointermove', touch(2, 450 + i * 10, 280))
    }
    // The spacing went from 200 to 400 px: twice as large.
    expect(r.view.scale).toBeCloseTo(scale / 2, 6)
    const at = r.view.project(p)
    expect(at.x).toBeCloseTo(350, 3)
    expect(at.y).toBeCloseTo(280, 3)
  })

  function twist(degrees: number): { roll: number; backMoved: number; mid: { x: number; y: number }; scale: number } {
    const p = pointUnder(r.view, 400, 300)
    const back = r.view.state().back
    const [a, b] = twoFingers(0, 100, 400, 300)
    r.fire('pointerdown', a)
    r.fire('pointerdown', b)
    const steps = Math.max(1, Math.round(degrees / 2))
    for (let i = 1; i <= steps; i++) {
      const [a2, b2] = twoFingers(rad((degrees * i) / steps), 100, 400, 300)
      r.fire('pointermove', a2)
      r.fire('pointermove', b2)
    }
    const now = r.view.state().back
    return {
      roll: deg(r.view.roll),
      backMoved: Math.hypot(now[0] - back[0], now[1] - back[1], now[2] - back[2]),
      mid: r.view.project(p),
      scale: r.view.scale,
    }
  }

  it('turned 30 degrees roll the view by 25 to 30 degrees about the screen normal, keeping the midpoint and zoom', () => {
    const t = twist(30)
    expect(t.roll).toBeGreaterThanOrEqual(25)
    expect(t.roll).toBeLessThanOrEqual(30)
    expect(t.backMoved).toBeLessThan(1e-9)
    expect(t.scale).toBeCloseTo(0.25, 9)
    expect(t.mid.x).toBeCloseTo(400, 3)
    expect(t.mid.y).toBeCloseTo(300, 3)
    // The view's up axis turned by the same angle as the roll.
    const up0 = new ViewCamera().axes().up
    const turned = r.view.axes().up
    expect(deg(up0.angleTo(turned))).toBeCloseTo(t.roll, 6)
  })

  it('turned 3 degrees roll nothing, and the dead zone is under 5 degrees', () => {
    expect(deg(TWIST_DEAD_ZONE)).toBeGreaterThan(3)
    expect(deg(TWIST_DEAD_ZONE)).toBeLessThan(5)
    expect(twist(3).roll).toBe(0)
  })

  it('turned the other way roll the other way', () => {
    expect(twist(-30).roll).toBeLessThanOrEqual(-25)
  })

  it('leave the remaining finger idle until every finger is up', () => {
    r.fire('pointerdown', touch(1, 300, 300))
    r.fire('pointerdown', touch(2, 500, 300))
    r.fire('pointerup', touch(2, 500, 300))
    const before = state(r.view)
    r.fire('pointermove', touch(1, 360, 340))
    expect(state(r.view)).toEqual(before)
    r.fire('pointerup', touch(1, 360, 340))
    r.fire('pointerdown', touch(3, 360, 340))
    r.fire('pointermove', touch(3, 420, 340))
    expect(state(r.view).back).not.toEqual(before.back)
  })
})

describe('pen', () => {
  const pen = (type: string, x: number, y: number, extra: PointerEventInit = {}): [string, PointerEventInit] => [
    type,
    { pointerId: 7, pointerType: 'pen', clientX: x, clientY: y, pressure: 0.5, ...extra },
  ]

  it('changes neither orientation, position nor zoom', () => {
    const before = state(r.view)
    r.fire(...pen('pointerdown', 400, 300))
    for (let i = 1; i <= 20; i++) r.fire(...pen('pointermove', 400 + i * 9, 300 + i * 3))
    r.fire(...pen('pointerup', 580, 360))
    expect(state(r.view)).toEqual(before)
    expect(r.host.fit).not.toHaveBeenCalled()
  })

  it('goes to the pen handler: aim while down, commit on release', () => {
    const h: PenHandler = { aim: vi.fn(), commit: vi.fn(), cancel: vi.fn() }
    r.module.setPenHandler(h)
    const before = state(r.view)
    r.fire(...pen('pointermove', 50, 50))
    expect(h.aim).not.toHaveBeenCalled()
    r.fire(...pen('pointerdown', 100, 120))
    r.fire(...pen('pointermove', 110, 130))
    expect(h.aim).toHaveBeenCalledTimes(2)
    expect(h.commit).not.toHaveBeenCalled()
    r.fire(...pen('pointerup', 111, 131))
    expect(h.commit).toHaveBeenCalledWith({ x: 111, y: 131, pressure: 0.5, pointerId: 7 })
    r.fire(...pen('pointerdown', 100, 120))
    r.fire(...pen('pointercancel', 100, 120))
    expect(h.cancel).toHaveBeenCalledTimes(1)
    expect(state(r.view)).toEqual(before)
  })

  it('does not navigate while a finger is also down', () => {
    r.fire(...pen('pointerdown', 100, 100))
    r.fire('pointerdown', touch(1, 400, 300))
    r.fire(...pen('pointermove', 200, 260))
    const back = state(r.view).back
    r.fire(...pen('pointermove', 260, 300))
    expect(state(r.view).back).toEqual(back)
  })
})

describe('mouse', () => {
  const mouse = (id: number, x: number, y: number, button = 0): PointerEventInit => ({
    pointerId: id,
    pointerType: 'mouse',
    clientX: x,
    clientY: y,
    button,
  })

  it('orbits on a right drag about the point under the pointer', () => {
    const before = r.view.project([PIVOT.x, PIVOT.y, PIVOT.z])
    const back = state(r.view).back
    r.fire('pointerdown', mouse(1, 400, 300, 2))
    r.fire('pointermove', mouse(1, 480, 340))
    r.fire('pointerup', mouse(1, 480, 340, 2))
    const after = r.view.project([PIVOT.x, PIVOT.y, PIVOT.z])
    expect(Math.hypot(after.x - before.x, after.y - before.y)).toBeLessThan(1)
    expect(state(r.view).back).not.toEqual(back)
  })

  it('pans on a middle drag', () => {
    const p = pointUnder(r.view, 400, 300)
    r.fire('pointerdown', mouse(1, 400, 300, 1))
    r.fire('pointermove', mouse(1, 440, 330))
    const at = r.view.project(p)
    expect(at.x).toBeCloseTo(440, 6)
    expect(at.y).toBeCloseTo(330, 6)
    expect(r.view.scale).toBe(0.25)
  })

  it('does nothing on a left drag', () => {
    const before = state(r.view)
    r.fire('pointerdown', mouse(1, 400, 300, 0))
    r.fire('pointermove', mouse(1, 480, 340))
    expect(state(r.view)).toEqual(before)
  })

  it('zooms at the cursor with the wheel', () => {
    const p = pointUnder(r.view, 600, 200)
    const e = new WheelEvent('wheel', { clientX: 600, clientY: 200, deltaY: -100, cancelable: true, bubbles: true })
    r.canvas.dispatchEvent(e)
    expect(e.defaultPrevented).toBe(true)
    expect(r.view.scale).toBeLessThan(0.25)
    const at = r.view.project(p)
    expect(at.x).toBeCloseTo(600, 6)
    expect(at.y).toBeCloseTo(200, 6)
  })

  it('keeps the browser menu off a right click', () => {
    const e = new Event('contextmenu', { cancelable: true, bubbles: true })
    r.canvas.dispatchEvent(e)
    expect(e.defaultPrevented).toBe(true)
  })
})

describe('capture', () => {
  it('captures every pointer down by id, pen included', () => {
    r.fire('pointerdown', touch(11, 10, 10))
    r.fire('pointerdown', touch(12, 20, 20))
    r.fire('pointerdown', { pointerId: 13, pointerType: 'pen', clientX: 5, clientY: 5 })
    r.fire('pointerdown', { pointerId: 14, pointerType: 'mouse', button: 2, clientX: 5, clientY: 5 })
    expect(r.capture.mock.calls.map((c) => c[0])).toEqual([11, 12, 13, 14])
  })

  it('still navigates when the browser refuses the capture', () => {
    r.capture.mockImplementation(() => {
      throw new DOMException('no such pointer', 'NotFoundError')
    })
    const back = state(r.view).back
    r.fire('pointerdown', touch(1, 400, 300))
    r.fire('pointermove', touch(1, 460, 300))
    expect(state(r.view).back).not.toEqual(back)
  })
})
