import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PointerModule } from '../input/pointer'
import { SelectionStore } from '../selection/store'
import { STANDARD_VIEWS, ViewCamera } from '../viewport/camera'
import { decodeOutputs, isAssembly } from '../wire'
import type { AssemblyOutputs } from '../wire'
import { GizmoController, centreOf, hitHandle, layoutOf, parseTyped, readoutText } from './gizmo'
import type { DragSender, GizmoShape } from './gizmo'
import { instanceItem } from './select'
import { makeDragSender } from './useAssembly'

function hinge(): AssemblyOutputs {
  const b = readFileSync(resolve(process.cwd(), 'fixtures', 'hinge.assembly.fsw'))
  const out = decodeOutputs(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer)
  if (!isAssembly(out)) throw new Error('the fixture is not an Assembly')
  return out
}

interface Sent {
  op: string
  instance?: string
  T?: number[][]
}

function rig(select: string) {
  const outputs = hinge()
  const view = new ViewCamera()
  view.resize(800, 600)
  view.setBack(STANDARD_VIEWS.front)
  view.scale = 0.5
  view.target.set(0, 0, 0)
  const selection = new SelectionStore()
  const inst = outputs.instances.find((i) => i.id === select)!
  selection.set([instanceItem(inst.id, inst.part)])
  const sent: Sent[] = []
  const sender: DragSender = {
    disabled: false,
    begin: (instance) => void sent.push({ op: 'move_begin', instance }),
    move: (T) => void sent.push({ op: 'move_to', T }),
    end: async () => void sent.push({ op: 'move_end' }),
    listen: () => {},
  }
  let shape: GizmoShape | null = null
  const gizmo = new GizmoController(view, selection, (s) => (shape = s))
  gizmo.setSender(sender)
  gizmo.setOutputs(outputs)
  return { outputs, view, selection, sent, gizmo, shape: () => shape }
}

describe('the gizmo of an Assembly instance', () => {
  it('shows three arrows and three rings on a free instance, and nothing on a fixed one', () => {
    const free = rig('I2')
    const s = free.shape()!
    expect(s.instance).toBe('I2')
    expect(s.axes.filter((a) => a.move && a.turn).length).toBeGreaterThanOrEqual(2)
    const fixed = rig('I1')
    expect(fixed.outputs.instances.find((i) => i.id === 'I1')!.fixed).toBe(true)
    expect(fixed.shape()).toBeNull()
    // a press where its handles would be takes nothing
    expect(fixed.gizmo.press({ x: 400, y: 300, pressure: 1, pointerId: 1 }, 'touch')).toBe(false)
  })

  it('shows none when no sender is allowed (a read-only Document)', () => {
    const r = rig('I2')
    r.gizmo.setSender({ disabled: true, begin() {}, move() {}, async end() {}, listen() {} })
    expect(r.shape()).toBeNull()
  })
})

