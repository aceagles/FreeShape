import { Vector3 } from 'three'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BOX_SLOP, PointerModule } from '../input/pointer'
import type { BoxDrag } from '../input/pointer'
import { fixture } from '../test/fixtures'
import { studioBoxHits } from '../viewport/boxSelect'
import { ViewCamera, STANDARD_VIEWS } from '../viewport/camera'
import { PickIndex } from '../viewport/pick'
import type { PickHit } from '../viewport/pick'
import { buildScene } from '../viewport/scene'
import type { StudioOutputs } from '../wire'
import { wireSelection } from './aimCommit'
import { BADGE_OFFSET, MouseOverlay } from './mouseOverlay'
import { SelectionStore } from './store'

// I9.9: the mouse in Desktop mode, on the Bracket with a real picker and a fixed camera (1024 x 768, iso, fitted).
// Events are synthetic Pointer Events on a canvas; the box's hits are checked against a projection of their own.

interface Rig {
  canvas: HTMLCanvasElement
  view: ViewCamera
  outputs: StudioOutputs
  selection: SelectionStore
  pointer: PointerModule
  index: PickIndex
  drawn: (BoxDrag | null)[]
  fire: (type: string, init: PointerEventInit) => void
}

function rig(desktop = true): Rig {
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
  pointer.setDesktop(desktop)
  const selection = new SelectionStore()
  const drawn: (BoxDrag | null)[] = []
  wireSelection(pointer, selection, (x, y, kind, filter) => index.pick(view, x, y, kind, filter), {
    hits: (b, filter) => studioBoxHits(outputs, new Set(index.partIds), view, b, filter),
    draw: (b) => void drawn.push(b),
  })
  const fire = (type: string, init: PointerEventInit) => canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, ...init }))
  return { canvas, view, outputs, selection, pointer, index, drawn, fire }
}

let r: Rig
beforeEach(() => {
  r = rig()
})
afterEach(() => {
  r.pointer.dispose()
  r.canvas.remove()
})

const mouse = (x: number, y: number, keys: { shift?: boolean; ctrl?: boolean } = {}): PointerEventInit => ({
  pointerId: 20,
  pointerType: 'mouse',
  clientX: x,
  clientY: y,
  button: 0,
  shiftKey: keys.shift ?? false,
  ctrlKey: keys.ctrl ?? false,
})

function click(rg: Rig, p: { x: number; y: number }, keys: { shift?: boolean; ctrl?: boolean } = {}) {
  rg.fire('pointermove', mouse(p.x, p.y, keys))
  rg.fire('pointerdown', mouse(p.x, p.y, keys))
  rg.fire('pointerup', mouse(p.x, p.y, keys))
}

/** A left drag from `a` to `b` in a few moves, released with these keys held. */
function drag(rg: Rig, a: { x: number; y: number }, b: { x: number; y: number }, keys: { shift?: boolean; ctrl?: boolean } = {}) {
  rg.fire('pointerdown', mouse(a.x, a.y))
  for (let i = 1; i <= 4; i++) rg.fire('pointermove', mouse(a.x + ((b.x - a.x) * i) / 4, a.y + ((b.y - a.y) * i) / 4, keys))
  rg.fire('pointerup', mouse(b.x, b.y, keys))
}

/** One spot per face where the mouse picks that face, and a spot with nothing under it. */
function spots(rg: Rig): { faces: { x: number; y: number; hit: PickHit }[]; empty: { x: number; y: number } } {
  const faces = new Map<number, { x: number; y: number; hit: PickHit }>()
  let empty: { x: number; y: number } | null = null
  for (let y = 10; y < 768; y += 14) {
    for (let x = 10; x < 1024; x += 14) {
      const hit = rg.index.pick(rg.view, x, y, 'mouse')
      if (hit === null) empty ??= { x, y }
      else if (hit.kind === 'face' && !faces.has(hit.index)) faces.set(hit.index, { x, y, hit })
    }
  }
  if (!empty) throw new Error('no empty spot in the view')
  return { faces: [...faces.values()], empty }
}

