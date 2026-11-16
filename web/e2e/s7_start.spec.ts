import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, idOf } from './support/docs.ts'
import { featureMenu } from './support/features.ts'
import { faceSpots } from './support/pick.ts'
import type { Spot } from './support/pick.ts'
import { chooseWithRing, place, regionSpot, screenFor } from './support/draw.ts'
import { fingerTap, penStroke } from './support/pointer.ts'
import { settledSketch } from './support/sketch.ts'
import { readViewport, settled } from './support/viewport.ts'

// Slice S7, starting a sketch, editing one and using its regions (card I7.13) against `freeshape serve`: the Sketch tool with
// nothing selected asks for a plane; a rectangle is drawn with the pen; Done returns to the Part Studio, where a pen Commit
// inside the new region selects it and Extrude takes it. Edit on a sketch feature opens it, and Cancel asks before it drops changes.

test.afterEach(async ({ page }) => closeAll(page.request))

const features = (page: Page) => page.locator('.panel-row[data-feature]')

test('Sketch with nothing selected asks for a plane; draw, Done, pick the region with the pen, and Extrude takes it', async ({ page }) => {
  await openApp(page)
  const before = await features(page).count()
  await page.locator('[data-tool="sketch"]').click()
  const ask = page.getByRole('dialog', { name: 'Sketch on which plane?' })
  await expect(ask.getByRole('button')).toHaveText(['Cancel', 'Top', 'Front', 'Right'])
  await ask.getByRole('button', { name: 'Top', exact: true }).click()
  await expect(page.locator('[data-strip="sketch"]')).toBeVisible({ timeout: 20_000 })
  await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.paints ?? 0) > 0)
  // a rectangle clear of the bracket (the Bracket lies at x 0 to 60), so nothing hides its region afterwards
  await chooseWithRing(page, 'rectangle')
  await place(page, await screenFor(page, -30, -10))
  await place(page, await screenFor(page, -10, 10))
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(4)
  const rev = (await readViewport(page)).revision!
  await page.getByTestId('sketch-done').click()
  await expect(page.locator('[data-strip="feature"]')).toBeVisible({ timeout: 20_000 })
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r, rev)
  await expect(features(page)).toHaveCount(before + 1)
  await expect(page.getByTestId('selection-count')).toHaveText('Nothing selected')

  // the region lies on the Top plane at x -30 to -10, y -10 to 10: find it in the view and press the pen there
  const at = await regionSpot(page)
  const hit = await page.evaluate(({ x, y }) => window.__freeshapeViewport!.pickAt(x, y, 'pen'), at)
  expect(hit).toMatchObject({ kind: 'region', index: 0 })
  await penStroke(page, at, at)
  await expect(page.getByTestId('selection-count')).toHaveText('1 region')
  const sel = (await readViewport(page)).selection
  expect(sel.items.map((i) => [i.kind, i.index])).toEqual([['region', 0]])

  // Extrude takes the region from the selection
  await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
  await page.getByRole('menuitem', { name: /^Extrude/ }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByTestId('dialog-form')).toContainText(/Region 1 of Sketch/)
  // tap the field: its region is lit in the view, as a face is (the view draws the dialog's base, which has the sketch)
  await dialog.locator('[data-testid^="sel-"]').first().getByRole('button', { name: /Tap, then pick/ }).click()
  await expect.poll(async () => (await readViewport(page)).selection.items.map((i) => i.kind), { timeout: 15_000 }).toEqual(['region'])
  // the dialog's preview is drawn: a ghost of the extrusion over the base
  await expect.poll(async () => (await readViewport(page)).preview, { timeout: 15_000 }).not.toBeNull()
  const rev2 = (await readViewport(page)).revision!
  await dialog.getByRole('button', { name: 'Accept', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r, rev2)
  await expect(features(page)).toHaveCount(before + 2)
})

test('Edit on a sketch feature opens it; Cancel asks before it drops a change, and Done returns to the Part Studio', async ({ page }) => {
  await openApp(page)
  await featureMenu(page, 'Bracket profile', 'Edit')
  await expect(page.locator('[data-strip="sketch"]')).toBeVisible({ timeout: 20_000 })
  await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.paints ?? 0) > 0)
  await expect(page.getByTestId('sketch-status')).toContainText('Bracket profile')
  // nothing changed yet: Cancel goes straight back
  await page.getByTestId('sketch-cancel').click()
  await expect(page.locator('[data-strip="feature"]')).toBeVisible()
  await expect(page.getByRole('dialog', { name: 'Discard this sketch?' })).toHaveCount(0)

  await featureMenu(page, 'Bracket profile', 'Edit')
  await expect(page.locator('[data-strip="sketch"]')).toBeVisible({ timeout: 20_000 })
  await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.paints ?? 0) > 0)
  // a point drawn is a change
  await chooseWithRing(page, 'point')
  await place(page, await screenFor(page, 20, 30))
  await expect.poll(async () => (await settledSketch(page)).stats.points).toBeGreaterThan(0)
  await page.getByTestId('sketch-cancel').click()
  const ask = page.getByRole('dialog', { name: 'Discard this sketch?' })
  await expect(ask).toBeVisible()
  await ask.getByRole('button', { name: 'Keep sketching' }).click()
  await expect(page.locator('[data-strip="sketch"]')).toBeVisible()
  await page.getByTestId('sketch-cancel').click()
  await page.getByRole('dialog', { name: 'Discard this sketch?' }).getByRole('button', { name: 'Discard' }).click()
  await expect(page.locator('[data-strip="feature"]')).toBeVisible()
  await expect(page.locator('[data-strip="sketch"]')).toHaveCount(0)
})

