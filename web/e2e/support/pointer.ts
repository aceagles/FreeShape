import type { Page } from '@playwright/test'
import { viewCanvas } from './viewport.ts'

/**
 * Pointer sequences for the view. WebKit under Playwright has no touch or pen input device, so they are
 * PointerEvents dispatched on the canvas from page.evaluate, with `pointerType` 'touch', 'pen' or 'mouse' and a
 * distinct `pointerId` for each contact. Positions are CSS pixels from the canvas's top-left corner.
 */
export type PointerKind = 'touch' | 'pen' | 'mouse'
export type PointerPhase = 'down' | 'move' | 'up' | 'cancel'

export interface PointerStep {
  phase: PointerPhase
  kind: PointerKind
  id: number
  x: number
  y: number
  /** Mouse button: 0 left, 1 middle, 2 right. Ignored for touch and pen. */
  button?: number
  pressure?: number
}

export interface Point {
  x: number
  y: number
}

/** Dispatches the steps in order on the canvas, one task per step so the page can run between them. */
export async function sendPointer(page: Page, steps: PointerStep[]): Promise<void> {
  const box = await viewCanvas(page).boundingBox()
  if (!box) throw new Error('the view canvas has no box')
  await page.evaluate(
    async ({ steps, left, top }) => {
      const canvas = document.querySelector('[data-region="viewport"] canvas') as HTMLCanvasElement
      const held = new Map<number, number>()
      for (const s of steps) {
        const button = s.kind === 'mouse' ? (s.button ?? 0) : 0
        const buttons = s.kind === 'mouse' ? 1 << (button === 2 ? 1 : button === 1 ? 2 : 0) : 1
        const down = s.phase === 'down'
        const up = s.phase === 'up' || s.phase === 'cancel'
        if (down) held.set(s.id, buttons)
        const pressed = down || (s.phase === 'move' && held.has(s.id))
        canvas.dispatchEvent(
          new PointerEvent(s.phase === 'move' ? 'pointermove' : `pointer${s.phase}`, {
            bubbles: true,
            cancelable: true,
            composed: true,
            pointerId: s.id,
            pointerType: s.kind,
            isPrimary: true,
            clientX: left + s.x,
            clientY: top + s.y,
            button: s.phase === 'move' ? -1 : button,
            buttons: pressed ? (held.get(s.id) ?? 0) : 0,
            pressure: s.pressure ?? (pressed ? 0.5 : 0),
          }),
        )
        if (up) held.delete(s.id)
        await new Promise((r) => setTimeout(r, 0))
      }
    },
    { steps, left: box.x, top: box.y },
  )
}

const lerp = (a: Point, b: Point, t: number): Point => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t })

/** down at `from`, `moves` moves along the line to `to`, up at `to`. */
export function dragSteps(kind: PointerKind, id: number, from: Point, to: Point, opts: { moves?: number; button?: number } = {}): PointerStep[] {
  const moves = opts.moves ?? 8
  const button = opts.button
  const steps: PointerStep[] = [{ phase: 'down', kind, id, ...from, button }]
  for (let i = 1; i <= moves; i++) steps.push({ phase: 'move', kind, id, ...lerp(from, to, i / moves), button })
  steps.push({ phase: 'up', kind, id, ...to, button })
  return steps
}

/** One finger drags: orbit. */
export const fingerDrag = (page: Page, from: Point, to: Point, opts: { id?: number; moves?: number } = {}) =>
  sendPointer(page, dragSteps('touch', opts.id ?? 1, from, to, opts))

/** A short touch: a tap. */
export const fingerTap = (page: Page, at: Point, id = 1) =>
  sendPointer(page, [
    { phase: 'down', kind: 'touch', id, ...at },
    { phase: 'up', kind: 'touch', id, ...at },
  ])

/** Two fingers move together: `a0`/`b0` are the start points, `a1`/`b1` the end points. Pinch, pan and twist are all this. */
export function twoFingerSteps(a0: Point, b0: Point, a1: Point, b1: Point, moves = 8): PointerStep[] {
  const steps: PointerStep[] = [
    { phase: 'down', kind: 'touch', id: 1, ...a0 },
    { phase: 'down', kind: 'touch', id: 2, ...b0 },
  ]
  for (let i = 1; i <= moves; i++) {
    const t = i / moves
    steps.push({ phase: 'move', kind: 'touch', id: 1, ...lerp(a0, a1, t) })
    steps.push({ phase: 'move', kind: 'touch', id: 2, ...lerp(b0, b1, t) })
  }
  steps.push({ phase: 'up', kind: 'touch', id: 1, ...a1 }, { phase: 'up', kind: 'touch', id: 2, ...b1 })
  return steps
}
export const twoFingers = (page: Page, a0: Point, b0: Point, a1: Point, b1: Point, moves?: number) =>
  sendPointer(page, twoFingerSteps(a0, b0, a1, b1, moves))

/** The pen draws or aims: it never navigates. `pressure` rides on every event. */
export const penStroke = (page: Page, from: Point, to: Point, opts: { id?: number; moves?: number; pressure?: number } = {}) =>
  sendPointer(
    page,
    dragSteps('pen', opts.id ?? 10, from, to, opts).map((s) => ({ ...s, pressure: s.phase === 'up' ? 0 : (opts.pressure ?? 0.5) })),
  )

/** Mouse drag with a button: 2 (right) orbits, 1 (middle) pans. */
export const mouseDrag = (page: Page, button: 0 | 1 | 2, from: Point, to: Point, opts: { id?: number; moves?: number } = {}) =>
  sendPointer(page, dragSteps('mouse', opts.id ?? 20, from, to, { ...opts, button }))

/** Wheel at a point in the canvas: a real wheel event from the mouse, so it goes the way a user's does. */
export async function mouseWheel(page: Page, at: Point, deltaY: number): Promise<void> {
  const box = await viewCanvas(page).boundingBox()
  if (!box) throw new Error('the view canvas has no box')
  await page.mouse.move(box.x + at.x, box.y + at.y)
  await page.mouse.wheel(0, deltaY)
}

/** The middle of the canvas, for sequences that do not care where they start. */
export async function canvasCentre(page: Page): Promise<Point> {
  const box = await viewCanvas(page).boundingBox()
  if (!box) throw new Error('the view canvas has no box')
  return { x: box.width / 2, y: box.height / 2 }
}
