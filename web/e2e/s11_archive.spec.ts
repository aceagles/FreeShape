import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import type { Download, Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, listRows, removeDocument } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { partRow } from './support/parts.ts'

// Slice S11, card I11.10: a Document as one .fsdoc, in both modes, against `freeshape serve` on a Documents root and the real
// archive routes (no mock). The bracket is downloaded (from the home screen's row menu, and from inside the Document), the file is
// checked to be a zip whose first entry is the marker, and it is uploaded back with Open file… / Open Document archive…, which makes
// a second Document that opens with the same part. The upload's folder is named by `?name=` (COPY), so it can be removed again.
const COPY = 'Archive spec copy'
test.beforeEach(() => removeDocument(COPY))
test.afterEach(async ({ page }) => {
  await closeAll(page.request)
  removeDocument(COPY)
})

const partNames = (page: Page): Promise<string[]> => partRow(page, /./).allTextContents()
const input = (page: Page) => page.getByTestId('archive-input')

/** The file of a download, checked: a non-empty zip named Bracket.fsdoc whose first entry is the marker `freeshape-archive.json`. */
async function expectArchive(d: Download): Promise<Buffer> {
  expect(d.suggestedFilename()).toBe('Bracket.fsdoc')
  const path = await d.path()
  if (path === null) throw new Error('the download has no file')
  const file = readFileSync(path)
  expect(file.length).toBeGreaterThan(100)
  // a zip: the first local file header, then the length of the first entry's name, then the name
  expect(file.subarray(0, 4).toString('latin1')).toBe('PK\x03\x04')
  const nameLength = file.readUInt16LE(26)
  expect(file.subarray(30, 30 + nameLength).toString('utf8')).toBe('freeshape-archive.json')
  return file
}

/** The home screen with the bracket's row menu open, and Download (.fsdoc) taken. */
async function downloadFromHome(page: Page): Promise<Download> {
  await closeAll(page.request)
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()
  const row = page.locator('li.doc-item', { has: page.locator('.doc-name', { hasText: /^Bracket$/ }) })
  await row.getByRole('button', { name: 'Document menu' }).click()
  const download = page.waitForEvent('download')
  await page.getByRole('menuitem', { name: 'Download (.fsdoc)' }).click()
  return download
}

/** Open file… on the home screen with the archive's bytes, under the name COPY; the new Document opens. */
async function openFileFromHome(page: Page, file: Buffer): Promise<void> {
  await expect(page.getByRole('button', { name: 'Open file…' })).toBeVisible()
  await input(page).setInputFiles({ name: `${COPY}.fsdoc`, mimeType: 'application/octet-stream', buffer: file })
  await expect(page.getByTestId('shell')).toBeVisible({ timeout: 30_000 })
}

/** The Documents the server lists called Bracket: the original and, once uploaded, its copy. */
const bracketRows = async (page: Page) => (await listRows(page.request)).filter((r) => r.name === 'Bracket')

async function homeFlow(page: Page): Promise<void> {
  // the part the bracket shows when it is opened, for comparison
  await openApp(page)
  await expect.poll(() => partNames(page)).not.toEqual([])
  const original = await partNames(page)

  const file = await expectArchive(await downloadFromHome(page))
  expect(await bracketRows(page)).toHaveLength(1)

  await openFileFromHome(page, file)
  // the new Document has the bracket's name (the archive keeps it) and the same part
  await expect(page.getByTestId('document-name')).toHaveText('Bracket')
  await expect.poll(() => partNames(page)).toEqual(original)
  expect(await bracketRows(page)).toHaveLength(2)

  // back on the home screen the two Documents are listed under one name
  await closeAll(page.request)
  await page.goto('/')
  await expect(page.locator('.doc-row .doc-name', { hasText: /^Bracket$/ })).toHaveCount(2)
}

test.describe('Tablet mode', () => {
  test("download the bracket from its row menu, open the file with Open file…, and the new Document shows the same part", async ({ page }) => {
    await homeFlow(page)
  })

  test('Download Document in the Export menu gives the same file, and Open file… takes it back', async ({ page }) => {
    await openApp(page)
    await expect.poll(() => partNames(page)).not.toEqual([])
    const original = await partNames(page)
    await page.getByRole('button', { name: 'Export', exact: true }).click()
    const download = page.waitForEvent('download')
    await page.getByRole('menuitem', { name: 'Download Document' }).click()
    const file = await expectArchive(await download)

    await page.getByRole('button', { name: 'Back to Documents' }).click()
    await openFileFromHome(page, file)
    await expect.poll(() => partNames(page)).toEqual(original)
    expect(await bracketRows(page)).toHaveLength(2)
  })

  test("a file that is not an archive shows the server's reason and adds no Document", async ({ page }) => {
    await closeAll(page.request)
    await page.goto('/')
    const before = (await listRows(page.request)).length
    await expect(page.getByRole('button', { name: 'Open file…' })).toBeVisible()
    await input(page).setInputFiles({ name: `${COPY}.fsdoc`, mimeType: 'application/octet-stream', buffer: Buffer.from('this is not a zip') })
    const dialog = page.getByRole('dialog', { name: `Could not open "${COPY}.fsdoc"` })
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('not a Document archive')
    await dialog.getByRole('button', { name: 'OK' }).click()
    await expect(dialog).toHaveCount(0)
    expect((await listRows(page.request)).length).toBe(before)
    await expect(page.getByTestId('home')).toBeVisible()
  })
})

test.describe('Desktop mode', () => {
  test.use(desktopMode)

  test('download the bracket from its row menu, open the file with Open file…, and the new Document shows the same part', async ({ page }) => {
    await homeFlow(page)
  })

  test('Save Document archive… and Open Document archive… in the Document menu', async ({ page }) => {
    await openApp(page)
    await expect.poll(() => partNames(page)).not.toEqual([])
    const original = await partNames(page)
    const originalId = (await bracketRows(page))[0]!.id
    await page.getByRole('button', { name: 'Document', exact: true }).click()
    const download = page.waitForEvent('download')
    await page.getByRole('menuitem', { name: 'Save Document archive…' }).click()
    const file = await expectArchive(await download)

    // Open Document archive… waits for the probe that tells it the server holds a Documents root
    await expect(page.getByRole('button', { name: 'Document', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Document', exact: true }).click()
    await expect(page.getByRole('menuitem', { name: 'Open Document archive…' })).toBeEnabled()
    const chooser = page.waitForEvent('filechooser')
    await page.getByRole('menuitem', { name: 'Open Document archive…' }).click()
    await (await chooser).setFiles({ name: `${COPY}.fsdoc`, mimeType: 'application/octet-stream', buffer: file })
    // it leaves the bracket (nothing unsaved) and opens the copy, which has the bracket's name and part
    await expect.poll(async () => (await bracketRows(page)).length).toBe(2)
    await expect
      .poll(async () => (await bracketRows(page)).filter((r) => r.open).map((r) => r.id !== originalId))
      .toEqual([true])
    await expect(page.getByTestId('shell')).toBeVisible({ timeout: 30_000 })
    await expect.poll(() => partNames(page)).toEqual(original)
  })
})
