import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { partRow } from './support/parts.ts'

// Slice S11, card I11.8: a file from the device, in both modes, against the real import route (no mock: a device upload needs no
// file root). The picker is `setInputFiles` on the explorer's file input; a drop is a drop event with a File on the page. The
// progress bar only exists while the bytes go out, so it is covered by the vitest with a fake XMLHttpRequest: here every check
// is on the state the upload ends in, polled.
test.afterEach(async ({ page }) => closeAll(page.request))

const BOX = resolve(import.meta.dirname, 'fixtures/box.step')
const explorer = (page: Page) => page.getByRole('dialog', { name: 'Files' })
const partCount = (page: Page) => partRow(page, /./).count()
const input = (page: Page): Locator => explorer(page).getByTestId('ex-device-input')
const status = (page: Page) => explorer(page).getByTestId('ex-device-status')
const currentTab = (page: Page) => page.locator('nav[aria-label="Tabs"] .tab-button[aria-current="page"]')

/** A drop of a file on `selector`: a drop event whose DataTransfer holds it, as a browser makes one. Returns whether the page took the dragover (the browser would otherwise open the file). */
async function dropFile(page: Page, selector: string, file: { name: string; bytes: Buffer }): Promise<boolean> {
  return page.evaluate(
    ({ selector, name, b64 }) => {
      const el = document.querySelector(selector)!
      const dt = new DataTransfer()
      dt.items.add(new File([Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))], name))
      const over = new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true })
      el.dispatchEvent(over)
      el.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }))
      return over.defaultPrevented
    },
    { selector, name: file.name, b64: file.bytes.toString('base64') },
  )
}
const box = (name = 'box.step') => ({ name, bytes: readFileSync(BOX) })

async function openImport(page: Page): Promise<void> {
  await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
  await page.getByRole('menuitem', { name: /^Import/ }).click()
  await expect(explorer(page)).toBeVisible()
}

test.describe('Tablet mode', () => {
  test('From this device… adds the picked STEP to the bracket\'s Part Studio', async ({ page }) => {
    await openApp(page)
    await expect.poll(() => partCount(page)).toBeGreaterThan(0)
    const before = await partCount(page)
    await openImport(page)
    await expect(explorer(page).getByRole('button', { name: 'From this device…', exact: true })).toBeEnabled()
    await input(page).setInputFiles(BOX)
    await expect(status(page)).toHaveText('Imported box.step')
    await expect(page.getByTestId('upload-progress')).toHaveCount(0)
    await expect.poll(() => partCount(page)).toBe(before + 1)
  })

  test('the second button makes a new Part Studio named after the file and shows it', async ({ page }) => {
    await openApp(page)
    await openImport(page)
    await explorer(page).getByRole('button', { name: 'From this device into a new Part Studio…' }).click()
    await input(page).setInputFiles(BOX)
    await expect(status(page)).toHaveText('Imported box.step')
    await expect(currentTab(page)).toHaveText('box')
    await expect.poll(() => partCount(page)).toBe(1)
  })

  test("a file that is not STEP shows the server's reason and changes nothing", async ({ page }) => {
    await openApp(page)
    await expect.poll(() => partCount(page)).toBeGreaterThan(0)
    const before = await partCount(page)
    await openImport(page)
    await input(page).setInputFiles({ name: 'notes.step', mimeType: 'application/octet-stream', buffer: Buffer.from('not a step file') })
    await expect(status(page)).toContainText('the body is not a STEP')
    expect(await partCount(page)).toBe(before)
  })

  test('a file dropped on the view imports nothing and says to use From this device…', async ({ page }) => {
    await openApp(page)
    const taken = await dropFile(page, '[data-testid="shell-viewport"]', box())
    expect(taken).toBe(true)
    await expect(page.getByRole('dialog', { name: 'Import' })).toContainText('Dropping files works in Desktop mode')
    await expect(page.locator('.tab-button')).toHaveCount(1)
  })
})

test.describe('Desktop mode', () => {
  test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })

  test('From this device… (Ctrl+Shift+L) adds the picked STEP to the bracket\'s Part Studio', async ({ page }) => {
    await openApp(page)
    await expect.poll(() => partCount(page)).toBeGreaterThan(0)
    const before = await partCount(page)
    await page.keyboard.press('Control+Shift+L')
    await expect(explorer(page)).toBeVisible()
    await input(page).setInputFiles(BOX)
    await expect(status(page)).toHaveText('Imported box.step')
    await expect.poll(() => partCount(page)).toBe(before + 1)
  })

  test('a STEP dropped on the view opens in a new Part Studio tab named after it', async ({ page }) => {
    await openApp(page)
    const tabs = await page.locator('.tab-button').count()
    expect(await dropFile(page, '[data-testid="shell-viewport"]', box())).toBe(true)
    await expect(currentTab(page)).toHaveText('box')
    await expect(page.locator('.tab-button')).toHaveCount(tabs + 1)
    await expect.poll(() => partCount(page)).toBe(1)
    await expect(page.getByTestId('upload-progress')).toHaveCount(0)
  })

  test('a .png dropped on the view does nothing but say why', async ({ page }) => {
    await openApp(page)
    const tabs = await page.locator('.tab-button').count()
    expect(await dropFile(page, '[data-testid="shell-viewport"]', { name: 'photo.png', bytes: Buffer.from('x') })).toBe(true)
    await expect(page.getByRole('dialog', { name: 'Import' })).toContainText('“photo.png” is not a STEP or BREP file')
    await expect(page.locator('.tab-button')).toHaveCount(tabs)
  })

  test('a STEP dropped off the view is kept from the browser, imports nothing and says where to drop', async ({ page }) => {
    await openApp(page)
    const tabs = await page.locator('.tab-button').count()
    expect(await dropFile(page, '[data-testid="shell"]', box())).toBe(true)
    await expect(page.getByRole('dialog', { name: 'Import' })).toContainText('Drop a STEP or BREP file on the view')
    await expect(page.locator('.tab-button')).toHaveCount(tabs)
  })
})