test('with the Top plane selected, Sketch opens on it without asking', async ({ page }) => {
  await openApp(page)
  // a plane is picked where the pen finds one and no face or edge is: scan the view for a plane pick
  const cam = (await settled(page)).camera
  const grid: Spot[] = []
  for (let y = 8; y < cam.height; y += 12) for (let x = 8; x < cam.width; x += 12) grid.push({ x, y })
  const hits = await page.evaluate((g) => g.map((s) => window.__freeshapeViewport!.pickAt(s.x, s.y, 'pen')), grid)
  const i = hits.findIndex((h) => h?.kind === 'plane' && h.key === 'Top')
  expect(i).toBeGreaterThanOrEqual(0)
  await penStroke(page, grid[i]!, grid[i]!)
  await expect(page.getByTestId('selection-count')).toHaveText('1 plane')
  await page.locator('[data-tool="sketch"]').click()
  await expect(page.locator('[data-strip="sketch"]')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('dialog', { name: 'Sketch on which plane?' })).toHaveCount(0)
  // the view is square to the Top plane: it looks along z
  await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.tilt ?? 1) < 1e-3)
  const back = (await settled(page)).camera.back
  expect(Math.abs(back[2])).toBeGreaterThan(0.999)
})

// F12: on the iPad a flat face picked by finger or Pencil, then Sketch pressed the same way, opened no sketch (the client
// wrapped the refs route's plane reference in a second one, the server refused it, and nothing was said)
async function flatFaceSpot(page: Page, pointer: 'pen' | 'touch'): Promise<Spot> {
  const doc = await idOf(page.request, 'Bracket')
  const spots = await faceSpots(page, page.request, pointer)
  for (const s of spots) {
    const hit = await page.evaluate((s) => window.__freeshapeViewport!.pickAt(s.x, s.y, s.pointer), { ...s, pointer })
    if (hit?.kind !== 'face') continue
    const r = await page.request.post(`/documents/${doc}/tabs/T1/refs`, { data: { picks: [{ part: hit.part, kind: 'face', index: hit.index + 1, want: ['plane'] }] } })
    const items = ((await r.json()) as { items: { ok: boolean }[] }).items
    if (items[0]?.ok) return s
  }
  throw new Error('no flat face of the Bracket is in view')
}

/** A press on a button as the Pencil makes it: pointerdown and pointerup of kind pen, then the click the browser follows them with. */
async function penPress(page: Page, selector: string): Promise<void> {
  const box = (await page.locator(selector).first().boundingBox())!
  await page.evaluate(
    ({ selector, x, y }) => {
      const el = document.querySelector(selector)!
      const o = { bubbles: true, cancelable: true, composed: true, pointerId: 10, pointerType: 'pen', isPrimary: true, clientX: x, clientY: y, button: 0 }
      el.dispatchEvent(new PointerEvent('pointerdown', { ...o, buttons: 1, pressure: 0.5 }))
      el.dispatchEvent(new PointerEvent('pointerup', { ...o, buttons: 0, pressure: 0 }))
      el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: x, clientY: y, detail: 1 }))
    },
    { selector, x: box.x + box.width / 2, y: box.y + box.height / 2 },
  )
}

for (const input of ['finger', 'pen'] as const) {
  test(`with a flat face picked by the ${input}, Sketch pressed by the ${input} opens on that face (F12)`, async ({ page }) => {
    await openApp(page)
    const at = await flatFaceSpot(page, input === 'finger' ? 'touch' : 'pen')
    if (input === 'finger') await fingerTap(page, at)
    else await penStroke(page, at, at)
    await expect(page.getByTestId('selection-count')).toHaveText('1 face')
    if (input === 'finger') await page.locator('[data-tool="sketch"]').tap()
    else await penPress(page, '[data-tool="sketch"]')
    await expect(page.locator('[data-strip="sketch"]')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByRole('dialog')).toHaveCount(0)
    // the view turns square to the face
    await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.tilt ?? 1) < 1e-3)
    await page.getByTestId('sketch-cancel').click()
    await expect(page.locator('[data-strip="feature"]')).toBeVisible()
  })
}
