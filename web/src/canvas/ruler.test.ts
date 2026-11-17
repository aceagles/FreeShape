import { Vector3 } from 'three'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PointerModule } from '../input/pointer'
import type { PenHandler } from '../input/pointer'
import { ViewCamera } from '../viewport/camera'
import { canvasSpec } from '../viewport/canvases'
import type { CanvasSpec } from '../viewport/canvases'
import type { Canvas } from '../wire'
import { endOnScreen, RulerTool, snapAngle } from './ruler'
import type { End } from './ruler'

// The ruler of Calibrate (card I12.6) on the real pointer module: events in, the ends and the request out. The view looks straight
// down on a 100 x 50 mm canvas centred on the origin at 0.25 mm per pixel: 4 px to the mm, the picture spans x 200..600, y 200..400.

const CENTRE = { x: 400, y: 300 }
const px = (u: number, v = 0) => ({ x: CENTRE.x + u * 4, y: CENTRE.y - v * 4 })

const wire = (over: Partial<Canvas> = {}): Canvas => ({
  id: 'C1',
  name: 'Canvas 1',
  blob: 'k',
  origin: [0, 0, 0],
  xdir: [1, 0, 0],
  ydir: [0, 1, 0],
  normal: [0, 0, 1],
  width: 100,
  height: 50,
  opacity: 50,
  flipH: false,
  flipV: false,
  selectable: true,
  hidden: false,
  filename: 'p.png',
  pixels: [],
  ...over,
})

interface Rig {
  el: HTMLCanvasElement
  view: ViewCamera
  module: PointerModule
  tool: RulerTool
  fire: (type: string, init: PointerEventInit & { t?: number }) => void
  spec: { current: CanvasSpec | null }
}

function rig(spec: CanvasSpec | null = canvasSpec(wire()), look?: (v: ViewCamera) => void): Rig {
  const view = new ViewCamera()
  view.resize(800, 600)
  view.scale = 0.25
  view.setBack([0, 0, 1])
  view.target.set(0, 0, 0)
  look?.(view)
  view.apply()
  const el = document.createElement('canvas')
  el.setPointerCapture = (() => {}) as unknown as typeof el.setPointerCapture
  document.body.append(el)
  const module = new PointerModule(el, { view, pickPoint: () => null, modelCentre: () => new Vector3(), fit: vi.fn(), requestRender: vi.fn() })
  const holder = { current: spec }
  const tool = new RulerTool({ pointer: module, view, canvas: () => holder.current })
  const fire = (type: string, init: PointerEventInit & { t?: number }) => {
    const e = new PointerEvent(type, { bubbles: true, cancelable: true, ...init })
    if (init.t !== undefined) Object.defineProperty(e, 'timeStamp', { value: init.t })
    el.dispatchEvent(e)
  }
  return { el, view, module, tool, fire, spec: holder }
}

let r: Rig
beforeEach(() => {
  r = rig()
  r.tool.start()
})
afterEach(() => {
  r.tool.stop()
  r.module.dispose()
  r.el.remove()
})

const mouse = (x: number, y: number, extra: PointerEventInit & { t?: number } = {}): PointerEventInit & { t?: number } => ({ pointerId: 1, pointerType: 'mouse', clientX: x, clientY: y, button: 0, ...extra })
const click = (p: { x: number; y: number }, t = 0, extra: PointerEventInit = {}) => {
  r.fire('pointermove', mouse(p.x, p.y, { t, ...extra }))
  r.fire('pointerdown', mouse(p.x, p.y, { t, ...extra }))
  r.fire('pointerup', mouse(p.x, p.y, { t: t + 40, ...extra }))
}
const pen = (x: number, y: number, extra: PointerEventInit & { t?: number } = {}): PointerEventInit & { t?: number } => ({ pointerId: 7, pointerType: 'pen', clientX: x, clientY: y, pressure: 0.5, ...extra })
const finger = (id: number, x: number, y: number, t?: number): PointerEventInit & { t?: number } => ({ pointerId: id, pointerType: 'touch', clientX: x, clientY: y, t })
const near = (a: End | null, u: number, v: number) => {
  expect(a).not.toBeNull()
  expect(a!.u).toBeCloseTo(u, 6)
  expect(a!.v).toBeCloseTo(v, 6)
}

