import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { screenFor } from './support/draw.ts'
import { desktopMode } from './support/mode.ts'
import { sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { openNewSketch, settledSketch } from './support/sketch.ts'
import { viewCanvas } from './support/viewport.ts'

// Slice S9, card F22: in Desktop mode the Dimension tool takes a press on an item, a drag and a release at the label's place, as
// well as the click on the item and the click at the place; the dimension follows the mouse once an item is picked.
test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })
test.afterEach(async ({ page }) => closeAll(page.request))

const MOUSE = 20
const at = (p: Point) => ({ kind: 'mouse' as const, id: MOUSE, ...p })
const click = (page: Page, p: Point) => sendPointer(page, [{ phase: 'move', ...at(p) }, { phase: 'down', ...at(p), button: 0 }, { phase: 'up', ...at(p), button: 0 }])
const labels = (page: Page) => page.locator('.sketch-layer g[data-kind="dimension"]:not([data-preview]) text').evaluateAll((els) => els.map((e) => e.textContent ?? ''))
const preview = (page: Page) => page.locator('.sketch-layer g[data-preview="1"]')

async function rectangle(page: Page) {
  await openApp(page)
  await openNewSketch(page, 'Top')
  await page.keyboard.press('g')
  await click(page, await screenFor(page, -30, -10))
  await click(page, await screenFor(page, 20, 15))
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(4)
  await page.keyboard.press('d')
  await expect(page.locator('[data-strip="sketch"]').getByRole('button', { name: 'Dimension', exact: true })).toHaveAttribute('aria-pressed', 'true')
}

/** The side lengths of the sketch: the distances between its points that are not the diagonals. */
async function gaps(page: Page): Promise<number[]> {
  const dots = await page.locator('.sketch-layer circle[data-kind="point"]').evaluateAll((els) => els.map((e) => ({ x: e.getBoundingClientRect().x + e.getBoundingClientRect().width / 2, y: e.getBoundingClientRect().y + e.getBoundingClientRect().height / 2 })))
  const box = (await viewCanvas(page).boundingBox())!
  const uvs = await Promise.all(dots.map((d) => page.evaluate(([x, y]) => window.__freeshapeViewport?.sketch?.uvAt(x!, y!) ?? null, [d.x - box.x, d.y - box.y])))
  return uvs.flatMap((a, i) => uvs.slice(i + 1).map((b) => (a && b ? Math.hypot(a[0] - b[0], a[1] - b[1]) : 0)))
}

test('Dimension in Desktop mode: press on a side, drag away, release; 30 and Enter make the side 30 mm', async ({ page }) => {
  await rectangle(page)
  const side = await screenFor(page, -17.5, -10)
  const away = await screenFor(page, -17.5, -25)
  const part = await screenFor(page, -17.5, -18)
  // the press picks the side; the drag shows the dimension where the pointer is
  await sendPointer(page, [{ phase: 'move', ...at(side) }, { phase: 'down', ...at(side), button: 0 }, { phase: 'move', ...at(part) }, { phase: 'move', ...at(away) }])
  await expect(page.getByTestId('selection-count')).toHaveText('1 item')
  await expect(preview(page)).toHaveCount(1)
  await expect(preview(page).locator('text')).toHaveText('50 mm')
  await expect(page.locator('[data-testid="dimension-inline"]')).toHaveCount(0)
  // the release places it and opens the value box, with no second click
  await sendPointer(page, [{ phase: 'up', ...at(away), button: 0 }])
  const box = page.locator('[data-testid="dimension-inline"] input')
  await expect(box).toBeFocused()
  await expect(preview(page)).toHaveCount(0)
  await page.keyboard.type('30')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('dimension-inline')).toHaveCount(0)
  await expect.poll(() => labels(page)).toEqual(['30 mm'])
  const g = await gaps(page)
  expect(g.some((v) => Math.abs(v - 30) < 0.05)).toBe(true)
  expect(g.some((v) => Math.abs(v - 50) < 0.05)).toBe(false)
})

test('Dimension in Desktop mode: a click on a side picks it, the dimension follows the mouse, a second click places it', async ({ page }) => {
  await rectangle(page)
  await click(page, await screenFor(page, -17.5, -10))
  await expect(page.getByTestId('selection-count')).toHaveText('1 item')
  await expect(page.locator('[data-testid="dimension-inline"]')).toHaveCount(0)
  // no button down: the preview still follows the pointer
  await sendPointer(page, [{ phase: 'move', ...at(await screenFor(page, -17.5, -24)) }])
  await expect(preview(page)).toHaveCount(1)
  await click(page, await screenFor(page, -17.5, -24))
  await expect(page.locator('[data-testid="dimension-inline"] input')).toBeFocused()
  await page.keyboard.type('30')
  await page.keyboard.press('Enter')
  await expect.poll(() => labels(page)).toEqual(['30 mm'])
})

test('Dimension in Desktop mode: Esc during the press drops the picks and the release places nothing', async ({ page }) => {
  await rectangle(page)
  const side = await screenFor(page, -17.5, -10)
  const away = await screenFor(page, -17.5, -25)
  await sendPointer(page, [{ phase: 'move', ...at(side) }, { phase: 'down', ...at(side), button: 0 }, { phase: 'move', ...at(away) }])
  await expect(preview(page)).toHaveCount(1)
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('selection-count')).toHaveText('Nothing selected')
  await expect(preview(page)).toHaveCount(0)
  await sendPointer(page, [{ phase: 'up', ...at(away), button: 0 }])
  await expect(page.locator('[data-testid="dimension-inline"]')).toHaveCount(0)
  await expect(page.getByTestId('selection-count')).toHaveText('Nothing selected')
  expect((await settledSketch(page)).stats.labels).toBe(0)
})
