import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { featureRow } from './support/features.ts'
import { desktopMode } from './support/mode.ts'
import { faceSpots } from './support/pick.ts'
import { drawn, readViewport, settled, viewCanvas } from './support/viewport.ts'

// Slice S9, card I9.10: right-click context menus in Desktop mode under WebKit, on a copy of the example Bracket. A right
// click without movement opens the desktop's menu at the pointer; a right drag still orbits and opens none. Every state is
// polled or awaited through a locator: nothing is read once straight after a click.
test.use(desktopMode)
test.afterEach(async ({ page }) => closeAll(page.request))

const menu = (page: import('@playwright/test').Page) => page.getByTestId('context-menu')

test('right-click the Extrude row, Suppress, and the part changes; right-click it again, Unsuppress', async ({ page }) => {
  await openApp(page)
  expect(await page.getByTestId('shell').getAttribute('data-mode')).toBe('desktop')
  const before = await drawn(page)
  const row = featureRow(page, 'Extrude 1')
  await row.click({ button: 'right' })
  await expect(menu(page)).toBeVisible()
  await expect(menu(page).getByRole('menuitem')).toHaveText(['Edit…', 'Rename…', 'Suppress', 'Roll to here', 'Add to new folder', 'Delete'])
  await expect(menu(page).getByRole('menuitem', { name: 'Add to new folder' })).toBeDisabled()
  await menu(page).getByRole('menuitem', { name: 'Suppress', exact: true }).click()
  await expect(menu(page)).toHaveCount(0)
  await expect(row).toHaveAttribute('data-state', 'suppressed')
  await expect.poll(async () => (await drawn(page)).faces).not.toBe(before.faces)

  await row.click({ button: 'right' })
  await menu(page).getByRole('menuitem', { name: 'Unsuppress', exact: true }).click()
  await expect(row).not.toHaveAttribute('data-state', 'suppressed')
  await expect.poll(async () => (await drawn(page)).faces).toBe(before.faces)
})

test('a right click on the view opens the view menu at the pointer; a right drag orbits and opens none', async ({ page }) => {
  await openApp(page)
  const canvas = (await viewCanvas(page).boundingBox())!
  const spots = await faceSpots(page, page.request, 'mouse')
  expect(spots.length).toBeGreaterThan(0)
  const spot = spots[0]!
  const x = canvas.x + spot.x
  const y = canvas.y + spot.y

  // over a face: its part's items, then the view's
  await page.mouse.click(x, y, { button: 'right' })
  await expect(menu(page)).toBeVisible()
  const items = await menu(page).getByRole('menuitem').allTextContents()
  expect(items.slice(0, 2)).toEqual(['New sketch', 'Normal to'])
  expect(items).toEqual(expect.arrayContaining(['Hide part', 'Isolate part', 'Select part', 'Zoom to fit', 'Views']))
  const box = (await menu(page).boundingBox())!
  expect(Math.abs(box.x - x)).toBeLessThan(2)
  // Hide part hides the part
  await menu(page).getByRole('menuitem', { name: 'Hide part' }).click()
  await expect.poll(async () => (await readViewport(page)).hiddenParts).toBeGreaterThan(0)

  // the menu now has Show all parts; it brings the part back
  await page.mouse.click(x, y, { button: 'right' })
  await menu(page).getByRole('menuitem', { name: 'Show all parts' }).click()
  await expect.poll(async () => (await readViewport(page)).hiddenParts).toBe(0)

  // a right drag orbits the view and opens no menu
  const was = (await settled(page)).camera.back
  await page.mouse.move(x, y)
  await page.mouse.down({ button: 'right' })
  await page.mouse.move(x + 60, y + 25, { steps: 6 })
  await page.mouse.up({ button: 'right' })
  await expect.poll(async () => (await readViewport(page)).camera.back).not.toEqual(was)
  await expect(menu(page)).toHaveCount(0)
})

test('a right click on a tab and on a part row opens the desktop menu, and Escape closes it', async ({ page }) => {
  await openApp(page)
  await page.locator('.tab-button').first().click({ button: 'right' })
  await expect(menu(page).getByRole('menuitem')).toHaveText(['Rename…', 'Duplicate', 'Close'])
  await page.keyboard.press('Escape')
  await expect(menu(page)).toHaveCount(0)

  await page.locator('.part-select[data-part]').first().click({ button: 'right' })
  await expect(menu(page).getByRole('menuitem').first()).toHaveText('Rename…')
  await expect(menu(page).getByRole('menuitem', { name: 'Export STEP…' })).toBeDisabled()
  await page.keyboard.press('Escape')
  await expect(menu(page)).toHaveCount(0)
})
