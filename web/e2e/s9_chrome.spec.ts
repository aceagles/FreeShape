import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { featureRow } from './support/features.ts'
import { desktopMode } from './support/mode.ts'

// Slice S9, card I9.2: the Desktop-mode chrome. Desktop mode, 1440 x 900, mouse.
test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })
test.afterEach(async ({ page }) => closeAll(page.request))

test('the bottom strip makes and switches tabs, the Document menu closes one, and a feature dialog floats over the viewport', async ({ page }) => {
  await openApp(page)
  const strip = page.locator('[data-region="tab-strip"]')
  const tabs = strip.locator('.tab-button')
  const before = await tabs.count()
  // the top bar has the menus and no tabs
  await expect(page.locator('[data-region="top-bar"] .tab-button')).toHaveCount(0)
  await expect(page.locator('[data-region="top-bar"]').getByRole('button', { name: 'View', exact: true })).toBeVisible()

  // + makes a Part Studio, which opens
  await strip.getByRole('button', { name: 'Add tab' }).click()
  await expect(page.getByRole('menuitem', { name: 'Create Drawing…' })).toBeDisabled()
  await page.getByRole('menuitem', { name: 'Create Part Studio' }).click()
  await expect(tabs).toHaveCount(before + 1)
  await expect(tabs.last()).toHaveAttribute('aria-current', 'page')

  // switch tabs from the strip
  await tabs.first().click()
  await expect(tabs.first()).toHaveAttribute('aria-current', 'page')
  await tabs.last().click()
  await expect(tabs.last()).toHaveAttribute('aria-current', 'page')

  // Close tab from the Document menu takes the tab on screen out of the Document
  await page.locator('[data-region="top-bar"]').getByRole('button', { name: 'Document', exact: true }).click()
  await page.getByRole('menuitem', { name: /^Close tab/ }).click()
  await page.getByRole('button', { name: 'Delete', exact: true }).click()
  await expect(tabs).toHaveCount(before)

  // edit a feature: its dialog floats over the viewport at (12, 12) and the feature list stays
  await tabs.first().click()
  await featureRow(page, 'Extrude 1').dblclick()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  const view = (await page.getByTestId('shell-viewport').boundingBox())!
  const box = (await dialog.boundingBox())!
  expect(Math.round(box.x - view.x)).toBe(12)
  expect(Math.round(box.y - view.y)).toBe(12)
  expect(Math.round(box.width)).toBe(300)
  await expect(featureRow(page, 'Fillet 1')).toBeVisible()
  const list = (await page.getByTestId('left-column').boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(list.x + list.width)
})

test('the splitter resizes the left column and keeps the width on the device', async ({ page }) => {
  await openApp(page)
  const column = page.getByTestId('left-column')
  expect(Math.round((await column.boundingBox())!.width)).toBe(270)
  const bar = (await page.getByTestId('splitter').boundingBox())!
  await page.mouse.move(bar.x, 300)
  await page.mouse.down()
  await page.mouse.move(bar.x + 130, 300, { steps: 4 })
  await page.mouse.up()
  await expect.poll(async () => Math.round((await column.boundingBox())!.width)).toBe(400)
  await expect.poll(() => page.evaluate(() => window.localStorage.getItem('freeshape.leftWidth'))).toBe('400')
})
