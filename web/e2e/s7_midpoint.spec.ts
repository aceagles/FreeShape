import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { chooseWithRing, PEN, place, screenFor } from './support/draw.ts'
import { mouseDrag, penStroke, sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { openNewSketch, screenOf, settledSketch, uvAt } from './support/sketch.ts'

// Slice S7, F24: a second line started on the middle of the first is tied to it with a midpoint constraint, so the start stays in
// the middle when an end of the first line is dragged. Pen events stand in for the Pencil, as in every browser spec.

test.afterEach(async ({ page }) => closeAll(page.request))

const stats = async (page: Page) => (await settledSketch(page)).stats

/** The id of the sketch point (not the origin) drawn nearest a screen position. */
async function pointNear(page: Page, at: Point): Promise<string> {
  const ids = await page.locator('.sketch-layer circle[data-kind="point"]').evaluateAll((els) => els.map((e) => e.getAttribute('data-id')!).filter((id) => id !== 'origin'))
  let best = ''
  let bestD = Infinity
  for (const id of ids) {
    const s = await screenOf(page, 'point', id)
    const d = Math.hypot(s.x - at.x, s.y - at.y)
    if (d < bestD) {
      bestD = d
      best = id
    }
  }
  expect(bestD, 'a point near the place').toBeLessThan(4)
  return best
}

const uvOfPoint = async (page: Page, id: string) => {
  const s = await screenOf(page, 'point', id)
  return (await uvAt(page, s.x, s.y))!
}

const MOUSE = 1
const mouseAt = (p: Point) => ({ phase: 'move' as const, kind: 'mouse' as const, id: MOUSE, ...p })
const click = (page: Page, p: Point) => sendPointer(page, [mouseAt(p), { phase: 'down', kind: 'mouse', id: MOUSE, button: 0, ...p }, { phase: 'up', kind: 'mouse', id: MOUSE, button: 0, ...p }])

for (const kind of ['pen', 'mouse'] as const) test(`a line started on the middle of another stays in its middle when the other is dragged (${kind})`, async ({ page }) => {
  const put = (p: Point) => (kind === 'pen' ? place(page, p) : click(page, p))
  const drag = (from: Point, to: Point) => (kind === 'pen' ? penStroke(page, from, to, { moves: 10 }) : mouseDrag(page, 0, from, to, { id: MOUSE, moves: 10 }))
  await openApp(page)
  await openNewSketch(page, 'Top')
  await chooseWithRing(page, 'line')
  const a = await screenFor(page, -30, 10)
  const b = await screenFor(page, 30, 30)
  await put(a)
  await put(b)
  await expect.poll(async () => (await stats(page)).lines).toBe(1)
  // the chain stops with a pen lift on its last point
  await put(b)
  await expect.poll(async () => (await settledSketch(page)).draw.picks).toHaveLength(0)

  // aim a little off the middle: the mark says Midpoint and sits on the middle
  const mid = await screenFor(page, 0, 20)
  const near = { x: mid.x + 4, y: mid.y - 3 }
  await sendPointer(page, kind === 'pen' ? [{ phase: 'down', kind: 'pen', id: PEN, x: near.x + 20, y: near.y }, { phase: 'move', kind: 'pen', id: PEN, ...near }] : [mouseAt(near)])
  const aim = (await settledSketch(page)).draw.aim!
  expect(aim.snap).toBe('mid')
  expect(Math.hypot(aim.at.x - mid.x, aim.at.y - mid.y)).toBeLessThan(1)
  await expect(page.locator('[data-kind="aim"][data-snap="mid"]')).toBeVisible()
  await expect(page.locator('[data-kind="aim"] text')).toHaveText('Midpoint')
  await (kind === 'pen' ? sendPointer(page, [{ phase: 'up', kind: 'pen', id: PEN, ...near, pressure: 0 }]) : click(page, near))
  await expect.poll(async () => (await settledSketch(page)).draw.picks).toHaveLength(1)
  const end = await screenFor(page, 0, 50)
  await put(end)
  await expect.poll(async () => (await stats(page)).lines).toBe(2)
  await expect(page.locator('.sketch-layer circle[data-kind="point"]')).toHaveCount(5) // the origin, two ends, the start on the middle and the far end

  const [pa, pb, pm] = [await pointNear(page, a), await pointNear(page, b), await pointNear(page, mid)]
  const expectMiddle = async () => {
    const [ua, ub, um] = [await uvOfPoint(page, pa), await uvOfPoint(page, pb), await uvOfPoint(page, pm)]
    expect(um[0]).toBeCloseTo((ua[0] + ub[0]) / 2, 3)
    expect(um[1]).toBeCloseTo((ua[1] + ub[1]) / 2, 3)
  }
  await expectMiddle()

  // leave the tool, then drag the far end of the first line: the second line's start follows the middle
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await expect.poll(async () => (await settledSketch(page)).draw.tool).toBe('select')
  const from = await screenOf(page, 'point', pb)
  await drag(from, { x: from.x + 30, y: from.y + 40 })
  await expect.poll(async () => (await screenOf(page, 'point', pb)).x).toBeGreaterThan(from.x + 15)
  await settledSketch(page)
  const moved = await screenOf(page, 'point', pm)
  expect(Math.hypot(moved.x - mid.x, moved.y - mid.y)).toBeGreaterThan(8)
  await expectMiddle()
})
