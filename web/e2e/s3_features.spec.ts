import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { dragRollbackTo, featureMenu, featureRow } from './support/features.ts'
import { partEye } from './support/parts.ts'
import { drawn, readViewport, say, seen } from './support/viewport.ts'

// The gate of slice S3 (card I3.4): the feature list against `freeshape serve`, on the Bracket. The viewport hook says
// what is drawn; the rows say what the list shows. Edits stay in the server's memory (nothing is saved) and the next
// test's openApp closes the Document and throws them away. The panel's other actions (rename, delete, failed rows) and
// the Parts list are in s3_panel.spec.ts; the three flows below were there and live here now, so each runs once.

test.afterEach(async ({ page }) => closeAll(page.request))

const BRACKET_FEATURES = ['Bracket profile', 'Extrude 1', 'Fillet 1', 'Hole positions', 'Hole 1', 'Slot', 'Slot cut', 'Chamfer 1']

test('the panel lists the fixture\'s features; suppressing the fillet from its menu changes the face count, undo restores it', async ({ page }) => {
  await openApp(page)
  expect(await page.locator('.panel-row[data-feature] .row-name').allTextContents()).toEqual(BRACKET_FEATURES)
  const before = await drawn(page)
  const edits: string[] = []
  page.on('request', (r) => r.url().endsWith('/edit') && edits.push(r.postData() ?? ''))
  await featureMenu(page, 'Fillet 1', 'Suppress')
  await expect(featureRow(page, 'Fillet 1')).toHaveAttribute('data-state', 'suppressed')
  await expect.poll(async () => (await drawn(page)).faces).not.toBe(before.faces)
  await say(page, `suppress Fillet 1 (was ${JSON.stringify(before)})`)
  expect(JSON.parse(edits[0]!)).toMatchObject({ op: 'set_suppressed', value: true })
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(featureRow(page, 'Fillet 1')).not.toHaveAttribute('data-state', 'suppressed')
  await expect.poll(async () => (await drawn(page)).faces).toBe(before.faces)
  await say(page, 'undo')
})

// Moved from s3_panel.spec.ts, every assertion kept: Slot cut suppressed and unsuppressed from its menu.
test('suppress and unsuppress change the model and the row', async ({ page }) => {
  await openApp(page)
  const before = await drawn(page)
  await featureMenu(page, 'Slot cut', 'Suppress')
  await expect(featureRow(page, 'Slot cut')).toHaveAttribute('data-state', 'suppressed')
  await expect.poll(async () => (await drawn(page)).faces).not.toBe(before.faces)
  await say(page, `suppress Slot cut (was ${JSON.stringify(before)})`)
  const gone = await drawn(page)
  expect(gone.revision).toBeGreaterThan(before.revision ?? 0)
  await featureMenu(page, 'Slot cut', 'Unsuppress')
  await expect(featureRow(page, 'Slot cut')).not.toHaveAttribute('data-state', 'suppressed')
  await expect.poll(async () => (await drawn(page)).faces).toBe(before.faces)
  await say(page, 'unsuppress Slot cut')
})

// Moved from s3_panel.spec.ts, every assertion kept: the mouse drag, above Slot.
test('dragging the Drag handle rolls the model back and forward in one edit each', async ({ page }) => {
  await openApp(page)
  const before = await drawn(page)
  const edits: string[] = []
  page.on('request', (r) => r.url().endsWith('/edit') && edits.push(r.postData() ?? ''))
  await dragRollbackTo(page, /^Slot$/)
  await expect(page.locator('.panel-row[data-state="rolled-back"]')).toHaveCount(3) // Slot, Slot cut, Chamfer 1
  await expect.poll(async () => (await drawn(page)).faces).not.toBe(before.faces)
  await say(page, `rolled back above Slot (was ${JSON.stringify(before)})`)
  expect(edits.map((e) => JSON.parse(e))).toEqual([{ op: 'set_rollback', index: 5 }])
  await dragRollbackTo(page, null)
  await expect(page.locator('.panel-row[data-state="rolled-back"]')).toHaveCount(0)
  await expect.poll(async () => (await drawn(page)).faces).toBe(before.faces)
  await say(page, 'rolled forward again')
  expect(edits.length).toBe(2)
})

