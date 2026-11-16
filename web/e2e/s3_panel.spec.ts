import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { dragRollbackTo, featureMenu, featureRow } from './support/features.ts'
import { partEye, partMenu, partRow } from './support/parts.ts'
import { readViewport } from './support/viewport.ts'

// The feature panel (card I3.2) against `freeshape serve`: each action changes the model in the viewport and the list.
// The viewport hook says what is drawn; the rows say what the list shows. Edits stay in the server's memory (nothing is
// saved) and the next test's openApp closes the Document and throws them away.

test.afterEach(async ({ page }) => closeAll(page.request))

const drawn = async (page: Parameters<typeof readViewport>[0]) => {
  const v = await readViewport(page)
  return { parts: v.parts, faces: v.faces, revision: v.revision }
}
/** Say what the viewport drew (the run's log is the record of what the real server did). */
const say = async (page: Parameters<typeof readViewport>[0], what: string) => console.log(`${what}: ${JSON.stringify(await drawn(page))}`)

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

test('rename changes the row text', async ({ page }) => {
  await openApp(page)
  await featureMenu(page, 'Slot cut', 'Rename')
  const field = page.getByRole('textbox', { name: 'Name' })
  await field.fill('Keyhole')
  await field.press('Enter')
  await expect(featureRow(page, 'Keyhole')).toBeVisible()
  await expect(featureRow(page, 'Slot cut')).toHaveCount(0)
})

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

test('delete (after a confirmation), undo and redo', async ({ page }) => {
  await openApp(page)
  const undo = page.getByRole('button', { name: 'Undo' })
  const redo = page.getByRole('button', { name: 'Redo' })
  await expect(undo).toBeDisabled()
  await expect(redo).toBeDisabled()
  const before = await drawn(page)
  await featureMenu(page, 'Chamfer 1', 'Delete')
  await page.getByRole('dialog', { name: /Delete "Chamfer 1"/ }).getByRole('button', { name: 'Delete' }).click()
  await expect(featureRow(page, 'Chamfer 1')).toHaveCount(0)
  await expect.poll(async () => (await drawn(page)).faces).not.toBe(before.faces)
  await say(page, `deleted Chamfer 1 (was ${JSON.stringify(before)})`)
  await expect(undo).toBeEnabled()
  await undo.click()
  await expect(featureRow(page, 'Chamfer 1')).toBeVisible()
  await expect.poll(async () => (await drawn(page)).faces).toBe(before.faces)
  await say(page, 'undo')
  await expect(redo).toBeEnabled()
  await redo.click()
  await expect(featureRow(page, 'Chamfer 1')).toHaveCount(0)
  await expect.poll(async () => (await drawn(page)).faces).not.toBe(before.faces)
  await say(page, 'redo')
})

test('suppressing a feature another one needs makes that one fail, with its message in the row', async ({ page }) => {
  await openApp(page)
  await featureMenu(page, 'Extrude 1', 'Suppress')
  const failed = page.locator('.panel-row[data-state="failed"]').first()
  await expect(failed).toBeVisible()
  await expect(failed.locator('.row-message')).not.toHaveText('')
  console.log(`failed after suppressing Extrude 1: ${await failed.innerText()} | viewport ${JSON.stringify(await drawn(page))}`)
  const box = await page.getByTestId('left-column').boundingBox()
  const row = await failed.boundingBox()
  expect((row?.x ?? 0) + (row?.width ?? 0)).toBeLessThanOrEqual((box?.x ?? 0) + (box?.width ?? 0))
})

// The Parts list (card I3.3): the Hinge's Part Studio `hinge_parts` has several parts. Every number below comes from the
// viewport hook (window.__freeshapeViewport), read after the edit's outputs have been drawn.
const seen = async (page: Parameters<typeof readViewport>[0]) => {
  const v = await readViewport(page)
  return { revision: v.revision, parts: v.parts, visibleParts: v.visibleParts, hiddenParts: v.hiddenParts, ids: v.visiblePartIds, faces: v.faces, planeHalf: v.planeHalf, highlighted: v.highlightedPart }
}