describe('mouse: click, move, click', () => {
  it('two clicks on the canvas, 40 mm apart, give the ends in the canvas frame and the request with the typed distance', () => {
    click(px(-20))
    expect(r.tool.state.phase).toBe('second')
    // the line and its length follow the mouse
    r.fire('pointermove', mouse(px(20).x, px(20).y))
    near(r.tool.state.b, 20, 0)
    expect(r.tool.state.length).toBeCloseTo(40, 6)
    expect(r.tool.state.phase).toBe('second')
    click(px(20), 1000)
    expect(r.tool.state.phase).toBe('set')
    expect(r.tool.body(50, false, false)).toEqual({ p0: [expect.closeTo(-20, 6), expect.closeTo(0, 6)], p1: [expect.closeTo(20, 6), expect.closeTo(0, 6)], distance: 50, centre: false, snap: false })
  })

  it('a click off the canvas sets no end, and says so', () => {
    click({ x: 100, y: 100 })
    expect(r.tool.state.a).toBeNull()
    expect(r.tool.state.phase).toBe('first')
    expect(r.tool.state.note).toMatch(/on the picture/)
    click(px(-20), 1000)
    click({ x: 700, y: 550 }, 2000)
    expect(r.tool.state.phase).toBe('second')
    expect(r.tool.state.b).toBeNull()
  })

  it('with no canvas to measure on (a miss of everything) a click does nothing', () => {
    r.spec.current = null
    click(px(-20))
    expect(r.tool.state.a).toBeNull()
  })

  it('press, drag and release is the pen\'s gesture on the mouse too', () => {
    r.fire('pointerdown', mouse(px(-20).x, px(-20).y))
    r.fire('pointermove', mouse(px(0).x, px(0).y))
    r.fire('pointermove', mouse(px(20).x, px(20).y))
    r.fire('pointerup', mouse(px(20).x, px(20).y, { t: 500 }))
    expect(r.tool.state.phase).toBe('set')
    near(r.tool.state.a, -20, 0)
    near(r.tool.state.b, 20, 0)
  })

  it('Shift snaps a 7 degree line to 5 degrees and keeps its length', () => {
    click(px(-20))
    const len = 40
    const end = { u: -20 + len * Math.cos((7 * Math.PI) / 180), v: len * Math.sin((7 * Math.PI) / 180) }
    r.fire('pointermove', mouse(px(end.u, end.v).x, px(end.u, end.v).y, { shiftKey: true }))
    const { a, b } = r.tool.state
    expect((Math.atan2(b!.v - a!.v, b!.u - a!.u) * 180) / Math.PI).toBeCloseTo(5, 6)
    expect(r.tool.state.length).toBeCloseTo(len, 6)
    // without Shift the line is as drawn
    r.fire('pointermove', mouse(px(end.u, end.v).x, px(end.u, end.v).y))
    expect((Math.atan2(r.tool.state.b!.v - a!.v, r.tool.state.b!.u - a!.u) * 180) / Math.PI).toBeCloseTo(7, 6)
    // and Ctrl is nobody's: the line is as drawn
    r.fire('pointermove', mouse(px(end.u, end.v).x, px(end.u, end.v).y, { ctrlKey: true }))
    expect((Math.atan2(r.tool.state.b!.v - a!.v, r.tool.state.b!.u - a!.u) * 180) / Math.PI).toBeCloseTo(7, 6)
  })

  it('two clicks close together are two clicks in Desktop mode, not a double click', () => {
    r.module.setDesktop(true)
    // 2 px apart, 100 ms apart: the pointer module's double click (a second press within 400 ms and 6 px) when a handler has `doubleClick`
    click(px(-20), 1000)
    click({ x: px(-20).x + 2, y: px(-20).y }, 1100)
    // it was a click: the second end was tried and refused as the same point (a double click would have placed and said nothing)
    expect(r.tool.state.note).toMatch(/same point/)
    expect(r.tool.state.phase).toBe('second')
    // the third click, far away, places the second end
    click(px(20), 2000)
    expect(r.tool.state.phase).toBe('set')
  })

  it('the right button still orbits and the wheel zooms: no end is set by them', () => {
    const before = r.view.scale
    r.fire('pointerdown', mouse(400, 300, { pointerId: 2, button: 2 }))
    r.fire('pointermove', mouse(420, 310, { pointerId: 2 }))
    r.fire('pointerup', mouse(420, 310, { pointerId: 2, button: 2 }))
    r.el.dispatchEvent(new WheelEvent('wheel', { deltaY: -100, clientX: 400, clientY: 300, cancelable: true, bubbles: true }))
    expect(r.view.scale).toBeLessThan(before)
    expect(r.tool.state.a).toBeNull()
  })
})