// The owner uses a finger and a Pencil: the handle driven by PointerEvents of those types, above the last feature.
for (const by of ['touch', 'pen'] as const) {
  test(`a ${by} drag of the Drag handle above the last feature greys it and changes the face count; dragging back restores it`, async ({ page }) => {
    await openApp(page)
    const before = await drawn(page)
    const edits: string[] = []
    page.on('request', (r) => r.url().endsWith('/edit') && edits.push(r.postData() ?? ''))
    await dragRollbackTo(page, /^Chamfer 1$/, by)
    await expect(page.locator('.panel-row[data-state="rolled-back"]')).toHaveCount(1)
    await expect(featureRow(page, 'Chamfer 1')).toHaveAttribute('data-state', 'rolled-back')
    await expect.poll(async () => (await drawn(page)).faces).not.toBe(before.faces)
    await say(page, `${by}: rolled back above Chamfer 1 (was ${JSON.stringify(before)})`)
    await expect(page.getByTestId('rollback-bar')).not.toHaveAttribute('data-dragging', '')
    await dragRollbackTo(page, null, by)
    await expect(page.locator('.panel-row[data-state="rolled-back"]')).toHaveCount(0)
    await expect.poll(async () => (await drawn(page)).faces).toBe(before.faces)
    await say(page, `${by}: rolled forward again`)
    expect(edits.map((e) => JSON.parse(e))).toEqual([
      { op: 'set_rollback', index: 7 },
      { op: 'set_rollback', index: 8 },
    ])
  })
}

// Moved from s3_panel.spec.ts, every assertion kept: the Bracket's only part hidden by its eye, the hook reports no
// visible part, and the default planes keep their size.
test('with every part hidden or failed the default planes keep their size instead of jumping', async ({ page }) => {
  await openApp(page)
  const before = await seen(page)
  expect(before.planeHalf).toBeGreaterThan(0)
  await partEye(page, 'Bracket').click()
  await expect.poll(async () => (await seen(page)).visibleParts).toBe(0)
  const hidden = await seen(page)
  console.log(`all hidden: planeHalf ${before.planeHalf} -> ${hidden.planeHalf}`)
  expect(hidden.planeHalf).toBe(before.planeHalf)
  await partEye(page, 'Bracket').click()
  await expect.poll(async () => (await seen(page)).visibleParts).toBe(1)
  await featureMenu(page, 'Extrude 1', 'Suppress')
  await expect.poll(async () => (await seen(page)).visibleParts).toBe(0)
  const failed = await seen(page)
  console.log(`every feature failed: planeHalf ${before.planeHalf} -> ${failed.planeHalf}`)
  expect(failed.planeHalf).toBe(before.planeHalf)
})

// The view shows only the tab on screen: an Assembly has a view of its own (card I8.1), and a new
// empty Part Studio shows its own default planes, never another tab's parts.
test('another kind of tab or a new Part Studio never shows the previous tab\'s parts; switching back draws them again', async ({ page }) => {
  await openApp(page, { document: 'Hinge' })
  const before = await seen(page)
  expect(before.parts).toBe(3)
  await page.locator('.tab-button[data-kind="assembly"]').click()
  // the Assembly tab has its own view and panel (card I8.1): the Part Studio's view is gone, so none of its parts is drawn
  await expect.poll(() => page.evaluate(() => window.__freeshapeViewport === undefined)).toBe(true)
  await expect(page.getByTestId('viewport-empty')).toHaveCount(0)
  await expect(page.getByTestId('feature-panel')).toHaveCount(0)
  await expect(page.getByTestId('assembly-panel')).toBeVisible()
  await page.locator('.tab-button[data-kind="studio"]').first().click()
  await expect.poll(async () => (await seen(page)).parts).toBe(3)
  const back = await readViewport(page)
  expect([back.faces, back.emptyKind]).toEqual([before.faces, null])
  await expect(page.getByTestId('viewport-empty')).toHaveCount(0)
  await expect(page.getByText('Parts (3)')).toBeVisible()
  await page.getByRole('button', { name: 'Add tab' }).click()
  await page.getByRole('menuitem', { name: 'Part Studio' }).click()
  await expect.poll(async () => (await readViewport(page)).parts).toBe(0)
  await expect(page.locator('.tab-button[aria-current="page"]')).toHaveCount(1)
  await expect.poll(async () => (await readViewport(page)).planes).toBeGreaterThanOrEqual(3)
  const fresh = await readViewport(page)
  console.log(`new Part Studio: parts ${fresh.parts} planes ${fresh.planes} emptyKind ${fresh.emptyKind}`)
  expect([fresh.faces, fresh.emptyKind]).toEqual([0, null])
  await expect(page.getByTestId('viewport-empty')).toHaveCount(0)
  await expect(page.getByText('Parts (0)')).toBeVisible()
})