const ids = (rg: Rig) => rg.selection.getState().items.map((i) => i.id)

describe('a click in Desktop mode', () => {
  it('on two faces selects both; again on the first leaves the second; on empty space clears; with Shift or Ctrl it does not', () => {
    const { faces, empty } = spots(r)
    const [a, b] = faces
    click(r, a!)
    click(r, b!)
    expect(r.selection.getState().items.map((i) => `${i.kind}:${i.index}`)).toEqual([`face:${a!.hit.index}`, `face:${b!.hit.index}`])
    click(r, a!)
    expect(r.selection.getState().items.map((i) => `${i.kind}:${i.index}`)).toEqual([`face:${b!.hit.index}`])
    click(r, a!)
    click(r, empty, { shift: true })
    expect(r.selection.getState().items).toHaveLength(2)
    click(r, empty, { ctrl: true })
    expect(r.selection.getState().items).toHaveLength(2)
    click(r, empty)
    expect(r.selection.getState().items).toHaveLength(0)
    // a click is not a drag: no box was drawn
    expect(r.drawn).toEqual([])
  })

  it('moving less than the box slop is still a click', () => {
    const { faces } = spots(r)
    const a = faces[0]!
    r.fire('pointerdown', mouse(a.x, a.y))
    r.fire('pointermove', mouse(a.x + BOX_SLOP - 1, a.y))
    r.fire('pointerup', mouse(a.x + BOX_SLOP - 1, a.y))
    expect(r.drawn).toEqual([])
    expect(r.selection.getState().items).toHaveLength(1)
  })
})

