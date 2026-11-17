import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { canvasCentre, fingerDrag, fingerTap, mouseDrag, penStroke, sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { handleOf, openNewSketch, pickSketchAt, openSketchFeature, readSketch, screenOf, settledSketch, uvAt } from './support/sketch.ts'

// Slice S7, selecting, moving, deleting and construction in the sketch (card I7.9) against `freeshape serve`: the Bracket
// profile sketch, opened through the test hook. The profile is fully defined, so each test first deletes the 60 mm
// dimension (k19), which frees the end of the bottom edge (p2, with p3 above it) to move along x. Nothing is committed.

test.afterEach(async ({ page }) => closeAll(page.request))

/** Open the sketch and delete the 60 mm dimension by tapping its label and pressing Delete. */
async function openFreed(page: Page): Promise<void> {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await settledSketch(page)
  const before = (await readSketch(page)).stats.labels
  await fingerTap(page, await screenOf(page, 'dimension', 'k19'))
  await expect.poll(async () => (await readSketch(page)).selected).toEqual([{ kind: 'dimension', id: 'k19' }])
  await page.locator('[data-tool="delete"]').click()
  await expect.poll(async () => (await readSketch(page)).stats.labels).toBe(before - 1)
  await expect.poll(async () => (await readSketch(page)).selected).toEqual([])
  await settledSketch(page)
}

test('Delete removes the selected dimension and clears the selection; Construct turns a line to construction and back', async ({ page }) => {
  await openFreed(page)
  await expect(page.getByTestId('selection-count')).toHaveText('Nothing selected')
  const before = (await readSketch(page)).stats.dashed
  // a quarter of the way along the edge: its middle holds the constraint's glyph
  const a = await screenOf(page, 'point', 'origin')
  const b = await screenOf(page, 'point', 'p2')
  await fingerTap(page, { x: a.x + (b.x - a.x) / 4, y: a.y + (b.y - a.y) / 4 })
  await expect.poll(async () => (await readSketch(page)).selected).toEqual([{ kind: 'entity', id: 'l7' }])
  await page.locator('[data-tool="construct"]').click()
  await expect.poll(async () => (await readSketch(page)).stats.dashed).toBe(before + 1)
  // still selected: the same button turns it back
  await page.locator('[data-tool="construct"]').click()
  await expect.poll(async () => (await readSketch(page)).stats.dashed).toBe(before)
})

test('the pen drags a point directly: it follows the pointer, the solve decides, and the debug panel records the round trips', async ({ page }) => {
  await openFreed(page)
  const p2 = await screenOf(page, 'point', 'p2')
  const origin = await screenOf(page, 'point', 'origin')
  await penStroke(page, p2, { x: p2.x + 60, y: p2.y + 40 }, { moves: 10 })
  // p2 keeps its y (the bottom edge is horizontal) and ends up farther right. While the pen is down the point is drawn
  // where the pointer is (60 px right, 40 px down), and only the server's answer puts it where the solve decides, so the
  // test waits for the settled place: farther right AND back on its y. Reading y as soon as x had moved raced that answer.
  await expect
    .poll(async () => {
      const at = await screenOf(page, 'point', 'p2')
      return at.x > p2.x + 30 && Math.abs(at.y - p2.y) < 1
    })
    .toBe(true)
  expect(await screenOf(page, 'point', 'origin')).toEqual(origin)
  expect((await readSketch(page)).selected).toEqual([])
  await page.getByTestId('link-status').click()
  await expect(page.getByTestId('drag-median')).toBeVisible()
})

test('a pen tap on a point selects it and moves nothing', async ({ page }) => {
  await openFreed(page)
  const p2 = await screenOf(page, 'point', 'p2')
  await penStroke(page, p2, { x: p2.x + 3, y: p2.y + 2 }, { moves: 2 })
  await expect.poll(async () => (await readSketch(page)).selected).toEqual([{ kind: 'point', id: 'p2' }])
  const after = await screenOf(page, 'point', 'p2')
  expect(after.x).toBeCloseTo(p2.x, 3)
  await page.getByTestId('link-status').click()
  await expect(page.getByTestId('drag-median')).toHaveCount(0)
})

test('a finger drag that starts on a point pans the view; the point moves only by its Drag handle', async ({ page }) => {
  await openFreed(page)
  const p2 = await screenOf(page, 'point', 'p2')
  const origin = await screenOf(page, 'point', 'origin')
  await fingerDrag(page, p2, { x: p2.x - 40, y: p2.y + 20 })
  await expect.poll(async () => (await screenOf(page, 'point', 'origin')).x - origin.x).toBeCloseTo(-40, 3)
  const panned = await screenOf(page, 'point', 'origin')
  // select it with a tap, then take its handle
  const p2b = await screenOf(page, 'point', 'p2')
  await fingerTap(page, p2b)
  await expect.poll(async () => (await readSketch(page)).selected).toEqual([{ kind: 'point', id: 'p2' }])
  await settledSketch(page)
  await expect.poll(() => handleOf(page, 'point', 'p2')).not.toBeNull()
  const h = await handleOf(page, 'point', 'p2')
  await fingerDrag(page, h!, { x: h!.x + 50, y: h!.y }, { moves: 10 })
  await expect.poll(async () => (await screenOf(page, 'point', 'p2')).x).toBeGreaterThan(p2b.x + 25)
  // the view did not pan under it
  expect(await screenOf(page, 'point', 'origin')).toEqual(panned)
  await page.getByTestId('link-status').click()
  await expect(page.getByTestId('drag-median')).toBeVisible()
})

test('a dimension label moves by a pen drag and stays where it was dropped', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await settledSketch(page)
  const l = await screenOf(page, 'dimension', 'k19')
  await penStroke(page, l, { x: l.x + 50, y: l.y + 40 }, { moves: 8 })
  await expect.poll(async () => (await screenOf(page, 'dimension', 'k19')).y).toBeGreaterThan(l.y + 30)
  const after = await screenOf(page, 'dimension', 'k19')
  expect(after.x).toBeCloseTo(l.x + 50, 0)
  // the server's answer redraws the sketch with the label where it was dropped
  await settledSketch(page)
  expect((await screenOf(page, 'dimension', 'k19')).y).toBeCloseTo(after.y, 0)
  expect((await readSketch(page)).selected).toEqual([])
})