describe('dragging the X arrow handle', () => {
  let canvas: HTMLCanvasElement
  let module: PointerModule
  beforeEach(() => {
    canvas = document.createElement('canvas')
    canvas.setPointerCapture = (() => {}) as typeof canvas.setPointerCapture
    document.body.append(canvas)
  })
  afterEach(() => {
    module.dispose()
    canvas.remove()
  })

  it.each(['touch', 'pen', 'mouse'] as const)('with a %s sends move_begin, move_to along X only, then move_end, and does not turn the view', async (type) => {
    const r = rig('I2')
    const orbit = vi.spyOn(r.view, 'orbit')
    const host = { view: r.view, pickPoint: () => null, modelCentre: () => r.view.target, fit: vi.fn(), requestRender: vi.fn() }
    module = new PointerModule(canvas, host as never)
    module.setHandleHandler(r.gizmo)
    const x = r.shape()!.axes[0]!
    expect(x.move && x.dir).toBeTruthy()
    const from = x.move!
    const fire = (t: string, p: { x: number; y: number }) =>
      canvas.dispatchEvent(new PointerEvent(t, { bubbles: true, cancelable: true, pointerId: 7, pointerType: type, button: 0, clientX: p.x, clientY: p.y }))
    fire('pointerdown', from)
    for (let k = 1; k <= 4; k++) fire('pointermove', { x: from.x + x.dir!.x * 10 * k, y: from.y + x.dir!.y * 10 * k })
    fire('pointerup', { x: from.x + x.dir!.x * 40, y: from.y + x.dir!.y * 40 })
    expect(r.sent[0]).toEqual({ op: 'move_begin', instance: 'I2' })
    expect(r.sent.at(-1)).toEqual({ op: 'move_end' })
    const moves = r.sent.filter((s) => s.op === 'move_to')
    expect(moves.length).toBeGreaterThanOrEqual(2)
    for (const m of moves) {
      const T = m.T!
      expect(Math.abs(T[1]![3]!)).toBeLessThan(1e-9)
      expect(Math.abs(T[2]![3]!)).toBeLessThan(1e-9)
      for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) expect(T[i]![j]).toBeCloseTo(i === j ? 1 : 0, 9)
    }
    // 40 px at 0.5 mm per px is 20 mm along the instance's X axis, and it grows with the drag
    const xs = moves.map((m) => m.T![0]![3]!)
    expect(Math.abs(xs.at(-1)!)).toBeGreaterThan(0)
    expect(Math.abs(Math.hypot(...[0, 1, 2].map((i) => moves.at(-1)!.T![i]![3]!)))).toBeCloseTo(20, 1)
    for (let i = 1; i < xs.length; i++) expect(Math.abs(xs[i]!)).toBeGreaterThanOrEqual(Math.abs(xs[i - 1]!) - 1e-9)
    expect(orbit).not.toHaveBeenCalled()
    expect(r.gizmo.readout()?.kind).toBe('move')
  })

  it('a press away from every handle still orbits and sends nothing', () => {
    const r = rig('I2')
    const orbit = vi.spyOn(r.view, 'orbit')
    const host = { view: r.view, pickPoint: () => null, modelCentre: () => r.view.target, fit: vi.fn(), requestRender: vi.fn() }
    module = new PointerModule(canvas, host as never)
    module.setHandleHandler(r.gizmo)
    const fire = (t: string, x: number, y: number) =>
      canvas.dispatchEvent(new PointerEvent(t, { bubbles: true, cancelable: true, pointerId: 3, pointerType: 'touch', clientX: x, clientY: y }))
    fire('pointerdown', 20, 20)
    fire('pointermove', 60, 40)
    fire('pointerup', 60, 40)
    expect(orbit).toHaveBeenCalled()
    expect(r.sent).toEqual([])
  })
})

describe('turning by a ring', () => {
  it('sends a rotation about the instance axis through its origin, with the angle as the value', () => {
    const r = rig('I2')
    const s = r.shape()!
    const ring = s.axes.find((a) => a.turn && a.axis === 2) ?? s.axes.find((a) => a.turn)!
    const p = { x: ring.turn!.x, y: ring.turn!.y, pressure: 1, pointerId: 1 }
    expect(hitHandle(s, p, 'touch')).toEqual({ kind: 'turn', axis: ring.axis })
    expect(r.gizmo.press(p, 'touch')).toBe(true)
    // swing the finger a quarter turn about the origin
    const dx = p.x - s.origin.x
    const dy = p.y - s.origin.y
    r.gizmo.release({ ...p, x: s.origin.x - dy, y: s.origin.y + dx })
    const moves = r.sent.filter((m) => m.op === 'move_to')
    const T = moves.at(-1)!.T!
    // a rigid turn: orthonormal, with the origin of the instance where it was
    const o = layoutOf(r.view, r.outputs.instances.find((i) => i.id === 'I2')!.T, centreOf(r.outputs.parts[r.outputs.instances.find((i) => i.id === 'I2')!.part!])).origin
    expect(o.x).toBeCloseTo(s.origin.x, 6)
    expect(T[0]![0]! ** 2 + T[1]![0]! ** 2 + T[2]![0]! ** 2).toBeCloseTo(1, 9)
    expect(Math.abs(r.gizmo.readout()!.value)).toBeCloseTo(90, 1)
    expect(readoutText('turn', 90)).toBe('90°')
  })
})

