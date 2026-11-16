import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { dragRollbackTo, featureMenu, featureRow } from './support/features.ts'
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