describe('a box in Desktop mode', () => {
  /** The bracket's edges on screen, projected by the camera itself (not by the code under test). */
  function edgesOnScreen(rg: Rig): { key: string; pts: { x: number; y: number }[] }[] {
    const out: { key: string; pts: { x: number; y: number }[] }[] = []
    for (const part of rg.outputs.parts) {
      part.mesh!.edges.forEach((poly, e) => {
        // a polyline of one point is not boxed, as on the desktop (`len(poly) >= 2`)
        if (poly.length < 6) return
        const pts: { x: number; y: number }[] = []
        for (let k = 0; k + 2 < poly.length; k += 3) pts.push(rg.view.project([poly[k]!, poly[k + 1]!, poly[k + 2]!]))
        out.push({ key: `${part.id}#${e}`, pts })
      })
    }
    return out
  }

  /** A box over the middle of the model: some edges wholly inside it, some crossing it. */
  function middleBox(rg: Rig): { x0: number; y0: number; x1: number; y1: number } {
    const all = edgesOnScreen(rg).flatMap((e) => e.pts)
    const xs = all.map((p) => p.x)
    const ys = all.map((p) => p.y)
    const [lx, hx, ly, hy] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
    return { x0: lx + (hx - lx) * 0.2, y0: ly + (hy - ly) * 0.15, x1: lx + (hx - lx) * 0.8, y1: ly + (hy - ly) * 0.85 }
  }

  const inBox = (p: { x: number; y: number }, b: { x0: number; y0: number; x1: number; y1: number }) => p.x >= b.x0 && p.x <= b.x1 && p.y >= b.y0 && p.y <= b.y1
  const edgeKeys = (rg: Rig) => rg.selection.getState().items.filter((i) => i.kind === 'edge').map((i) => `${i.part}#${i.index}`).sort()

  it('dragged left to right selects only the edges wholly inside it; right to left also those it crosses', () => {
    r.selection.setPickFilter(['edge'])
    const b = middleBox(r)
    const edges = edgesOnScreen(r)
    const inside = edges.filter((e) => e.pts.every((p) => inBox(p, b))).map((e) => e.key).sort()
    const touching = edges.filter((e) => e.pts.some((p) => inBox(p, b))).map((e) => e.key)
    expect(inside.length).toBeGreaterThan(0)
    expect(inside.length).toBeLessThan(edges.length)

    drag(r, { x: b.x0, y: b.y0 }, { x: b.x1, y: b.y1 })
    expect(edgeKeys(r)).toEqual(inside)
    // the box was drawn while dragged, then taken away
    expect(r.drawn.at(-1)).toBeNull()
    expect(r.drawn.some((d) => d !== null && d.x1 > d.x0)).toBe(true)

    drag(r, { x: b.x1, y: b.y0 }, { x: b.x0, y: b.y1 })
    const crossing = edgeKeys(r)
    // everything wholly inside, every edge with a point inside, and more than the window took
    for (const k of [...inside, ...touching]) expect(crossing).toContain(k)
    expect(crossing.length).toBeGreaterThan(inside.length)
  })

  it('with Shift (or Ctrl) adds to the selection instead of replacing it', () => {
    const { faces } = spots(r)
    click(r, faces[0]!)
    const face = ids(r)[0]!
    r.selection.setPickFilter(['edge'])
    const b = middleBox(r)
    drag(r, { x: b.x0, y: b.y0 }, { x: b.x1, y: b.y1 }, { shift: true })
    expect(ids(r)[0]).toBe(face)
    const n = ids(r).length
    expect(n).toBeGreaterThan(1)
    // again with Ctrl: what is already selected stays once, in its place
    drag(r, { x: b.x0, y: b.y0 }, { x: b.x1, y: b.y1 }, { ctrl: true })
    expect(ids(r)).toHaveLength(n)
    // and without a key it replaces: the face is gone
    drag(r, { x: b.x0, y: b.y0 }, { x: b.x1, y: b.y1 })
    expect(ids(r)).not.toContain(face)
    expect(ids(r)).toHaveLength(n - 1)
  })

  it('takes faces and sketch regions by the desktop defaults, and nothing while a dialog has the view', () => {
    const all = { x: 0, y: 0 }
    const end = { x: 1023, y: 767 }
    drag(r, all, end)
    const kinds = new Set(r.selection.getState().items.map((i) => i.kind))
    expect(kinds).toEqual(new Set(['face', 'edge']))
    const faces = r.outputs.parts.reduce((n, p) => n + p.mesh!.faceRanges.length, 0)
    const edges = r.outputs.parts.reduce((n, p) => n + p.mesh!.edges.filter((e) => e.length >= 6).length, 0)
    expect(r.selection.getState().items).toHaveLength(faces + edges)
    r.selection.clear()
    r.selection.enterDialog()
    r.drawn.length = 0
    drag(r, all, end)
    expect(r.drawn).toEqual([])
    r.selection.leaveDialog()
    expect(r.selection.getState().items).toHaveLength(0)
  })
})

