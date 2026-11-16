import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { featureMenu, featureRow } from './support/features.ts'
import { partEye, partMenu, partRow } from './support/parts.ts'
import { drawn, readViewport, say, seen } from './support/viewport.ts'

// The feature panel (card I3.2) against `freeshape serve`: each action changes the model in the viewport and the list.
// The viewport hook says what is drawn; the rows say what the list shows. Edits stay in the server's memory (nothing is
// saved) and the next test's openApp closes the Document and throws them away.

test.afterEach(async ({ page }) => closeAll(page.request))

test('rename changes the row text', async ({ page }) => {
  await openApp(page)
  await featureMenu(page, 'Slot cut', 'Rename')
  const field = page.getByRole('textbox', { name: 'Name' })
  await field.fill('Keyhole')
  await field.press('Enter')
  await expect(featureRow(page, 'Keyhole')).toBeVisible()
  await expect(featureRow(page, 'Slot cut')).toHaveCount(0)
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
