import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, idOf } from './support/docs.ts'
import { featureMenu, featureRow } from './support/features.ts'
import { edgeSpot } from './support/pick.ts'
import { sendPointer } from './support/pointer.ts'
import { readViewport, viewCanvas } from './support/viewport.ts'

// Slice S5 gate (card I5.8): editing a feature in the real client against `freeshape serve`. Edit from the row menu opens
// the dialog over the feature panel without resizing the view; a depth on the pad previews, Cancel restores the committed
// revision, and Accept raises it; the fillet's edge list changes by removing a chip and picking another edge.

test.afterEach(async ({ page }) => closeAll(page.request))

const revisionOnServer = async (page: Page): Promise<number> => {
  const id = await idOf(page.request, 'Bracket')
  return ((await (await page.request.get(`/documents/${id}/tabs`)).json()).tabs as Array<{ revision: number }>)[0]!.revision
}
const header = (page: Page, name: string) => page.locator('.dialog-header').getByRole('button', { name, exact: true })
const key = async (page: Page, ...names: string[]) => {
  for (const n of names) await page.getByTestId('pad').getByRole('button', { name: n, exact: true }).click()
}

async function editFromMenu(page: Page, feature: string): Promise<void> {
  await featureMenu(page, feature, 'Edit')
  await expect(page.getByRole('dialog', { name: feature })).toBeVisible()
}

/** Depth 50 on the pad, the preview that follows, and the preview number the hook reports. */
async function previewDepth(page: Page, before: number): Promise<void> {
  await expect(page.getByTestId('pad-field')).toContainText('40')
  await key(page, '5', '0', 'Enter')
  await page.waitForFunction((n) => (window.__freeshapeViewport?.preview ?? 0) > n, before)
  expect((await readViewport(page)).tintedFeature).toBe('F2')
}

test('Edit on the extrude opens the dialog over the feature panel, and the view keeps its width', async ({ page }) => {
  await openApp(page)
  const column = (await page.getByTestId('left-column').boundingBox())!
  const view = (await viewCanvas(page).boundingBox())!
  const cam = (await readViewport(page)).camera
  await editFromMenu(page, 'Extrude 1')
  const dialog = (await page.locator('[data-region="dialog"]').boundingBox())!
  // the dialog sits in the panel column, on top of the feature panel
  expect(dialog.x).toBeGreaterThanOrEqual(column.x - 0.5)
  expect(dialog.x + dialog.width).toBeLessThanOrEqual(column.x + column.width + 0.5)
  // over it: what is at the middle of the feature's row is the dialog
  const row = (await featureRow(page, 'Extrude 1').boundingBox())!
  const covered = await page.evaluate(([x, y]) => !!document.elementFromPoint(x!, y!)?.closest('[data-region="dialog"]'), [row.x + row.width / 2, row.y + row.height / 2])
  expect(covered).toBe(true)
  const after = (await viewCanvas(page).boundingBox())!
  expect(after.width).toBe(view.width)
  expect(after.x).toBe(view.x)
  expect((await readViewport(page)).camera.width).toBe(cam.width)
  await header(page, 'Cancel').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect((await viewCanvas(page).boundingBox())!.width).toBe(view.width)
})

test('a depth on the pad previews; Cancel restores the committed revision; doing it again, Accept raises it', async ({ page }) => {
  await openApp(page)
  const rev = (await readViewport(page)).revision!
  await editFromMenu(page, 'Extrude 1')
  await previewDepth(page, (await readViewport(page)).preview ?? 0)
  expect(await revisionOnServer(page)).toBe(rev)
  await header(page, 'Cancel').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction(() => window.__freeshapeViewport?.preview === null)
  const v = await readViewport(page)
  expect(v.revision).toBe(rev)
  expect(v.tintedFeature).toBeNull()
  expect(await revisionOnServer(page)).toBe(rev)

  await editFromMenu(page, 'Extrude 1')
  await previewDepth(page, (await readViewport(page)).preview ?? 0)
  await header(page, 'Accept').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r && window.__freeshapeViewport?.preview === null, rev)
  expect(await revisionOnServer(page)).toBeGreaterThan(rev)
})