test('the eye hides a part from the view and shows it again; the visible count follows', async ({ page }) => {
  await openApp(page, { document: 'Hinge' })
  const names = await page.locator('.part-select[data-part] .row-name').allTextContents()
  expect(names.length).toBeGreaterThan(1)
  await expect(page.getByText(`Parts (${names.length})`)).toBeVisible()
  const before = await seen(page)
  expect(before.visibleParts).toBe(names.length)
  const edits: string[] = []
  page.on('request', (r) => r.url().endsWith('/edit') && edits.push(r.postData() ?? ''))
  await partEye(page, names[0]!).click()
  await expect.poll(async () => (await seen(page)).visibleParts).toBe(names.length - 1)
  const hidden = await seen(page)
  console.log(`hide ${names[0]}: ${JSON.stringify(before)} -> ${JSON.stringify(hidden)}`)
  expect(hidden.hiddenParts).toBe(1)
  expect(hidden.faces).toBeLessThan(before.faces)
  expect(hidden.ids).not.toContain(before.ids[0])
  expect(JSON.parse(edits[0]!)).toMatchObject({ op: 'set_part_override', hidden: true })
  await expect(page.locator('[data-part-row][data-hidden]')).toHaveCount(1)
  await partEye(page, names[0]!).click()
  await expect.poll(async () => (await seen(page)).visibleParts).toBe(names.length)
  const shown = await seen(page)
  console.log(`show ${names[0]}: ${JSON.stringify(shown)}`)
  expect(shown.faces).toBe(before.faces)
  expect(shown.hiddenParts).toBe(0)
})

test('rename shows in the list from the next outputs; Colour changes the part in the view; Default colour puts it back', async ({ page }) => {
  await openApp(page, { document: 'Hinge' })
  const first = (await page.locator('.part-select[data-part] .row-name').first().textContent())!
  const before = await seen(page)
  const id = before.ids[0]!
  const colorBefore = (await readViewport(page)).partColors[id]!
  await partMenu(page, first, 'Rename')
  const field = page.getByRole('textbox', { name: 'Name' })
  await field.fill('Leaf A')
  await field.press('Enter')
  await expect(partRow(page, 'Leaf A')).toBeVisible()
  await expect(partRow(page, new RegExp(`^${first}$`))).toHaveCount(0)
  console.log(`rename ${first} -> Leaf A: list shows ${await page.locator('.part-select[data-part] .row-name').allTextContents()}`)
  await partMenu(page, 'Leaf A', 'Colour')
  await page.getByRole('button', { name: 'Dusty rose' }).click()
  await expect.poll(async () => (await readViewport(page)).partColors[id]).toEqual([0.84, 0.58, 0.68])
  console.log(`colour ${id}: ${JSON.stringify(colorBefore)} -> ${JSON.stringify((await readViewport(page)).partColors[id])}`)
  await partMenu(page, 'Leaf A', 'Colour')
  await page.getByRole('button', { name: 'Default colour' }).click()
  // the colour the part was built with (the Hinge's base plate carries a saved override, which Default drops)
  await expect.poll(async () => (await readViewport(page)).partColors[id]).toEqual([0.6, 0.74, 0.85])
  console.log(`default colour ${id}: ${JSON.stringify((await readViewport(page)).partColors[id])} (the saved override was ${JSON.stringify(colorBefore)})`)
})

test('selecting a part highlights it in the view, and selecting a feature takes the highlight away', async ({ page }) => {
  await openApp(page, { document: 'Hinge' })
  const before = await seen(page)
  expect(before.highlighted).toBeNull()
  await partRow(page, new RegExp('.')).first().click()
  await expect.poll(async () => (await seen(page)).highlighted).toBe(before.ids[0])
  console.log(`selected part: highlighted ${(await seen(page)).highlighted}`)
  await page.locator('.panel-row[data-feature]').first().click()
  await expect.poll(async () => (await seen(page)).highlighted).toBeNull()
  await expect(page.locator('.part-select[aria-current="true"]')).toHaveCount(0)
})

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
