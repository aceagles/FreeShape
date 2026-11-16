import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { fingerDrag, fingerTap, penStroke } from './support/pointer.ts'
import { handleOf, openSketchFeature, readSketch, screenOf, settledSketch } from './support/sketch.ts'

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
  expect((await readSketch(page)).selected).toEqual([])
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
  const panned = await screenOf(page, 'point', 'origin')
  expect(panned.x - origin.x).toBeCloseTo(-40, 3)
  // select it with a tap, then take its handle
  const p2b = await screenOf(page, 'point', 'p2')
  await fingerTap(page, p2b)
  await expect.poll(async () => (await readSketch(page)).selected).toEqual([{ kind: 'point', id: 'p2' }])
  await settledSketch(page)
  const h = await handleOf(page, 'point', 'p2')
  expect(h).not.toBeNull()
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
