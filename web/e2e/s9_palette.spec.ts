import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { edgeSpot } from './support/pick.ts'
import { readViewport, viewCanvas } from './support/viewport.ts'

// Slice S9, card I9.11: the Search tools palette and the shortcut list in Desktop mode, under WebKit, on a copy of the example
// Bracket. Every state is polled or awaited through a locator: nothing is read once straight after a key or a click.
test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })
test.afterEach(async ({ page }) => closeAll(page.request))

const palette = (page: Page) => page.getByTestId('palette')
const box = (page: Page) => palette(page).getByRole('combobox', { name: 'Search tools' })

test('with an edge selected, Alt+C, fil, Enter opens Fillet with the edge in its field', async ({ page, request }) => {
  await openApp(page)
  await expect.poll(() => page.getByTestId('shell').getAttribute('data-mode')).toBe('desktop')
  // the toolbar's Fillet is on once the types route has answered
  await expect.poll(() => page.locator('[data-tool="fillet"]').getAttribute('data-state')).toBe('ok')
  const spot = await edgeSpot(page, request, 'mouse')
  const canvas = (await viewCanvas(page).boundingBox())!
  await page.mouse.move(canvas.x + spot.x, canvas.y + spot.y)
  await page.mouse.click(canvas.x + spot.x, canvas.y + spot.y)
  await expect.poll(async () => (await readViewport(page)).selection.items.map((s) => s.kind)).toEqual(['edge'])

  await page.keyboard.press('Alt+KeyC')
  await expect(palette(page)).toBeVisible()
  await expect(box(page)).toBeFocused()
  await page.keyboard.type('fil')
  // Fillet ranks first, as search_commands ranks it
  await expect(palette(page).getByRole('option').first()).toContainText('Fillet')
  await expect(palette(page).getByRole('option', { selected: true })).toContainText('Fillet')
  await page.keyboard.press('Enter')
  await expect(palette(page)).toHaveCount(0)
  const dialog = page.getByRole('dialog', { name: /Fillet/ })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByTestId('dialog-form')).toContainText(/Edge \d+ of /)
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('Ctrl+Space opens the palette, a menu command is listed with its chord, Esc closes it and starts nothing', async ({ page }) => {
  await openApp(page)
  await page.keyboard.press('Control+Space')
  await expect(palette(page)).toBeVisible()
  await page.keyboard.type('save')
  await expect(palette(page).getByRole('option').first()).toContainText('Save')
  await expect(palette(page).getByRole('option').first()).toContainText('Ctrl+S')
  await page.keyboard.press('Escape')
  await expect(palette(page)).toHaveCount(0)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  // the Search tools button opens it, and a tool the client cannot draw is dimmed and runs nothing
  await page.locator('[data-region="top-bar"]').getByRole('button', { name: 'Search tools' }).click()
  await expect(palette(page)).toBeVisible()
  await page.keyboard.type('derived')
  const row = palette(page).getByRole('option', { name: /^Derived/ })
  await expect(row).toHaveAttribute('aria-disabled', 'true')
  await page.keyboard.press('Enter')
  await expect(palette(page)).toBeVisible()
  await expect(page.getByRole('dialog', { name: /Derived/ })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(palette(page)).toHaveCount(0)
})

test('the ? button lists every key, grouped by workspace; Esc closes the list', async ({ page }) => {
  await openApp(page)
  await page.locator('[data-region="top-bar"]').getByRole('button', { name: 'Keyboard shortcuts' }).click()
  const list = page.getByTestId('shortcuts')
  await expect(list).toBeVisible()
  for (const group of ['Part Studio', 'Sketch', 'Assembly']) await expect(list.getByRole('region', { name: group })).toBeVisible()
  // P is two keys: Show planes in a Part Studio, Point in a sketch
  await expect(list.getByRole('region', { name: 'Part Studio' }).locator('[data-chord="P"]')).toHaveCount(1)
  await expect(list.getByRole('region', { name: 'Sketch' }).locator('[data-chord="P"]')).toHaveCount(1)
  await expect(list.getByRole('region', { name: 'Assembly' }).locator('[data-chord="Ctrl+Z"]')).toHaveCount(1)
  await page.keyboard.press('Escape')
  await expect(list).toHaveCount(0)
})