// F14: a drag on a circle's rim. With the radius held by a diameter dimension the whole circle moves by the drag offset;
// with it free the rim resizes the circle, as the desktop's Sketcher.mouse_drag does.
async function drawCircle(page: Page): Promise<{ centre: string; circle: string }> {
  await openApp(page)
  await openNewSketch(page, 'Top')
  const c = await canvasCentre(page)
  const pen = (phase: 'down' | 'move' | 'up', p: Point) => ({ phase, kind: 'pen' as const, id: 10, ...p, pressure: phase === 'up' ? 0 : 0.5 })
  await sendPointer(page, [pen('down', c)])
  await expect(page.getByTestId('quick-circle')).toBeVisible()
  const b = (await page.getByTestId('quick-circle').boundingBox())!
  const box = (await page.getByTestId('viewport').boundingBox())!
  const to = { x: b.x + b.width / 2 - box.x, y: b.y + b.height / 2 - box.y }
  await sendPointer(page, [pen('move', { x: (c.x + to.x) / 2, y: (c.y + to.y) / 2 }), pen('move', to), pen('up', to)])
  const place = (p: Point) => sendPointer(page, [pen('down', { x: p.x - 3, y: p.y }), pen('move', p), pen('up', p)])
  await place({ x: c.x + 150, y: c.y + 100 })
  await place({ x: c.x + 230, y: c.y + 100 })
  await expect.poll(async () => (await settledSketch(page)).stats.circles).toBe(1)
  const circle = (await page.locator('.sketch-layer [data-kind="circle"]').first().getAttribute('data-id'))!
  const centre = (await page.locator('.sketch-layer circle[data-kind="point"]').evaluateAll((els) => els.map((e) => e.getAttribute('data-id')!))).find((i) => i !== 'origin')!
  return { centre, circle }
}