describe('a typed value', () => {
  it('reads a distance with its unit and an angle in degrees or radians', () => {
    expect(parseTyped('12.5 mm', 'move')).toBe(12.5)
    expect(parseTyped('1 in', 'move')).toBeCloseTo(25.4, 9)
    expect(parseTyped('-2cm', 'move')).toBe(-20)
    expect(parseTyped('30 deg', 'turn')).toBe(30)
    expect(parseTyped('45°', 'turn')).toBe(45)
    expect(parseTyped(`${Math.PI} rad`, 'turn')).toBeCloseTo(180, 9)
    expect(parseTyped('abc', 'move')).toBeNull()
    expect(parseTyped('3 deg', 'move')).toBeNull()
    expect(parseTyped('2 +', 'move')).toBeNull()
  })

  it('moves by the difference from what the drag showed', async () => {
    const r = rig('I2')
    const s = r.shape()!
    const x = s.axes[0]!
    r.gizmo.press({ x: x.move!.x, y: x.move!.y, pressure: 1, pointerId: 1 }, 'touch')
    r.gizmo.release({ x: x.move!.x + x.dir!.x * 20, y: x.move!.y + x.dir!.y * 20, pressure: 1, pointerId: 1 })
    const shown = r.gizmo.readout()!.value
    r.sent.length = 0
    await r.gizmo.typed(50)
    expect(r.sent.map((m) => m.op)).toEqual(['move_begin', 'move_to', 'move_end'])
    const T = r.sent[1]!.T!
    expect(Math.hypot(T[0]![3]!, T[1]![3]!, T[2]![3]!)).toBeCloseTo(Math.abs(50 - shown), 6)
    expect(r.gizmo.readout()!.value).toBe(50)
  })
})

describe('the drag sender', () => {
  it('sends only the latest move_to while one is in flight, then move_end after the last answer', async () => {
    const posted: Record<string, unknown>[] = []
    const gates: (() => void)[] = []
    const post = (edit: Record<string, unknown>) => {
      posted.push(edit)
      return new Promise<void>((res) => gates.push(res))
    }
    const s = makeDragSender(post, () => {}, false)
    const tick = () => new Promise((r) => setTimeout(r, 0))
    s.begin('I2')
    s.move([[1]])
    s.move([[2]])
    await tick()
    expect(posted.map((p) => p['op'])).toEqual(['move_begin']) // nothing goes before move_begin is answered
    gates.shift()!() // move_begin answered
    await tick()
    expect(posted.map((p) => p['T'] ?? p['op'])).toEqual(['move_begin', [[2]]]) // the newer one, not [[1]]
    s.move([[3]])
    s.move([[4]])
    const ended = s.end()
    gates.shift()!() // [[2]] answered
    await tick()
    expect(posted.at(-1)).toEqual({ op: 'move_to', T: [[4]] }) // [[3]] was dropped
    gates.shift()!()
    await tick()
    expect(posted.at(-1)).toEqual({ op: 'move_end' })
    gates.shift()!()
    await ended
  })

  it('reports a refusal once and sends no more moves', async () => {
    const onError = vi.fn()
    let n = 0
    const post = (edit: Record<string, unknown>) => (edit['op'] === 'move_to' && ++n === 1 ? Promise.reject(new Error('no')) : Promise.resolve())
    const s = makeDragSender(post, onError, false)
    s.begin('I2')
    s.move([[1]])
    await new Promise((r) => setTimeout(r, 0))
    s.move([[2]])
    s.move([[3]])
    await s.end()
    expect(onError).toHaveBeenCalledTimes(1)
    expect(n).toBe(1)
  })
})
