import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { screenFor } from './support/draw.ts'
import { desktopMode } from './support/mode.ts'
import { sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { openNewSketch, settledSketch } from './support/sketch.ts'

// Slice S9, card I9.4: the Desktop sketch toolbar. Desktop mode, 1440 x 900, mouse.
test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })
test.afterEach(async ({ page }) => closeAll(page.request))

const bar = (page: Page) => page.locator('[data-strip="sketch"]')
const MOUSE = 20

/** A mouse click on the view at a point: move there, press, release. */
const click = (page: Page, p: Point) =>
  sendPointer(page, [
    { phase: 'move', kind: 'mouse', id: MOUSE, ...p },
    { phase: 'down', kind: 'mouse', id: MOUSE, ...p, button: 0 },
    { phase: 'up', kind: 'mouse', id: MOUSE, ...p, button: 0 },
  ])

test('a sketch on Top: the toolbar has the desktop row, Line draws with mouse clicks, Finish sketch puts the sketch in the feature list', async ({ page }) => {
  await openApp(page)
  const features = page.locator('.panel-row[data-feature]')
  await expect.poll(() => features.count()).toBeGreaterThan(0)
  const before = await features.count()
  await openNewSketch(page, 'Top')

  // the sketch row replaces the feature row, in one 44 px line; the tablet strip is not drawn
  await expect(page.locator('[data-strip="feature"]')).toHaveCount(0)
  await expect(page.locator('.tool-strip')).toHaveCount(0)
  expect(Math.round((await bar(page).boundingBox())!.height)).toBe(44)
  const line = bar(page).getByRole('button', { name: 'Line', exact: true })
  await expect(line).toHaveAttribute('title', 'Line (L)')
  await expect(bar(page).getByRole('button', { name: 'Select', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(bar(page).getByRole('button', { name: 'Polygon' })).toBeDisabled()
  await expect(bar(page).getByRole('button', { name: 'Polygon' })).toHaveAttribute('title', 'Desktop only for now')

  // the pinned right end is whole: Normal to, Fit and Finish sketch sit inside the window
  for (const name of ['Normal to', 'Zoom to fit', 'Finish sketch']) {
    const box = (await bar(page).getByRole('button', { name }).boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(1440)
  }

  // Line on, two clicks, a third click on the last point ends the chain
  await line.click()
  await expect(line).toHaveAttribute('aria-pressed', 'true')
  const a = await screenFor(page, -30, -10)
  const b = await screenFor(page, 20, 15)
  await click(page, a)
  await click(page, b)
  await click(page, b)
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBeGreaterThanOrEqual(1)

  await bar(page).getByRole('button', { name: 'Finish sketch' }).click()
  await page.locator('[data-strip="feature"]').waitFor()
  await expect(features).toHaveCount(before + 1)
  await expect(features.filter({ hasText: /^\s*Sketch/ }).last()).toBeVisible()
})

test('Cancel sketch in the Document menu asks first when the sketch has changes', async ({ page }) => {
  await openApp(page)
  await openNewSketch(page, 'Top')
  const menu = async () => page.locator('[data-region="top-bar"]').getByRole('button', { name: 'Document', exact: true }).click()
  // nothing drawn: it closes the sketch at once
  await menu()
  await page.getByRole('menuitem', { name: 'Cancel sketch' }).click()
  await page.locator('[data-strip="feature"]').waitFor()
  // after a drawn line it asks, and Keep sketching leaves the sketch open
  await openNewSketch(page, 'Top')
  await bar(page).getByRole('button', { name: 'Line', exact: true }).click()
  await click(page, await screenFor(page, -30, -10))
  await click(page, await screenFor(page, 20, 15))
  await click(page, await screenFor(page, 20, 15))
  await expect(bar(page).getByRole('button', { name: 'Undo', exact: true })).toBeEnabled()
  await menu()
  await page.getByRole('menuitem', { name: 'Cancel sketch' }).click()
  await expect(page.getByText('Discard this sketch?')).toBeVisible()
  await page.getByRole('button', { name: 'Keep sketching' }).click()
  await expect(bar(page)).toBeVisible()
  await menu()
  await page.getByRole('menuitem', { name: 'Cancel sketch' }).click()
  await page.getByRole('button', { name: 'Discard', exact: true }).click()
  await page.locator('[data-strip="feature"]').waitFor()
})

test('Construct with nothing selected makes the next line construction, and the tool pressed again goes back to Select (F20)', async ({ page }) => {
  await openApp(page)
  await openNewSketch(page, 'Top')
  const button = (name: string) => bar(page).getByRole('button', { name, exact: true })
  await expect(button('Construction')).toHaveAttribute('aria-pressed', 'false')
  await button('Construction').click()
  await expect(button('Construction')).toHaveAttribute('aria-pressed', 'true')
  await button('Line').click()
  const a = await screenFor(page, -30, -10)
  const b = await screenFor(page, 20, 15)
  await click(page, a)
  await click(page, b)
  await click(page, b)
  await expect.poll(async () => (await settledSketch(page)).stats.dashed).toBe(1)
  // a chain half drawn, then Line pressed again: back to Select, nothing pending
  await click(page, a)
  await expect.poll(async () => (await settledSketch(page)).draw.picks.length).toBe(1)
  await button('Line').click()
  await expect(button('Select')).toHaveAttribute('aria-pressed', 'true')
  await expect(button('Line')).toHaveAttribute('aria-pressed', 'false')
  expect((await settledSketch(page)).draw.picks).toEqual([])
  // the mode outlives the tool, and the same button turns it off
  await expect(button('Construction')).toHaveAttribute('aria-pressed', 'true')
  await button('Construction').click()
  await expect(button('Construction')).toHaveAttribute('aria-pressed', 'false')
})
