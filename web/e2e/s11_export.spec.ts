import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import type { Download, Page } from '@playwright/test'
import { openAssemblyTab } from './support/assembly.ts'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { partRow } from './support/parts.ts'

// Slice S11, card I11.9: export STEP and STL from the client, in both modes, against `freeshape serve`. Each export is a
// download the browser saves: a file with the tab's name, not empty; the STL is binary.
test.afterEach(async ({ page }) => closeAll(page.request))

const dialog = (page: Page) => page.getByRole('dialog', { name: 'Export' })

/** Press Export in the dialog and take the download it makes. */
async function exportNow(page: Page): Promise<Download> {
  const download = page.waitForEvent('download')
  await dialog(page).getByRole('button', { name: 'Export', exact: true }).click()
  return download
}
const bytesOf = (d: Download, path: string | null): Buffer => {
  if (path === null) throw new Error(`download ${d.suggestedFilename()} has no file`)
  return readFileSync(path)
}
/** A binary STL: an 80 byte header, a triangle count, and 50 bytes for each triangle, and nothing more. */
function expectBinaryStl(file: Buffer): void {
  expect(file.length).toBeGreaterThan(84)
  const triangles = file.readUInt32LE(80)
  expect(triangles).toBeGreaterThan(0)
  expect(file.length).toBe(84 + 50 * triangles)
}
function expectStep(file: Buffer): void {
  expect(file.length).toBeGreaterThan(100)
  expect(file.subarray(0, 13).toString('latin1')).toBe('ISO-10303-21;')
}

test.describe('Tablet mode', () => {
  test('the bracket\'s part as a binary STL, from the top bar', async ({ page }) => {
    await openApp(page)
    const tabName = ((await page.locator('.tab-button').first().textContent()) ?? '').trim()
    await partRow(page, /./).first().click()
    await page.getByRole('button', { name: 'Export', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Export STEP / STL…' }).click()
    await expect(dialog(page)).toBeVisible()
    await expect(dialog(page).locator('xpath=ancestor::*[@data-region="left-column"]')).toHaveCount(1)
    await dialog(page).getByRole('radio', { name: 'STL' }).click()
    await expect(dialog(page).getByRole('radio', { name: 'Fine' })).toHaveAttribute('aria-checked', 'true')
    await expect(dialog(page).getByRole('radio', { name: 'Selected (1)' })).toHaveAttribute('aria-checked', 'true')
    const d = await exportNow(page)
    expect(d.suggestedFilename()).toBe(`${tabName}.stl`)
    expectBinaryStl(bytesOf(d, await d.path()))
    await expect(dialog(page)).toHaveCount(0)
  })

  test('the hinge Assembly as STEP, from the top bar, with no parts to choose', async ({ page }) => {
    await openApp(page, { document: 'Hinge', model: false })
    await openAssemblyTab(page, 3)
    const tabName = ((await page.locator('.tab-button[data-kind="assembly"]').textContent()) ?? '').trim()
    await page.getByRole('button', { name: 'Export', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Export STEP / STL…' }).click()
    await expect(dialog(page).getByRole('radio', { name: 'STEP' })).toHaveAttribute('aria-checked', 'true')
    await expect(dialog(page).getByText(/Parts/)).toHaveCount(0)
    const d = await exportNow(page)
    expect(d.suggestedFilename()).toBe(`${tabName}.step`)
    expectStep(bytesOf(d, await d.path()))
  })
})

test.describe('Desktop mode', () => {
  test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })

  test('the bracket as STL from the Document menu, with the choices kept for the next export', async ({ page }) => {
    await openApp(page)
    const tabName = ((await page.locator('.tab-strip .tab-button').first().textContent()) ?? '').trim()
    await page.locator('[data-region="top-bar"]').getByRole('button', { name: 'Document', exact: true }).click()
    await page.getByRole('menuitem', { name: 'Export STL…' }).click()
    await expect(dialog(page)).toBeVisible()
    await dialog(page).getByRole('button', { name: /^Encoding, Binary/ }).click()
    await page.getByRole('menuitemcheckbox', { name: 'ASCII' }).click()
    await dialog(page).getByRole('button', { name: /^Encoding, ASCII/ }).click()
    await page.getByRole('menuitemcheckbox', { name: 'Binary' }).click()
    const d = await exportNow(page)
    expect(d.suggestedFilename()).toBe(`${tabName}.stl`)
    expectBinaryStl(bytesOf(d, await d.path()))
    // the next dialog opens on what was chosen: the toolbar's Export names no format, so it is the STL used last
    await page.locator('.desktop-toolbar [data-tool="export"]').click()
    await expect(dialog(page).getByRole('button', { name: /^Format, STL/ })).toBeVisible()
    await dialog(page).getByRole('button', { name: 'Cancel', exact: true }).click()
    await expect(dialog(page)).toHaveCount(0)
  })

  test('the hinge Assembly as STEP, from the Assembly toolbar', async ({ page }) => {
    await openApp(page, { document: 'Hinge', model: false })
    await openAssemblyTab(page, 3)
    await page.locator('[data-strip="assembly"] [data-tool="export"]').click()
    await expect(dialog(page).getByRole('button', { name: /^Format, STEP/ })).toBeVisible()
    const d = await exportNow(page)
    expect(d.suggestedFilename()).toMatch(/\.step$/)
    expectStep(bytesOf(d, await d.path()))
  })
})
