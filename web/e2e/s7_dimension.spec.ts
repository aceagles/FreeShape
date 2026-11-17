import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { fingerTap, sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { openSketchFeature, readSketch, screenOf, settledSketch } from './support/sketch.ts'

// Slice S7, dimensions with the pad (card I7.11) against `freeshape serve`: the bracket's Slot sketch (free) takes new
// dimensions, its Bracket profile (fully defined) refuses one and has labels to change. Taps are finger taps on the canvas;
// numbers go in on the pad. Nothing is committed: every test ends with the sketch cancelled or the Document closed.

test.afterEach(async ({ page }) => closeAll(page.request))

const labels = (page: Page) => page.locator('.sketch-layer g[data-kind="dimension"] text').evaluateAll((els) => els.map((e) => e.textContent ?? ''))
const dimensionTool = (page: Page) => page.locator('[data-strip="sketch"] [data-tool="dimension"]')
const mid = (a: Point, b: Point): Point => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 })
/** A spot on a line a quarter of the way along it: the middle is where its constraint glyph sits. */
async function onLine(page: Page, from: string, to: string): Promise<Point> {
  const a = await screenOf(page, 'point', from)
  const b = await screenOf(page, 'point', to)
  return { x: a.x + (b.x - a.x) / 4, y: a.y + (b.y - a.y) / 4 }
}

async function doubleTap(page: Page, at: Point): Promise<void> {
  await sendPointer(page, [
    { phase: 'down', kind: 'touch', id: 1, ...at },
    { phase: 'up', kind: 'touch', id: 1, ...at },
    { phase: 'down', kind: 'touch', id: 1, ...at },
    { phase: 'up', kind: 'touch', id: 1, ...at },
  ])
}

test('Dimension on a line: a tap on it, a tap for the label, 20 on the pad and Enter adds a length of 20 mm', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, /Slot(?! cut)/)
  expect(await labels(page)).toEqual([])
  await dimensionTool(page).click()
  await expect(dimensionTool(page)).toHaveAttribute('data-active', '')
  await expect(page.getByTestId('dimension-hint')).toContainText('Pick a line')
  await settledSketch(page)
  const a = await onLine(page, 'p1', 'p2')
  await fingerTap(page, a)
  await expect(page.getByTestId('selection-count')).toHaveText('1 item')
  await expect(page.getByTestId('dimension-hint')).toContainText('Tap where the dimension goes')
  await fingerTap(page, { x: a.x, y: a.y + 70 })
  // the pad opens in the dialog area with the measured length, selected
  await expect(page.getByTestId('pad')).toBeVisible()
  await expect(page.getByRole('dialog', { name: 'Dimension' })).toBeVisible()
  await expect(page.getByTestId('pad-field')).toHaveText('16 mm')
  await page.getByTestId('pad').getByRole('button', { name: '2', exact: true }).click()
  await page.getByTestId('pad').getByRole('button', { name: '0', exact: true }).click()
  await page.getByTestId('pad').getByRole('button', { name: 'Enter', exact: true }).click()
  await expect(page.getByTestId('pad')).toHaveCount(0)
  await expect.poll(() => labels(page)).toEqual(['20 mm'])
  await expect(page.getByTestId('selection-count')).toHaveText('Nothing selected')
  // the slot's line is now 20 mm long on screen: the tool is ready for the next dimension
  await expect(page.getByTestId('dimension-hint')).toContainText('Pick a line')
  await page.getByTestId('sketch-cancel').click()
})

test('two points with the label above them give a horizontal distance, typed on the keyboard', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, /Slot(?! cut)/)
  await dimensionTool(page).click()
  await settledSketch(page)
  const p1 = await screenOf(page, 'point', 'p1')
  const p3 = await screenOf(page, 'point', 'p3')
  await fingerTap(page, p1)
  await fingerTap(page, p3)
  await expect(page.getByTestId('selection-count')).toHaveText('2 points')
  // above the top line, between the points' u: the label sits beside their box, so the distance is horizontal
  const top = Math.min(p1.y, p3.y)
  await fingerTap(page, { x: mid(p1, p3).x, y: top - 90 })
  await expect(page.getByTestId('pad-field')).toHaveText('16 mm')
  await expect(page.locator('.pad-field-label')).toHaveText('Horizontal distance')
  await page.keyboard.type('18')
  await page.keyboard.press('Enter')
  await expect.poll(() => labels(page)).toEqual(['18 mm'])
  await page.getByTestId('sketch-cancel').click()
})

test('a double tap on a label changes its value; 0 is refused in the words of the server and the sketch stays as it was', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await expect.poll(async () => (await labels(page)).length).toBe(4)
  const before = await labels(page)
  expect(before.length).toBe(4)
  // a double tap on a label opens the pad on its value, in any tool
  await settledSketch(page)
  const at = await screenOf(page, 'dimension', 'k19')
  await doubleTap(page, at)
  await expect(page.getByTestId('pad-field')).toHaveText('60 mm')
  // 0 is refused: the words are shown, the pad stays and the sketch is untouched
  await page.keyboard.type('0')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('dimension-refusal')).toBeVisible()
  await expect(page.getByTestId('pad')).toBeVisible()
  console.log(`refusal: ${await page.getByTestId('dimension-refusal').innerText()}`)
  expect(await labels(page)).toEqual(before)
  // another try: 2*25 as typed text is evaluated by the server
  await page.keyboard.press('Backspace')
  await page.keyboard.type('2*25')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('pad')).toHaveCount(0)
  await expect.poll(async () => (await labels(page)).includes('50 mm'), { message: JSON.stringify(await labels(page)) }).toBe(true)
  await expect.poll(async () => (await readSketch(page)).stats.labels).toBe(4)
  await page.getByTestId('sketch-cancel').click()
})

test('a new dimension that the others already fix is refused in words and changes nothing', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await expect.poll(async () => (await labels(page)).length).toBeGreaterThan(0)
  const before = await labels(page)
  await dimensionTool(page).click()
  await settledSketch(page)
  const l = await onLine(page, 'origin', 'p2')
  await fingerTap(page, l)
  await fingerTap(page, { x: l.x, y: l.y + 70 })
  await expect(page.getByTestId('pad-field')).toHaveText('60 mm')
  await page.keyboard.type('55')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('dimension-refusal')).toBeVisible()
  expect(await labels(page)).toEqual(before)
  await page.getByTestId('pad').getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(page.getByTestId('pad')).toHaveCount(0)
  expect(await labels(page)).toEqual(before)
  await page.getByTestId('sketch-cancel').click()
})
