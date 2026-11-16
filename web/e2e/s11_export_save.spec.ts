import { readdirSync, readFileSync, rmSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { partRow } from './support/parts.ts'

// Slice S11, card I11.14: save an export into a server folder from the Export dialog, in both modes. The server is started
// with `--export-root out=<a temporary folder>` (FS_EXPORT_DIR); a run whose server has no export root skips this file.
const folder = process.env.FS_EXPORT_DIR ?? ''
test.skip(folder === '', 'the server has no export root: start it with --export-root out=<folder> and set FS_EXPORT_DIR')

test.afterEach(async ({ page }) => {
  await closeAll(page.request)
  if (folder !== '') for (const f of readdirSync(folder)) rmSync(join(folder, f), { force: true })
})

const dialog = (page: Page) => page.getByRole('dialog', { name: 'Export' })
const files = (): string[] => readdirSync(folder).filter((f) => !f.startsWith('.freeshape-export-'))

/** A binary STL: an 80 byte header, a triangle count, and 50 bytes for each triangle, and nothing more. */
function expectBinaryStl(file: Buffer): void {
  expect(file.length).toBeGreaterThan(84)
  const triangles = file.readUInt32LE(80)
  expect(triangles).toBeGreaterThan(0)
  expect(file.length).toBe(84 + 50 * triangles)
}

/** The folder holds one non-empty binary STL: its name. */
function theSavedStl(): string {
  const names = files()
  expect(names).toHaveLength(1)
  expect(names[0]).toMatch(/\.stl$/)
  expect(statSync(join(folder, names[0]!)).size).toBeGreaterThan(0)
  expectBinaryStl(readFileSync(join(folder, names[0]!)))
  return names[0]!
}

async function saveTwice(page: Page, save: () => Promise<void>): Promise<void> {
  let downloads = 0
  page.on('download', () => (downloads += 1))
  await save()
  await expect(page.getByTestId('export-saved').locator('span')).toHaveText(/^Saved .+\.stl to out$/)
  const name = theSavedStl()
  await expect(page.getByTestId('export-saved')).toContainText(`Saved ${name} to out`)
  expect(downloads).toBe(0)
  // the same name again asks before it replaces the file, and Replace leaves one file
  await page.getByTestId('export-saved').getByRole('button', { name: 'Dismiss' }).click()
  await save()
  const ask = page.getByRole('dialog', { name: 'Replace file?' })
  await expect(ask).toContainText(`Replace ${name} in out?`)
  await ask.getByRole('button', { name: 'Replace' }).click()
  await expect(page.getByTestId('export-saved')).toContainText(`Saved ${name} to out`)
  expect(theSavedStl()).toBe(name)
  expect(downloads).toBe(0)
}

test.describe('Tablet mode', () => {
  test('the bracket\'s part as STL into the folder, then again with Replace', async ({ page }) => {
    await openApp(page)
    await partRow(page, /./).first().click()
    await saveTwice(page, async () => {
      await page.getByRole('button', { name: 'Export', exact: true }).click()
      await dialog(page).getByRole('radio', { name: 'STL' }).click()
      await dialog(page).getByRole('radio', { name: 'Save to out' }).click()
      await dialog(page).getByRole('button', { name: 'Save to out', exact: true }).click()
    })
  })
})

test.describe('Desktop mode', () => {
  test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })

  test('the bracket\'s part as STL into the folder, then again with Replace', async ({ page }) => {
    await openApp(page)
    await partRow(page, /./).first().click()
    await saveTwice(page, async () => {
      await page.locator('[data-region="top-bar"]').getByRole('button', { name: 'Document', exact: true }).click()
      await page.getByRole('menuitem', { name: 'Export STL…' }).click()
      if ((await dialog(page).getByRole('button', { name: /^Destination, Save to out/ }).count()) === 0) {
        await dialog(page).getByRole('button', { name: /^Destination/ }).click()
        await page.getByRole('menuitemcheckbox', { name: 'Save to out' }).click()
      }
      await dialog(page).getByRole('button', { name: 'Save to out', exact: true }).click()
    })
  })
})