describe('pen: down on the first end, drag, lift on the second; or tap, tap', () => {
  it('a drag from the first end to the second gives the same request as two clicks', () => {
    r.fire('pointerdown', pen(px(-20).x, px(-20).y))
    expect(r.tool.state.loupe).toEqual({ x: px(-20).x, y: px(-20).y })
    r.fire('pointermove', pen(px(0).x, px(0).y))
    r.fire('pointermove', pen(px(20).x, px(20).y))
    // while the pen is down the line follows it and the second end is not yet placed
    expect(r.tool.state.phase).toBe('first')
    expect(r.tool.state.length).toBeCloseTo(40, 6)
    r.fire('pointerup', pen(px(20).x, px(20).y, { pressure: 0 }))
    expect(r.tool.state.phase).toBe('set')
    expect(r.tool.state.loupe).toBeNull()
    expect(r.tool.body(50, false, false)).toEqual({ p0: [expect.closeTo(-20, 6), expect.closeTo(0, 6)], p1: [expect.closeTo(20, 6), expect.closeTo(0, 6)], distance: 50, centre: false, snap: false })
  })

  it('tap, tap places the two ends on release', () => {
    r.fire('pointerdown', pen(px(-20).x, px(-20).y))
    r.fire('pointerup', pen(px(-20).x, px(-20).y, { pressure: 0 }))
    expect(r.tool.state.phase).toBe('second')
    near(r.tool.state.a, -20, 0)
    // the second press: the end follows the pen and lands where it lifts
    r.fire('pointerdown', pen(px(10).x, px(10).y))
    r.fire('pointermove', pen(px(20).x, px(20).y))
    expect(r.tool.state.phase).toBe('second')
    r.fire('pointerup', pen(px(20).x, px(20).y, { pressure: 0 }))
    expect(r.tool.state.phase).toBe('set')
    near(r.tool.state.b, 20, 0)
  })

  it('a pen that lifts off the picture places no second end; a cancelled press places nothing', () => {
    r.fire('pointerdown', pen(px(-20).x, px(-20).y))
    r.fire('pointerup', pen(px(-20).x, px(-20).y))
    r.fire('pointerdown', pen(px(0).x, px(0).y))
    r.fire('pointerup', pen(50, 50))
    expect(r.tool.state.phase).toBe('second')
    expect(r.tool.state.b).toBeNull()
    r.fire('pointerdown', pen(px(0).x, px(0).y))
    r.fire('pointercancel', pen(px(0).x, px(0).y))
    expect(r.tool.state.phase).toBe('second')
    expect(r.tool.state.b).toBeNull()
  })

  it('nothing relies on hover: a pen moving above the glass (no press) does nothing', () => {
    r.fire('pointermove', pen(px(-20).x, px(-20).y))
    expect(r.tool.state.a).toBeNull()
    expect(r.tool.state.hover).toBeNull()
  })
})

