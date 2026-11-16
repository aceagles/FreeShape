import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, idOf } from './support/docs.ts'
import { featureRow } from './support/features.ts'
import { desktopMode } from './support/mode.ts'
import { openSketchFeature, readSketch, screenOf, settledSketch } from './support/sketch.ts'
import { readViewport, viewCanvas } from './support/viewport.ts'

// Slice S9, card I9.16: Desktop mode's typed fields against `freeshape serve`. Desktop mode, 1440 x 900, mouse and
// keyboard: the Extrude's depth is typed in its row and Enter accepts; a sketch dimension is typed in the inline box.
test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })
test.afterEach(async ({ page }) => closeAll(page.request))

const revisionOnServer = async (page: Page): Promise<number> => {
  const id = await idOf(page.request, 'Bracket')
  return ((await (await page.request.get(`/documents/${id}/tabs`)).json()).tabs as Array<{ revision: number }>)[0]!.revision
}
const depthField = (page: Page) => page.getByRole('dialog', { name: 'Extrude 1' }).getByRole('textbox', { name: 'Depth' })

test('the Extrude: a depth typed in its row and Enter rebuild the feature with that depth', async ({ page }) => {
  await openApp(page)
  const rev = (await readViewport(page)).revision!
  await featureRow(page, 'Extrude 1').dblclick()
  await expect(depthField(page)).toHaveValue('40')
  // a typed field, no pad
  await expect(page.getByTestId('pad')).toHaveCount(0)
  await depthField(page).click()
  await page.keyboard.press('Control+A')
  await page.keyboard.type('25')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r && window.__freeshapeViewport?.preview === null, rev)
  expect(await revisionOnServer(page)).toBeGreaterThan(rev)
  // the feature holds the new depth
  await featureRow(page, 'Extrude 1').dblclick()
  await expect(depthField(page)).toHaveValue('25')
  await page.locator('.dialog-header').getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('a sketch: a double click on a dimension, a value typed in the inline box and Enter solve the sketch to it', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  const labels = () => page.locator('.sketch-layer g[data-kind="dimension"] text').evaluateAll((els) => els.map((e) => e.textContent ?? ''))
  await settledSketch(page)
  expect(await labels()).toContain('60 mm')
  const at = await screenOf(page, 'dimension', 'k19')
  const canvas = (await viewCanvas(page).boundingBox())!
  await page.mouse.dblclick(canvas.x + at.x, canvas.y + at.y)
  const box = page.getByTestId('dimension-inline').getByRole('textbox')
  await expect(box).toBeFocused()
  await expect(box).toHaveValue('60')
  // the box is at the label, the pad is not open
  const b = (await page.getByTestId('dimension-inline').boundingBox())!
  expect(Math.abs(b.x + b.width / 2 - (canvas.x + at.x))).toBeLessThan(60)
  expect(Math.abs(b.y - (canvas.y + at.y))).toBeLessThan(40)
  await expect(page.getByTestId('pad')).toHaveCount(0)
  await page.keyboard.type('50')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('dimension-inline')).toHaveCount(0)
  await expect.poll(async () => (await labels()).includes('50 mm'), { message: JSON.stringify(await labels()) }).toBe(true)
  expect((await readSketch(page)).stats.labels).toBe(4)
  // the sketch is left as it is: afterEach closes every Document without saving
})
