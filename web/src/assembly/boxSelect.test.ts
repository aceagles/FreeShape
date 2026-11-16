import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { Matrix4, Vector3 } from 'three'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PointerModule } from '../input/pointer'
import type { BoxDrag } from '../input/pointer'
import { wireSelection } from '../selection/aimCommit'
import { SelectionStore } from '../selection/store'
import { STANDARD_VIEWS, ViewCamera } from '../viewport/camera'
import { decodeOutputs, isAssembly } from '../wire'
import type { AssemblyOutputs } from '../wire'
import { GizmoController } from './gizmo'
import type { DragSender } from './gizmo'
import { buildAssemblyScene } from './scene'
import { assemblyBoxHits, instanceItem } from './select'

// I9.9: a selection box in an Assembly takes instances, and a left drag on a gizmo handle moves the instance and draws no box.

function hinge(): AssemblyOutputs {
  const b = readFileSync(resolve(process.cwd(), 'fixtures', 'hinge.assembly.fsw'))
  const out = decodeOutputs(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer)
  if (!isAssembly(out)) throw new Error('the fixture is not an Assembly')
  return out
}

function view(): ViewCamera {
  const v = new ViewCamera()
  v.resize(800, 600)
  v.setBack(STANDARD_VIEWS.front)
  v.scale = 0.5
  v.target.set(0, 0, 0)
  return v
}

/** Each drawn instance's box on screen, from its placement and its part's mesh, projected by the camera itself. */
function screenBoxes(outputs: AssemblyOutputs, v: ViewCamera): Map<string, { x0: number; y0: number; x1: number; y1: number }> {
  const out = new Map<string, { x0: number; y0: number; x1: number; y1: number }>()
  for (const inst of outputs.instances) {
    const mesh = inst.part === null ? undefined : outputs.parts[inst.part]?.mesh
    if (!mesh) continue
    const m = new Matrix4().set(...(inst.T.flat() as Parameters<Matrix4['set']>))
    let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity]
    for (let i = 0; i + 2 < mesh.positions.length; i += 3) {
      const w = new Vector3(mesh.positions[i]!, mesh.positions[i + 1]!, mesh.positions[i + 2]!).applyMatrix4(m)
      const p = v.project([w.x, w.y, w.z])
      x0 = Math.min(x0, p.x)
      y0 = Math.min(y0, p.y)
      x1 = Math.max(x1, p.x)
      y1 = Math.max(y1, p.y)
    }
    out.set(inst.id, { x0, y0, x1, y1 })
  }
  return out
}

describe('a box in an Assembly', () => {
  it('left to right takes the instances wholly inside it; right to left also those it crosses', () => {
    const outputs = hinge()
    const built = buildAssemblyScene(outputs)
    const v = view()
    // only the drawn instances (one the eye hides is not in the scene)
    const boxes = new Map([...screenBoxes(outputs, v)].filter(([k]) => built.objects.has(k)))
    expect(boxes.size).toBeGreaterThan(1)
    const [id, b] = [...boxes.entries()].find(([k]) => k === 'I2')!
    const window: BoxDrag = { x0: b.x0 - 1, y0: b.y0 - 1, x1: b.x1 + 1, y1: b.y1 + 1 }
    const inside = (o: { x0: number; y0: number; x1: number; y1: number }) => o.x0 >= window.x0 && o.x1 <= window.x1 && o.y0 >= window.y0 && o.y1 <= window.y1
    const expected = [...boxes].filter(([, o]) => inside(o)).map(([k]) => k)
    expect(expected).toContain(id)
    const got = assemblyBoxHits(outputs, built.objects, v, window)
    expect(got.map((i) => i.instance)).toEqual(expected)
    expect(got[0]).toEqual(instanceItem(id, outputs.instances.find((i) => i.id === id)!.part))
    // a box in the middle of I2 (nothing wholly inside it) takes nothing left to right, and I2 right to left
    const mid = { x: (b.x0 + b.x1) / 2, y: (b.y0 + b.y1) / 2 }
    const small: BoxDrag = { x0: mid.x - 2, y0: mid.y - 2, x1: mid.x + 2, y1: mid.y + 2 }
    expect(assemblyBoxHits(outputs, built.objects, v, small)).toEqual([])
    const crossing = assemblyBoxHits(outputs, built.objects, v, { x0: small.x1, y0: small.y0, x1: small.x0, y1: small.y1 }).map((i) => i.instance)
    expect(crossing).toContain(id)
    // the whole view takes every drawn instance
    expect(assemblyBoxHits(outputs, built.objects, v, { x0: -1e4, y0: -1e4, x1: 1e4, y1: 1e4 }).map((i) => i.instance)).toEqual([...boxes.keys()])
    built.dispose()
  })
})

describe('a left drag on a gizmo handle in Desktop mode', () => {
  let canvas: HTMLCanvasElement
  let module: PointerModule
  afterEach(() => {
    module.dispose()
    canvas.remove()
  })

  it('moves the instance and draws no box; a drag beside it draws one', () => {
    const outputs = hinge()
    const built = buildAssemblyScene(outputs)
    const v = view()
    const selection = new SelectionStore()
    const inst = outputs.instances.find((i) => i.id === 'I2')!
    selection.set([instanceItem(inst.id, inst.part)])
    const sent: string[] = []
    const sender: DragSender = { disabled: false, begin: () => void sent.push('move_begin'), move: () => void sent.push('move_to'), end: async () => void sent.push('move_end'), listen: () => {} }
    let shape: ReturnType<GizmoController['current']> = null
    const gizmo = new GizmoController(v, selection, (s) => (shape = s))
    gizmo.setSender(sender)
    gizmo.setOutputs(outputs)
    canvas = document.createElement('canvas')
    canvas.setPointerCapture = (() => {}) as typeof canvas.setPointerCapture
    document.body.append(canvas)
    module = new PointerModule(canvas, { view: v, pickPoint: () => null, modelCentre: () => v.target, fit: vi.fn(), requestRender: vi.fn() })
    module.setDesktop(true)
    module.setHandleHandler(gizmo)
    const drawn: (BoxDrag | null)[] = []
    wireSelection(module, selection, () => null, { hits: (b) => assemblyBoxHits(outputs, built.objects, v, b), draw: (b) => void drawn.push(b) })
    const fire = (t: string, p: { x: number; y: number }) => canvas.dispatchEvent(new PointerEvent(t, { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse', button: 0, clientX: p.x, clientY: p.y }))
    const x = shape!.axes[0]!
    const from = x.move!
    fire('pointerdown', from)
    for (let k = 1; k <= 4; k++) fire('pointermove', { x: from.x + x.dir!.x * 10 * k, y: from.y + x.dir!.y * 10 * k })
    fire('pointerup', { x: from.x + x.dir!.x * 40, y: from.y + x.dir!.y * 40 })
    expect(sent[0]).toBe('move_begin')
    expect(sent).toContain('move_to')
    expect(drawn).toEqual([])
    expect(selection.getState().items.map((i) => i.instance)).toEqual(['I2'])
    // the same drag in a corner of the view, away from every handle, is a box (and, holding nothing, clears the selection)
    sent.length = 0
    fire('pointerdown', { x: 2, y: 2 })
    fire('pointermove', { x: 20, y: 20 })
    fire('pointerup', { x: 20, y: 20 })
    expect(sent).toEqual([])
    expect(drawn.length).toBeGreaterThan(1)
    expect(drawn.at(-1)).toBeNull()
    expect(selection.getState().items).toEqual([])
    built.dispose()
  })
})
