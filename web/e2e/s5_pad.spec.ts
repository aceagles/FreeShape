import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { readViewport } from './support/viewport.ts'

// Slice S5, the numeric and expression pad (card I5.5), in the real client against `freeshape serve`. Nothing opens a pad
// for a user yet (the feature dialogs of I5.6 will), so the spec opens the test-only demo through window.__freeshape.

test.afterEach(async ({ page }) => closeAll(page.request))

const openPad = async (page: Page, opts: { value?: string; variables?: string[] } = {}) => {
  await openApp(page)
  await page.evaluate((o) => window.__freeshape?.openPadDemo(o), opts)
  await expect(page.getByTestId('pad')).toBeVisible()
}
const tap = async (page: Page, ...names: string[]) => {
  for (const n of names) await page.getByTestId('pad').getByRole('button', { name: n, exact: true }).click()
}
const fieldText = (page: Page) => page.getByTestId('pad-field').innerText()

test('every pad key is at least 44 x 44 CSS pixels, and the pad fits the screen', async ({ page }) => {
  await openPad(page)
  const keys = page.locator('[data-testid="pad"] .pad-key')
  expect(await keys.count()).toBe(25)
  for (let i = 0; i < 25; i++) {
    const box = await keys.nth(i).boundingBox()
    expect(box, `key ${i}`).not.toBeNull()
    expect(box!.width, `key ${i} width`).toBeGreaterThanOrEqual(44)
    expect(box!.height, `key ${i} height`).toBeGreaterThanOrEqual(44)
  }
  // with the variable list open too: its rows are touch-sized and the pad stays inside the panel column and the screen
  await page.getByRole('button', { name: 'Variables' }).click()
  const column = (await page.getByTestId('left-column').boundingBox())!
  const vars = page.locator('.pad-var')
  for (let i = 0; i < (await vars.count()); i++) {
    expect((await vars.nth(i).boundingBox())!.height).toBeGreaterThanOrEqual(44)
  }
  const pad = (await page.getByTestId('pad').boundingBox())!
  expect(pad.x).toBeGreaterThanOrEqual(column.x)
  expect(pad.x + pad.width).toBeLessThanOrEqual(column.x + column.width + 0.5)
  expect(pad.y + pad.height).toBeLessThanOrEqual(768)
})

test('taps enter 12.5 mm and Enter commits it once', async ({ page }) => {
  await openPad(page, { value: '' })
  await tap(page, '1', '2', 'Decimal point', '5', 'mm')
  expect(await fieldText(page)).toBe('12.5 mm')
  await tap(page, 'Enter')
  await expect(page.getByTestId('pad')).toHaveCount(0)
  expect(await page.evaluate(() => window.__freeshape?.padResult())).toEqual({ committed: true, text: '12.5 mm' })
})

test('the pad is not a native input, and the # key inserts a variable', async ({ page }) => {
  await openPad(page, { value: '25 mm', variables: ['depth', 'wall'] })
  expect(await page.locator('input, textarea, select, [contenteditable]').count()).toBe(0)
  await page.getByRole('button', { name: 'Variables' }).click()
  await page.getByRole('option', { name: '#wall' }).click()
  await tap(page, 'Multiply', '2')
  expect(await fieldText(page)).toBe('#wall*2')
  await tap(page, 'Cancel')
  expect(await page.evaluate(() => window.__freeshape?.padResult())).toEqual({ committed: false, text: '25 mm' })
})

test('a hardware keyboard types into the same field', async ({ page }) => {
  await openPad(page, { value: '25 mm' })
  await page.keyboard.type('7.5mm')
  expect(await fieldText(page)).toBe('7.5mm')
  await page.keyboard.press('Backspace')
  await page.keyboard.press('Enter')
  expect(await page.evaluate(() => window.__freeshape?.padResult())).toEqual({ committed: true, text: '7.5m' })
})

test('a tap on the pad moves no camera and changes no selection', async ({ page }) => {
  await openPad(page, { value: '25 mm' })
  const before = await readViewport(page)
  const rev = await page.evaluate(() => window.__freeshape!.shell.selection.getState().revision)
  await tap(page, '4', 'Plus', '3', 'Variables', 'Variables')
  const after = await readViewport(page)
  expect(after.camera).toEqual(before.camera)
  expect(after.selection).toEqual(before.selection)
  expect(await page.evaluate(() => window.__freeshape!.shell.selection.getState().revision)).toBe(rev)
})

test('the pad closes with the dialog', async ({ page }) => {
  await openPad(page)
  await page.getByRole('button', { name: 'Close dialog' }).click()
  await expect(page.getByTestId('pad')).toHaveCount(0)
})
