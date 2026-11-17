import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { screenFor } from './support/draw.ts'
import { desktopMode } from './support/mode.ts'
import { sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { openNewSketch, settledSketch } from './support/sketch.ts'

// Slice S9, card F18: the left column collapses while a sketch is open. Desktop mode, 1440 x 900, mouse.
test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })
test.afterEach(async ({ page }) => closeAll(page.request))

const bar = (page: Page) => page.locator('[data-strip="sketch"]')
const MOUSE = 20
const click = (page: Page, p: Point) =>
  sendPointer(page, [
    { phase: 'move', kind: 'mouse', id: MOUSE, ...p },
    { phase: 'down', kind: 'mouse', id: MOUSE, ...p, button: 0 },
    { phase: 'up', kind: 'mouse', id: MOUSE, ...p, button: 0 },
  ])
const viewportWidth = async (page: Page) => (await page.locator('[data-region="viewport"]').boundingBox())!.width

test('in a sketch the column collapses, the view widens, a line is drawn, the column expands, and the sketch finishes', async ({ page }) => {
  await openApp(page)
  const features = page.locator('.panel-row[data-feature]')
  await expect.poll(() => features.count()).toBeGreaterThan(0)
  const before = await features.count()
  await openNewSketch(page, 'Top')
  const wide = await viewportWidth(page)

  const collapse = page.getByRole('button', { name: 'Collapse feature panel' })
  await expect(collapse).toBeEnabled()
  await collapse.click()
  await expect(page.getByTestId('panel-rail')).toBeVisible()
  await expect.poll(() => viewportWidth(page)).toBeGreaterThan(wide)
  // the sketch toolbar is still there, and the canvas took the new width
  await expect(bar(page)).toBeVisible()
  const canvas = page.locator('[data-region="viewport"] canvas').first()
  await expect.poll(async () => Math.round((await canvas.boundingBox())!.width)).toBe(Math.round(await viewportWidth(page)))

  // Line on, two clicks, a third on the last point ends the chain
  await bar(page).getByRole('button', { name: 'Line', exact: true }).click()
  const a = await screenFor(page, -30, -10)
  const b = await screenFor(page, 20, 15)
  await click(page, a)
  await click(page, b)
  await click(page, b)
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBeGreaterThanOrEqual(1)

  await page.getByRole('button', { name: 'Expand feature panel' }).click()
  await expect(page.getByTestId('feature-panel')).toBeVisible()
  await expect.poll(() => viewportWidth(page)).toBe(wide)
  await expect(bar(page)).toBeVisible()

  await bar(page).getByRole('button', { name: 'Finish sketch' }).click()
  await page.locator('[data-strip="feature"]').waitFor()
  await expect(features).toHaveCount(before + 1)
})
