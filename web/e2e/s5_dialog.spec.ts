import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, idOf } from './support/docs.ts'
import { featureRow } from './support/features.ts'
import { edgeSpot } from './support/pick.ts'
import { sendPointer } from './support/pointer.ts'
import { readViewport } from './support/viewport.ts'

// Slice S5, the feature dialog (card I5.6), in the real client against `freeshape serve`: a double tap on the bracket's
// Extrude 1 opens its dialog, a depth typed on the pad is sent as a dialog update, the preview the server pushes is
// drawn tinted in place of the committed model, and Accept commits it (the revision rises) or Cancel drops it.

test.afterEach(async ({ page }) => closeAll(page.request))

const revisionOnServer = async (page: Page): Promise<number> => {
  const id = await idOf(page.request, 'Bracket')
  return ((await (await page.request.get(`/documents/${id}/tabs`)).json()).tabs as Array<{ revision: number }>)[0]!.revision
}
const header = (page: Page, name: string) => page.locator('.dialog-header').getByRole('button', { name, exact: true })
const key = async (page: Page, ...names: string[]) => {
  for (const n of names) await page.getByTestId('pad').getByRole('button', { name: n, exact: true }).click()
}

async function openExtrude(page: Page): Promise<void> {
  await openApp(page)
  await featureRow(page, 'Extrude 1').dblclick()
  await expect(page.getByRole('dialog', { name: 'Extrude 1' })).toBeVisible()
  // the first number (Depth) is on the pad, as in the layout
  await expect(page.getByTestId('pad')).toBeVisible()
  await expect(page.getByTestId('pad-field')).toContainText('40')
}

async function changeDepth(page: Page): Promise<void> {
  await key(page, '5', '0', 'Enter')
  await page.waitForFunction(() => (window.__freeshapeViewport?.preview ?? 0) >= 1)
  const v = await readViewport(page)
  expect(v.tintedFeature).toBe('F2')
  expect(v.tintedParts).toBeGreaterThan(0)
  await expect(page.getByRole('button', { name: /^Depth, 50 mm/ })).toBeVisible()
}

test('a double tap opens Extrude 1; the depth on the pad draws a tinted preview and Accept commits it', async ({ page }) => {
  await openExtrude(page)
  const rev = (await readViewport(page)).revision!
  await changeDepth(page)
  // a preview commits nothing
  expect(await revisionOnServer(page)).toBe(rev)
  await header(page, 'Accept').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r && window.__freeshapeViewport?.preview === null, rev)
  expect((await readViewport(page)).tintedFeature).toBeNull()
  expect(await revisionOnServer(page)).toBeGreaterThan(rev)
})

test('Cancel drops the preview: the committed revision is back, nothing is tinted, nothing was committed', async ({ page }) => {
  await openExtrude(page)
  const rev = (await readViewport(page)).revision!
  await changeDepth(page)
  await header(page, 'Cancel').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction(() => window.__freeshapeViewport?.preview === null)
  const v = await readViewport(page)
  expect(v.revision).toBe(rev)
  expect(v.tintedFeature).toBeNull()
  expect(await revisionOnServer(page)).toBe(rev)
})

test('the row menu has Edit, and a refused number shows its message under the field', async ({ page }) => {
  await openApp(page)
  const box = (await featureRow(page, 'Extrude 1').boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.getByRole('menu').waitFor()
  await page.mouse.up()
  await page.getByRole('menuitem', { name: 'Edit', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Extrude 1' })).toBeVisible()
  await key(page, 'Backspace', '0', 'Enter')
  await expect(page.locator('.dlg-message')).toHaveText('the value cannot be zero')
  // the field shows what was typed, marked invalid; the server keeps the last good depth for the preview
  await expect(page.locator('.dlg-field[data-invalid]')).toBeVisible()
  await header(page, 'Cancel').click()
})

test('Fillet 1: tap the edge field, pick another edge with the pen, see its chip and a preview, and Accept commits it', async ({ page, request }) => {
  await openApp(page)
  await featureRow(page, 'Fillet 1').dblclick()
  await expect(page.getByRole('dialog', { name: 'Fillet 1' })).toBeVisible()
  const field = page.locator('[data-testid^="sel-"]').first()
  const chips = field.getByTestId('sel-chip')
  await expect(chips.first()).toBeVisible()
  const rev = (await readViewport(page)).revision!
  // tapping the field makes it the active one: the view now picks edges for it
  await field.getByRole('button', { name: /Tap, then pick/ }).click()
  await expect(field).toHaveAttribute('data-active', '')
  await expect.poll(async () => (await readViewport(page)).selection.multi).toBe(true)
  // the view draws the model before Fillet 1 (card F3), and picks resolve against it: the field's own edge is there
  await page.locator('[data-dialog-base]').waitFor()
  const had = await chips.allTextContents()
  // a stored reference without an Element name (the bracket's own, an older file's) reads just "Edge"
  for (const t of had) expect(t).toMatch(/^(Edge \d+ of .+|Edge)$/)
  const held = had.flatMap((t) => /^Edge (\d+) of /.exec(t)?.slice(1).map((n) => Number(n) - 1) ?? [])
  // an edge the field does not hold (a pick of one it holds would take it out)
  const spot = await edgeSpot(page, request, 'pen', held)
  const before = (await readViewport(page)).preview ?? 0
  await sendPointer(page, [{ phase: 'down', kind: 'pen', id: 7, x: spot.x, y: spot.y }])
  await sendPointer(page, [{ phase: 'up', kind: 'pen', id: 7, x: spot.x, y: spot.y, pressure: 0 }])
  await expect(chips).toHaveCount(had.length + 1)
  // chips read "Edge 7 of Part 1", never the encoded Element name; the new one numbers the edge as the view drew it
  for (const t of await chips.allTextContents()) expect(t).toMatch(/^(Edge \d+ of .+|Edge)$/)
  await expect.poll(async () => (await chips.allTextContents()).some((t) => t.startsWith(`Edge ${spot.edge + 1} of `))).toBe(true)
  // the pick went to the field, not to the general selection: the chip of the view counts the field's items
  await page.waitForFunction((n) => (window.__freeshapeViewport?.preview ?? 0) > n, before)
  expect((await readViewport(page)).tintedFeature).not.toBeNull()
  // the edge exists in the model the feature is rebuilt on: the preview builds, and no Element name is shown
  await expect(page.getByTestId('preview-error')).toHaveCount(0)
  expect(await revisionOnServer(page)).toBe(rev)
  await header(page, 'Accept').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r && window.__freeshapeViewport?.preview === null, rev)
  // the general selection is as it was before the dialog: nothing
  expect((await readViewport(page)).selection.count).toBe(0)
})
