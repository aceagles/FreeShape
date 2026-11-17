import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { faceSpots } from './support/pick.ts'
import { sendPointer } from './support/pointer.ts'
import { settled, viewCanvas } from './support/viewport.ts'

// Slice S9, card I9.3: the Desktop feature toolbar. Desktop mode, 1440 x 900, mouse.
test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })
test.afterEach(async ({ page }) => closeAll(page.request))

const toolbar = (page: import('@playwright/test').Page) => page.locator('.desktop-toolbar')

test('Extrude on the toolbar, with a face of the bracket selected, opens its dialog with the face in its field', async ({ page, request }) => {
  await openApp(page)
  // the row is one 44 px line of 26 px icon buttons
  const bar = (await toolbar(page).boundingBox())!
  expect(Math.round(bar.height)).toBe(44)
  const extrude = toolbar(page).getByRole('button', { name: 'Extrude', exact: true })
  const box = (await extrude.boundingBox())!
  expect([Math.round(box.width), Math.round(box.height)]).toEqual([26, 26])
  await expect(extrude).toHaveAttribute('title', 'Extrude (Shift+E)')
  // the types route answers: Mate connector is a standard dialog since I10.4; Hole still has rows only the desktop draws
  await expect(toolbar(page).getByRole('button', { name: 'Mate connector' })).toBeEnabled()
  await expect(toolbar(page).getByRole('button', { name: 'Hole', exact: true })).toBeDisabled()
  await expect(toolbar(page).getByRole('button', { name: 'Hole', exact: true })).toHaveAttribute('title', 'Desktop only for now')

  // click a face with the mouse, then Extrude
  const [face] = await faceSpots(page, request, 'mouse')
  await sendPointer(page, [{ phase: 'move', kind: 'mouse', id: 20, ...face! }])
  await sendPointer(page, [
    { phase: 'down', kind: 'mouse', id: 20, ...face!, button: 0 },
    { phase: 'up', kind: 'mouse', id: 20, ...face!, button: 0 },
  ])
  await expect(page.getByTestId('selection-count')).toHaveText('1 face')
  await expect(extrude).toBeEnabled()
  await extrude.click()
  const dialog = page.getByRole('dialog', { name: /^Extrude/ })
  await expect(dialog).toBeVisible()
  await expect(page.locator('[data-testid^="sel-"]').first().getByTestId('sel-chip')).toHaveCount(1)
  // the feature buttons are off while the dialog is open; Fit still works
  await expect(toolbar(page).getByRole('button', { name: 'Fillet' })).toBeDisabled()
  await expect(toolbar(page).getByRole('button', { name: 'Zoom to fit' })).toBeEnabled()
})

test('Fit after a zoom fits the view again, and Iso looks along the isometric view', async ({ page }) => {
  await openApp(page)
  const fitted = (await settled(page)).camera
  const c = (await viewCanvas(page).boundingBox())!
  await page.mouse.move(c.x + c.width / 2, c.y + c.height / 2)
  for (let i = 0; i < 4; i++) await page.mouse.wheel(0, -240)
  await expect.poll(async () => (await settled(page)).camera.scale).toBeLessThan(fitted.scale * 0.9)
  await toolbar(page).getByRole('button', { name: 'Zoom to fit' }).click()
  await expect.poll(async () => (await settled(page)).camera.scale).toBeCloseTo(fitted.scale, 3)
  await toolbar(page).getByRole('button', { name: 'Isometric' }).click()
  const iso = (await settled(page)).camera
  for (const v of iso.back) expect(Math.abs(v)).toBeCloseTo(0.577, 2)
})
