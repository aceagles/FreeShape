import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { emptySpot, pickAll } from './support/pick.ts'
import type { Spot } from './support/pick.ts'
import { readViewport, settled, viewCanvas } from './support/viewport.ts'

// Slice S9, card I9.9: the mouse selects as on the desktop in Desktop mode: a box, a Shift-click that adds one more edge, the
// count beside the cursor, and a click on empty space that clears. Real mouse events under WebKit.
test.use(desktopMode)
test.afterEach(async ({ page }) => closeAll(page.request))

const key = (i: { kind: string; part: string | null; index: number }) => `${i.kind}:${i.part}:${i.index}`

/** The model's box on screen: where the mouse picks a face or an edge (planes left out), on a grid. */
async function modelBox(page: Page): Promise<{ x0: number; y0: number; x1: number; y1: number }> {
  const cam = (await settled(page)).camera
  const grid: Spot[] = []
  for (let y = 8; y < cam.height - 8; y += 10) for (let x = 8; x < cam.width - 8; x += 10) grid.push({ x, y })
  const hits = await pickAll(page, grid, 'mouse', ['face', 'edge'])
  const on = grid.filter((_, i) => hits[i] !== null)
  if (on.length === 0) throw new Error('the model is not in the view')
  return { x0: Math.min(...on.map((s) => s.x)), y0: Math.min(...on.map((s) => s.y)), x1: Math.max(...on.map((s) => s.x)), y1: Math.max(...on.map((s) => s.y)) }
}

test('a box selects the edges and faces of the left of the bracket, Shift-click adds one more edge, the count shows by the cursor, and a click on empty space clears', async ({ page }) => {
  await openApp(page)
  await expect.poll(() => page.getByTestId('shell').getAttribute('data-mode')).toBe('desktop')
  // Desktop mode has no Multi on the selection chip
  await expect(page.getByTestId('selection-multi')).toHaveCount(0)
  const canvas = (await viewCanvas(page).boundingBox())!
  const at = (s: Spot) => ({ x: canvas.x + s.x, y: canvas.y + s.y })
  const m = await modelBox(page)
  const mid = Math.round((m.x0 + m.x1) / 2)
  const from = { x: m.x0 - 12, y: m.y0 - 12 }
  const to = { x: mid, y: m.y1 + 12 }

  // the box is drawn while the left button is held: solid (not crossing) left to right
  await page.mouse.move(at(from).x, at(from).y)
  await page.mouse.down()
  await page.mouse.move(at(to).x, at(to).y, { steps: 8 })
  const box = page.getByTestId('select-box')
  await expect(box).toBeVisible()
  expect(await box.getAttribute('data-crossing')).toBeNull()
  await page.mouse.up()
  await expect(box).toBeHidden()

  // it took what lies wholly inside it: the same as the view's own reckoning for that box, edges among them
  const expected = await page.evaluate((b) => window.__freeshapeViewport!.boxHits(b.from.x, b.from.y, b.to.x, b.to.y), { from, to })
  expect(expected.some((i) => i.kind === 'edge')).toBe(true)
  await expect.poll(async () => (await readViewport(page)).selection.items.map(key).sort()).toEqual(expected.map(key).sort())
  const n = expected.length
  expect(n).toBeGreaterThan(1)

  // the count beside the cursor
  const badge = page.getByTestId('select-count-badge')
  await expect(badge).toBeVisible()
  await expect(badge).toHaveText(String(n))

  // an edge on the right of the bracket that the box did not take, Shift-clicked: one more
  const cam = (await settled(page)).camera
  const grid: Spot[] = []
  for (let y = 8; y < cam.height - 8; y += 6) for (let x = mid + 16; x < cam.width - 8; x += 6) grid.push({ x, y })
  const hits = await pickAll(page, grid, 'mouse')
  const taken = new Set(expected.map(key))
  const i = hits.findIndex((h) => h?.kind === 'edge' && !taken.has(key(h)))
  expect(i).toBeGreaterThanOrEqual(0)
  const edge = hits[i]!
  await page.mouse.move(at(grid[i]!).x, at(grid[i]!).y)
  await page.keyboard.down('Shift')
  await page.mouse.click(at(grid[i]!).x, at(grid[i]!).y)
  await page.keyboard.up('Shift')
  await expect.poll(async () => (await readViewport(page)).selection.count).toBe(n + 1)
  expect((await readViewport(page)).selection.items.map(key)).toContain(key(edge))
  await expect(badge).toHaveText(String(n + 1))

  // a click on empty space clears, and the badge goes
  const empty = await emptySpot(page, 'mouse')
  await page.mouse.click(at(empty).x, at(empty).y)
  await expect.poll(async () => (await readViewport(page)).selection.count).toBe(0)
  await expect(badge).toBeHidden()
})

test('a box dragged right to left is drawn dashed and also takes what it crosses', async ({ page }) => {
  await openApp(page)
  const canvas = (await viewCanvas(page).boundingBox())!
  const m = await modelBox(page)
  const mid = Math.round((m.x0 + m.x1) / 2)
  const from = { x: mid, y: m.y0 - 12 }
  const to = { x: m.x0 - 12, y: m.y1 + 12 }
  await page.mouse.move(canvas.x + from.x, canvas.y + from.y)
  await page.mouse.down()
  await page.mouse.move(canvas.x + to.x, canvas.y + to.y, { steps: 8 })
  await expect.poll(() => page.getByTestId('select-box').getAttribute('data-crossing')).toBe('')
  await page.mouse.up()
  const [crossing, window_] = await page.evaluate(
    (b) => [window.__freeshapeViewport!.boxHits(b.from.x, b.from.y, b.to.x, b.to.y), window.__freeshapeViewport!.boxHits(b.to.x, b.from.y, b.from.x, b.to.y)],
    { from, to },
  )
  expect(crossing.length).toBeGreaterThan(window_.length)
  await expect.poll(async () => (await readViewport(page)).selection.items.map(key).sort()).toEqual(crossing.map(key).sort())
})
