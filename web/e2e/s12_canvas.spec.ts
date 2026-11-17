import { resolve } from 'node:path'
import { inflateSync } from 'node:zlib'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, removeDocument } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { sendPointer } from './support/pointer.ts'
import { readViewport, viewCanvas } from './support/viewport.ts'

// Slice S12, card I12.5: a Canvas in the real client against `freeshape serve`. A new Document with an empty Part Studio, so nothing
// is in front of the picture; the picker is a file chooser answered with the 64 x 32 PNG (red, a blue square at its bottom left,
// made by web/fixtures/make_canvas_fixture.py). The tool uploads it, the generic dialog opens on it, Accept places it on Top: the
// feature list shows a Canvas, the view draws it (the pixel inside its rectangle is the picture's red, not the background), and
// picking it selects its row. Every check after an action waits on state (a poll or a locator).
const NAME = 'Canvas fixture'
const PNG = resolve(import.meta.dirname, 'fixtures/canvas.png')

test.beforeEach(async ({ page }) => {
  await closeAll(page.request)
  removeDocument(NAME)
  const made = (await (await page.request.post('/documents', { data: { name: NAME } })).json()) as { id: string }
  await page.request.post(`/documents/${made.id}/open`, { data: {} })
  await page.request.post(`/documents/${made.id}/save`, { data: {} })
  await page.request.post(`/documents/${made.id}/close`, { data: {} })
})
test.afterEach(async ({ page }) => {
  await closeAll(page.request)
  removeDocument(NAME)
})

/** The colour of one pixel of the page (CSS pixels from its top left): the screenshot of a 1 x 1 clip, its PNG inflated. */
async function pixel(page: Page, x: number, y: number): Promise<[number, number, number]> {
  const png = await page.screenshot({ clip: { x: Math.floor(x), y: Math.floor(y), width: 1, height: 1 } })
  const kind = png[25]! // IHDR colour type: 2 is RGB, 6 is RGBA
  const idat = png.indexOf('IDAT') + 4
  const length = png.readUInt32BE(idat - 8)
  const raw = inflateSync(png.subarray(idat, idat + length))
  const step = kind === 6 ? 4 : 3
  // the device pixel ratio may make the clip several pixels wide: the first pixel of the first row is the one asked for
  expect(raw.length).toBe(png.readUInt32BE(20) * (1 + png.readUInt32BE(16) * step))
  return [raw[1]!, raw[2]!, raw[3]!]
}

const canvases = (page: Page) => page.evaluate(() => window.__freeshapeViewport!.canvasScreens())
const accept = (page: Page) => page.getByRole('dialog').getByRole('button', { name: 'Accept', exact: true })

/** Everything the card asks of the tool, from the tool to the placed canvas; `start` taps the Canvas tool in the mode's own toolbar. */
async function place(page: Page, start: () => Promise<void>): Promise<void> {
  await openApp(page, { document: NAME, model: false })
  await expect.poll(async () => (await readViewport(page)).frames).toBeGreaterThan(0)
  await expect(page.locator('.panel-row[data-feature]')).toHaveCount(0)
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), start()])
  await chooser.setFiles(PNG)
  // the generic dialog is open on the uploaded image: Width and Height are preset, and the preview draws the canvas under the dialog
  await expect(page.getByRole('dialog', { name: /Canvas/ })).toBeVisible()
  await expect(page.getByRole('dialog')).toContainText('photo'.slice(0, 0) + 'canvas.png')
  await expect.poll(async () => (await readViewport(page)).canvases).toBe(1)
  await accept(page).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.locator('.panel-row[data-feature]', { hasText: 'Canvas' })).toHaveCount(1)
  // drawn: the picture arrived and is on the canvas
  await expect.poll(async () => (await canvases(page)).map((c) => c.pictured)).toEqual([true])
  expect((await canvases(page))[0]!.error).toBeNull()
}

/** The probe: the picture's red inside the rectangle, and not the background's colour (a pixel of the view's empty corner). */
async function expectDrawn(page: Page): Promise<void> {
  const box = (await viewCanvas(page).boundingBox())!
  const [c] = await canvases(page)
  // two frames, so the picture has been drawn (the view draws on demand)
  await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))))
  await expect
    .poll(async () => {
      const [r, g, b] = await pixel(page, box.x + c!.inside.x, box.y + c!.inside.y)
      return r - Math.max(g, b)
    })
    .toBeGreaterThan(40)
  const inside = await pixel(page, box.x + c!.inside.x, box.y + c!.inside.y)
  const background = await pixel(page, box.x + 4, box.y + 4)
  expect(inside).not.toEqual(background)
}

/** A pick on the canvas selects its feature row (the chip says "1 canvas"). */
async function expectPicked(page: Page, kind: 'pen' | 'mouse'): Promise<void> {
  const [c] = await canvases(page)
  await sendPointer(page, [{ phase: 'down', kind, id: 7, ...c!.inside }])
  await sendPointer(page, [{ phase: 'up', kind, id: 7, ...c!.inside, pressure: 0 }])
  await expect(page.getByTestId('selection-chip')).toContainText('1 canvas')
  await expect(page.getByTestId('feature-panel')).toHaveAttribute('data-selected-feature', c!.id)
}

test.describe('Tablet mode', () => {
  test('Create > Canvas: upload a PNG, place it on Top, Accept; the list shows a Canvas, the view draws it, a pen pick selects its row', async ({ page }) => {
    await place(page, async () => {
      await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
      await page.getByRole('menuitem', { name: /^Canvas/ }).click()
    })
    // the number pad's rule: nothing native on the page, here once the dialog is closed again
    expect(await page.locator('input, textarea, select, [contenteditable]').count()).toBe(0)
    await expectDrawn(page)
    await expectPicked(page, 'pen')
  })
})

test.describe('Desktop mode', () => {
  test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })

  test('the toolbar\'s Canvas button: the same flow, drawn, and a mouse click selects its row', async ({ page }) => {
    await place(page, () => page.locator('.desktop-toolbar [data-tool="canvas"]').click())
    await expectDrawn(page)
    await expectPicked(page, 'mouse')
  })
})