describe('Tablet mode', () => {
  it('a mouse left drag draws no box and a click replaces the selection, as before', () => {
    r.pointer.dispose()
    r.canvas.remove()
    r = rig(false)
    const { faces, empty } = spots(r)
    click(r, faces[0]!)
    click(r, faces[1]!)
    expect(r.selection.getState().items.map((i) => i.index)).toEqual([faces[1]!.hit.index])
    drag(r, { x: 0, y: 0 }, { x: 1023, y: 767 })
    expect(r.drawn).toEqual([])
    click(r, empty, { shift: true })
    expect(r.selection.getState().items).toHaveLength(0)
  })

  it('a pen Commit and a finger tap select as before', () => {
    r.pointer.dispose()
    r.canvas.remove()
    r = rig(false)
    const { faces } = spots(r)
    const a = faces[0]
    // what the pen and the finger pick there (their reach is wider than the mouse's); the finger's spot picks something else
    const byPen = r.index.pick(r.view, a!.x, a!.y, 'pen')!
    const b = faces.find((f) => {
      const h = r.index.pick(r.view, f.x, f.y, 'touch')
      return h !== null && (h.kind !== byPen.kind || h.index !== byPen.index || h.part !== byPen.part)
    })
    const byFinger = r.index.pick(r.view, b!.x, b!.y, 'touch')!
    r.fire('pointerdown', { pointerId: 7, pointerType: 'pen', clientX: a!.x, clientY: a!.y, pressure: 0.5 })
    r.fire('pointerup', { pointerId: 7, pointerType: 'pen', clientX: a!.x, clientY: a!.y, pressure: 0 })
    expect(r.selection.getState().items.map((i) => `${i.kind}:${i.index}`)).toEqual([`${byPen.kind}:${byPen.index}`])
    r.fire('pointerdown', { pointerId: 3, pointerType: 'touch', clientX: b!.x, clientY: b!.y })
    r.fire('pointerup', { pointerId: 3, pointerType: 'touch', clientX: b!.x, clientY: b!.y })
    expect(r.selection.getState().items.map((i) => `${i.kind}:${i.index}`)).toEqual([`${byFinger.kind}:${byFinger.index}`])
  })

  it('switching to Tablet in the middle of a box drops it', () => {
    r.fire('pointerdown', mouse(0, 0))
    r.fire('pointermove', mouse(40, 40))
    expect(r.drawn.at(-1)).not.toBeNull()
    r.pointer.setDesktop(false)
    expect(r.drawn.at(-1)).toBeNull()
  })
})

describe('the count badge', () => {
  it('shows 3 beside the cursor with three items selected and is hidden with one', () => {
    const parent = document.createElement('div')
    document.body.append(parent)
    const o = new MouseOverlay(parent)
    const badge = parent.querySelector<HTMLElement>('[data-testid="select-count-badge"]')!
    o.setEnabled(true)
    o.cursor({ x: 200, y: 150 })
    o.setCount(3)
    expect(badge.hidden).toBe(false)
    expect(badge.textContent).toBe('3')
    expect(badge.style.left).toBe(`${200 + BADGE_OFFSET.x}px`)
    expect(badge.style.top).toBe(`${150 + BADGE_OFFSET.y}px`)
    o.cursor({ x: 300, y: 10 })
    expect(badge.style.left).toBe(`${300 + BADGE_OFFSET.x}px`)
    o.setCount(1)
    expect(badge.hidden).toBe(true)
    // off the view, or in Tablet mode: none
    o.setCount(3)
    o.cursor(null)
    expect(badge.hidden).toBe(true)
    o.cursor({ x: 1, y: 1 })
    o.setEnabled(false)
    expect(badge.hidden).toBe(true)
    o.dispose()
    parent.remove()
  })

  it('draws the box blue left to right and dashed green (data-crossing) right to left', () => {
    const parent = document.createElement('div')
    const o = new MouseOverlay(parent)
    const box = parent.querySelector<HTMLElement>('[data-testid="select-box"]')!
    o.setEnabled(true)
    o.showBox({ x0: 10, y0: 20, x1: 110, y1: 70 })
    expect([box.hidden, box.style.left, box.style.top, box.style.width, box.style.height, 'crossing' in box.dataset]).toEqual([false, '10px', '20px', '100px', '50px', false])
    o.showBox({ x0: 110, y0: 70, x1: 10, y1: 20 })
    expect([box.style.left, box.style.top, 'crossing' in box.dataset]).toEqual(['10px', '20px', true])
    o.showBox(null)
    expect(box.hidden).toBe(true)
  })

  it('the pointer module tells it where the mouse is, and that it left', () => {
    const at: ({ x: number; y: number } | null)[] = []
    r.pointer.setCursorHandler((p) => void at.push(p))
    r.fire('pointermove', mouse(30, 40))
    r.fire('pointerleave', mouse(30, 40))
    expect(at).toEqual([{ x: 30, y: 40 }, null])
  })
})