/** The circle's radius in mm: the distance in the sketch between its centre and a point of its rim. */
async function radiusOf(page: Page, centre: string, circle: string): Promise<number> {
  const a = (await uvAt(page, ...Object.values(await screenOf(page, 'point', centre)) as [number, number]))!
  const b = (await uvAt(page, ...Object.values(await screenOf(page, 'entity', circle)) as [number, number]))!
  return Math.hypot(a[0] - b[0], a[1] - b[1])
}

test('a mouse drag on the rim of a circle with a diameter dimension moves the whole circle and keeps the diameter', async ({ page }) => {
  const { centre, circle } = await drawCircle(page)
  const rim0 = await screenOf(page, 'entity', circle)
  await page.locator('[data-strip="sketch"] [data-tool="dimension"]').click()
  await fingerTap(page, rim0)
  await fingerTap(page, { x: rim0.x + 90, y: rim0.y - 70 })
  await expect(page.getByTestId('pad')).toBeVisible()
  for (const k of ['1', '5']) await page.getByTestId('pad').getByRole('button', { name: k, exact: true }).click()
  await page.getByTestId('pad').getByRole('button', { name: 'Enter', exact: true }).click()
  await expect(page.getByTestId('pad')).toHaveCount(0)
  await page.locator('[data-strip="sketch"] [data-tool="select"]').click()
  await settledSketch(page)
  await expect.poll(() => radiusOf(page, centre, circle)).toBeCloseTo(7.5, 2)
  const c0 = (await uvAt(page, ...Object.values(await screenOf(page, 'point', centre)) as [number, number]))!
  const r0 = await radiusOf(page, centre, circle)
  expect(r0).toBeCloseTo(7.5, 2)
  const from = await screenOf(page, 'entity', circle)
  await expect.poll(() => pickSketchAt(page, from.x, from.y, 'mouse')).toEqual({ kind: 'entity', id: circle })
  const to = { x: from.x + 50, y: from.y + 30 }
  const u0 = (await uvAt(page, from.x, from.y))!
  const u1 = (await uvAt(page, to.x, to.y))!
  await mouseDrag(page, 0, from, to, { moves: 10 })
  await expect
    .poll(async () => {
      const c = (await uvAt(page, ...Object.values(await screenOf(page, 'point', centre)) as [number, number]))!
      return Math.hypot(c[0] - c0[0] - (u1[0] - u0[0]), c[1] - c0[1] - (u1[1] - u0[1]))
    })
    .toBeLessThan(0.05)
  expect(await radiusOf(page, centre, circle)).toBeCloseTo(7.5, 2)
})

test('a mouse drag on the rim of a circle with a free radius resizes it, as on the desktop', async ({ page }) => {
  const { centre, circle } = await drawCircle(page)
  await page.locator('[data-strip="sketch"] [data-tool="select"]').click()
  await settledSketch(page)
  const c0 = await screenOf(page, 'point', centre)
  const r0 = await radiusOf(page, centre, circle)
  const from = await screenOf(page, 'entity', circle)
  await expect.poll(() => pickSketchAt(page, from.x, from.y, 'mouse')).toEqual({ kind: 'entity', id: circle })
  // the middle of the stroke is the rim point farthest left: dragging left grows the circle
  await mouseDrag(page, 0, from, { x: from.x - 40, y: from.y }, { moves: 10 })
  await expect.poll(async () => (await radiusOf(page, centre, circle)) - r0).toBeGreaterThan(1)
  const c1 = await screenOf(page, 'point', centre)
  expect(c1.x).toBeCloseTo(c0.x, 1)
  expect(c1.y).toBeCloseTo(c0.y, 1)
})
