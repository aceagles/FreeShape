import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { featureRow } from './support/features.ts'
import { desktopMode } from './support/mode.ts'
import { pickAll } from './support/pick.ts'
import type { Spot } from './support/pick.ts'
import { readViewport, settled, viewCanvas } from './support/viewport.ts'

// Slice S9, card I9.14: the Select menu's filter reaches the selection box, and a mouse double click on a committed sketch's
// region opens the sketch for editing. Real mouse events under WebKit, in Desktop mode.
test.use(desktopMode)
test.afterEach(async ({ page }) => closeAll(page.request))

const kinds = async (page: Page) => [...new Set((await readViewport(page)).selection.items.map((i) => i.kind))].sort()

async function tick(page: Page, label: RegExp): Promise<void> {
  await page.getByRole('button', { name: 'Select' }).click()
  await page.getByRole('menuitemcheckbox', { name: label }).click()
}

/** A box over the whole model, dragged from corner to corner of the view (inside the model's own extent, left to right). */
async function boxOverModel(page: Page): Promise<void> {
  const canvas = (await viewCanvas(page).boundingBox())!
  const cam = (await settled(page)).camera
  const [x0, y0, x1, y1] = [canvas.x + 4, canvas.y + 4, canvas.x + cam.width - 4, canvas.y + cam.height - 4]
  await page.mouse.move(x0, y0)
  await page.mouse.down()
  await page.mouse.move(x1, y1, { steps: 8 })
  await page.mouse.up()
}

test('with Faces unticked a box over the bracket selects edges only', async ({ page }) => {
  await openApp(page)
  // the Select menu shows the desktop's defaults
  await page.getByRole('button', { name: 'Select' }).click()
  const ticks = () => page.getByRole('menuitemcheckbox').evaluateAll((els) => els.map((e) => `${e.textContent!.trim()}=${e.getAttribute('aria-checked')}`))
  await expect.poll(ticks).toEqual(['Vertices=false', 'Edges=true', 'Faces=true', 'Solids (whole parts)=false', 'Sketches=true', 'Planes=true', 'Axes=true'])
  await page.keyboard.press('Escape')

  await boxOverModel(page)
  await expect.poll(() => kinds(page)).toContain('face')
  expect(await kinds(page)).toContain('edge')

  await tick(page, /^Faces/)
  // the faces that were selected go with the tick
  await expect.poll(() => kinds(page)).not.toContain('face')
  await boxOverModel(page)
  await expect.poll(async () => (await readViewport(page)).selection.count).toBeGreaterThan(0)
  await expect.poll(() => kinds(page)).toEqual(['edge'])

  // Reset to default brings the faces back
  await page.getByRole('button', { name: 'Select' }).click()
  await page.getByRole('menuitem', { name: /Reset to default/ }).click()
  await boxOverModel(page)
  await expect.poll(() => kinds(page)).toContain('face')
})

test('a double click on a committed sketch\'s region opens the sketch', async ({ page }) => {
  await openApp(page)
  // the profile sketch is hidden once Extrude 1 takes it: show it from its row's eye
  const row = featureRow(page, 'Bracket profile')
  const id = (await row.getAttribute('data-feature'))!
  const eye = page.locator(`[data-feature-eye="${id}"]`)
  if ((await eye.getAttribute('aria-label')) === 'Show sketch') await eye.click()
  await expect(eye).toHaveAttribute('aria-label', 'Hide sketch')
  await expect.poll(async () => (await settled(page)).sketchRegions).toBeGreaterThan(0)

  // a spot where the mouse picks this sketch's region with the menu's default filter
  const cam = (await settled(page)).camera
  const grid: Spot[] = []
  for (let y = 8; y < cam.height - 8; y += 8) for (let x = 8; x < cam.width - 8; x += 8) grid.push({ x, y })
  const hits = await pickAll(page, grid, 'mouse', ['edge', 'face', 'region', 'plane', 'axis'])
  const at = grid[hits.findIndex((h) => h?.kind === 'region' && h.part === id)]
  expect(at).toBeDefined()

  const canvas = (await viewCanvas(page).boundingBox())!
  await page.mouse.move(canvas.x + at!.x, canvas.y + at!.y)
  await page.mouse.dblclick(canvas.x + at!.x, canvas.y + at!.y)
  await expect(page.locator('[data-strip="sketch"]')).toBeVisible()
  // the strip shows before the view has the sketch: wait for it
  await expect.poll(async () => (await readViewport(page)).sketch).not.toBeNull()
})