describe('finger: taps set the ends, two fingers navigate', () => {
  it('two one-finger taps, even quick ones (not a double tap that fits the view), set the two ends', () => {
    const fit = vi.spyOn(r.view, 'fit' as never)
    r.fire('pointerdown', finger(5, px(-20).x, px(-20).y, 100))
    r.fire('pointerup', finger(5, px(-20).x, px(-20).y, 150))
    expect(r.tool.state.phase).toBe('second')
    r.fire('pointerdown', finger(5, px(20).x, px(20).y, 200))
    r.fire('pointerup', finger(5, px(20).x, px(20).y, 250))
    expect(r.tool.state.phase).toBe('set')
    near(r.tool.state.b, 20, 0)
    expect(fit).not.toHaveBeenCalled()
  })

  it('a two-finger drag moves the camera and sets no end', () => {
    const before = r.view.target.clone()
    r.fire('pointerdown', finger(1, 380, 300, 0))
    r.fire('pointerdown', finger(2, 420, 300, 10))
    for (let i = 1; i <= 6; i++) {
      r.fire('pointermove', finger(1, 380 + i * 10, 300 + i * 4, 20 + i))
      r.fire('pointermove', finger(2, 420 + i * 10, 300 + i * 4, 20 + i))
    }
    r.fire('pointerup', finger(1, 440, 324, 100))
    r.fire('pointerup', finger(2, 480, 324, 100))
    expect(r.view.target.distanceTo(before)).toBeGreaterThan(1)
    expect(r.tool.state.a).toBeNull()
    expect(r.tool.state.phase).toBe('first')
  })
})

describe('both ends set: each can be dragged until the distance is accepted', () => {
  beforeEach(() => {
    click(px(-20))
    click(px(20), 1000)
  })

  it('a mouse press near an end takes it; the line and its length follow, and the other end stays', () => {
    r.fire('pointerdown', mouse(px(20).x + 3, px(20).y))
    r.fire('pointermove', mouse(px(30).x, px(30, 5).y))
    near(r.tool.state.b, 30, 5)
    expect(r.tool.state.length).toBeCloseTo(Math.hypot(50, 5), 6)
    r.fire('pointerup', mouse(px(30).x, px(30, 5).y, { t: 3000 }))
    near(r.tool.state.a, -20, 0)
    expect(r.tool.state.phase).toBe('set')
  })

  it('a pen takes an end from farther away and shows the magnifier while it drags; the end stays on the picture', () => {
    r.fire('pointerdown', pen(px(-20).x - 20, px(-20).y))
    expect(r.tool.state.loupe).not.toBeNull()
    r.fire('pointermove', pen(px(-70).x, px(-70).y))
    // dragged off the picture's edge: it stops at the edge
    near(r.tool.state.a, -50, 0)
    r.fire('pointerup', pen(px(-70).x, px(-70).y, { pressure: 0 }))
    expect(r.tool.state.loupe).toBeNull()
  })

  it('a press away from both ends does nothing', () => {
    r.fire('pointerdown', mouse(px(0).x, px(0, 15).y))
    r.fire('pointerup', mouse(px(0).x, px(0, 15).y, { t: 2000 }))
    near(r.tool.state.a, -20, 0)
    near(r.tool.state.b, 20, 0)
  })

  it('dragging an end onto the other keeps it where it was', () => {
    r.fire('pointerdown', mouse(px(20).x, px(20).y))
    r.fire('pointermove', mouse(px(-20).x, px(-20).y))
    r.fire('pointerup', mouse(px(-20).x, px(-20).y, { t: 3000 }))
    near(r.tool.state.b, 20, 0)
  })
})

describe('the pointer module gets its handlers back', () => {
  it('stop puts the selection\'s handlers where they were, and a slot something else took is left alone', () => {
    r.tool.stop()
    const seen: string[] = []
    const mine: PenHandler = { aim: () => seen.push('aim'), commit: () => seen.push('commit'), cancel: () => {} }
    r.module.setMouseHandler(mine)
    r.tool.start()
    click(px(-20))
    expect(seen).toEqual([])
    r.tool.stop()
    click(px(-20), 3000)
    expect(seen).toContain('commit')
  })
})