test('Fillet 1: removing an edge chip and picking another edge with the pen changes the edge list after Accept', async ({ page, request }) => {
  await openApp(page)
  const rev = (await readViewport(page)).revision!
  await editFromMenu(page, 'Fillet 1')
  const field = page.locator('[data-testid^="sel-"]').first()
  const chips = field.getByTestId('sel-chip')
  await expect(chips.first()).toBeVisible()
  const had = (await chips.allTextContents()).length
  // the bracket's stored edge is a legacy reference without an Element name: its chip reads plain "Edge"
  await expect(chips.first()).toHaveText(/^Edge/)
  await field.getByRole('button', { name: /^Remove /, exact: false }).first().click()
  await expect(chips).toHaveCount(had - 1)
  await field.getByRole('button', { name: /Tap, then pick/ }).click()
  await expect(field).toHaveAttribute('data-active', '')
  await page.locator('[data-dialog-base]').waitFor()
  const held = (await chips.allTextContents()).flatMap((t) => /^Edge (\d+) of /.exec(t)?.slice(1).map((n) => Number(n) - 1) ?? [])
  const spot = await edgeSpot(page, request, 'pen', held)
  const before = (await readViewport(page)).preview ?? 0
  await sendPointer(page, [{ phase: 'down', kind: 'pen', id: 7, x: spot.x, y: spot.y }])
  await sendPointer(page, [{ phase: 'up', kind: 'pen', id: 7, x: spot.x, y: spot.y, pressure: 0 }])
  await expect(chips).toHaveCount(had)
  await expect(chips.filter({ hasText: `Edge ${spot.edge + 1} of ` })).toHaveCount(1)
  const listed = (await chips.allTextContents()).sort()
  await page.waitForFunction((n) => (window.__freeshapeViewport?.preview ?? 0) > n, before)
  await expect(page.getByTestId('preview-error')).toHaveCount(0)
  expect(await revisionOnServer(page)).toBe(rev)
  // the form scrolls above the docked pad: scrolled to its end, the last row (Radius) is inside the form and clear of the pad
  const reach = await page.evaluate(() => {
    const form = document.querySelector('.dlg-form') as HTMLElement
    form.scrollTop = form.scrollHeight
    const last = form.lastElementChild!.getBoundingClientRect()
    const f = form.getBoundingClientRect()
    const dock = document.querySelector('[data-region="dock"]')!.getBoundingClientRect()
    return { lastBottom: last.bottom, formBottom: f.bottom, dockTop: dock.top, lastTop: last.top, formTop: f.top }
  })
  expect(reach.lastTop).toBeGreaterThanOrEqual(reach.formTop - 0.5)
  expect(reach.lastBottom).toBeLessThanOrEqual(reach.formBottom + 0.5)
  expect(reach.formBottom).toBeLessThanOrEqual(reach.dockTop + 0.5)
  await header(page, 'Accept').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r && window.__freeshapeViewport?.preview === null, rev)
  expect(await revisionOnServer(page)).toBeGreaterThan(rev)

  // reopened, the stored list is the new one. A chip names its edge in the model the view draws: until the dialog's base
  // has arrived that is the committed model, where the filleted edge has no such name and the chip reads plain "Edge". So
  // wait for the base, then the chips are the ones the dialog showed after the pick, every one a named edge.
  await editFromMenu(page, 'Fillet 1')
  await page.locator('[data-dialog-base]').waitFor()
  const reopened = page.locator('[data-testid^="sel-"]').first().getByTestId('sel-chip')
  await expect.poll(async () => (await reopened.allTextContents()).sort()).toEqual(listed)
  expect(listed.every((t) => /^Edge \d+ of /.test(t))).toBe(true)
  await header(page, 'Cancel').click()
})