describe('the frame: a screen point is the canvas point the server takes as p0 and p1', () => {
  // freeshape/canvas_calibrate.py `_axes`: the plane turned `rotation` degrees counter-clockwise, then each axis reversed by its flip
  const axes = (rotation: number, flipH: boolean, flipV: boolean) => {
    const c = Math.cos((rotation * Math.PI) / 180)
    const s = Math.sin((rotation * Math.PI) / 180)
    let xd: [number, number, number] = [c, s, 0]
    let yd: [number, number, number] = [-s, c, 0]
    if (flipH) xd = [-xd[0], -xd[1], 0]
    if (flipV) yd = [-yd[0], -yd[1], 0]
    return { xd, yd }
  }

  for (const [rotation, flipH, flipV] of [
    [0, false, false],
    [30, false, false],
    [30, true, false],
    [-70, false, true],
    [115, true, true],
  ] as const) {
    it(`rotation ${rotation}, flip H ${flipH}, flip V ${flipV}, seen from an oblique view`, () => {
      const { xd, yd } = axes(rotation, flipH, flipV)
      const spec = canvasSpec(wire({ origin: [12, -7, 0], xdir: xd, ydir: yd, flipH, flipV }))
      const oblique = rig(spec, (v) => {
        v.setBack([0.4, -0.5, 0.75])
        v.target.set(12, -7, 0)
        v.apply()
      })
      try {
        oblique.tool.start()
        // two frame points; each is put on screen from the PLANE point it stands for (the server's own frame), never through the ruler's code
        const frame: [number, number][] = [
          [-18, 9],
          [22, -11],
        ]
        const at = frame.map(([u, v]) => oblique.view.project([12 + u * xd[0] + v * yd[0], -7 + u * xd[1] + v * yd[1], 0]))
        for (const [i, p] of at.entries()) {
          oblique.fire('pointermove', mouse(p.x, p.y, { t: i * 1000 }))
          oblique.fire('pointerdown', mouse(p.x, p.y, { t: i * 1000 }))
          oblique.fire('pointerup', mouse(p.x, p.y, { t: i * 1000 + 30 }))
        }
        const body = oblique.tool.body(10, true, true)!
        expect(body.p0[0]).toBeCloseTo(-18, 4)
        expect(body.p0[1]).toBeCloseTo(9, 4)
        expect(body.p1[0]).toBeCloseTo(22, 4)
        expect(body.p1[1]).toBeCloseTo(-11, 4)
        expect(body.centre).toBe(true)
        expect(body.snap).toBe(true)
        // and the other way round: the end is drawn where its frame point is
        const back = endOnScreen(spec, oblique.view, { u: -18, v: 9 })
        expect(back.x).toBeCloseTo(at[0]!.x, 4)
        expect(back.y).toBeCloseTo(at[0]!.y, 4)
      } finally {
        oblique.tool.stop()
        oblique.module.dispose()
        oblique.el.remove()
      }
    })
  }
})

describe('snapAngle', () => {
  it('turns the end to the nearest 5 degrees about the other, keeping the length', () => {
    const e = snapAngle({ u: 1, v: 1 }, { u: 1 + 10 * Math.cos((7 * Math.PI) / 180), v: 1 + 10 * Math.sin((7 * Math.PI) / 180) })
    expect(Math.atan2(e.v - 1, e.u - 1)).toBeCloseTo((5 * Math.PI) / 180, 9)
    expect(Math.hypot(e.u - 1, e.v - 1)).toBeCloseTo(10, 9)
    // 2.4 degrees goes down to 0 and 2.6 up to 5; a line of no length stays
    expect(snapAngle({ u: 0, v: 0 }, { u: 10 * Math.cos((2.4 * Math.PI) / 180), v: 10 * Math.sin((2.4 * Math.PI) / 180) }).v).toBeCloseTo(0, 9)
    expect(snapAngle({ u: 0, v: 0 }, { u: 0, v: 0 })).toEqual({ u: 0, v: 0 })
  })
})
