import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { mockLibrary } from './support/library.ts'
import { desktopMode } from './support/mode.ts'

// Slice S11, card I11.11: a STEP into an Assembly, in both modes, in a new Assembly of the hinge Document. The device paths run
// against the real import route (the picker is a real file chooser; a drop is a drop event with a File): no mock. The explorer's
// listing is answered by support/library.ts, because the e2e server has no file root; its import call is turned into the upload
// the real route takes. So the JSON body {root, entry} is checked as the page sent it (the library's log) and by the vitest, and
// the real route is exercised with the same file's bytes; the {root, entry} branch of the route is covered by
// tests/test_assembly_insert_step_served.py. Every check polls the page's state.
test.afterEach(async ({ page }) => closeAll(page.request))

const PAIR = resolve(import.meta.dirname, 'fixtures/two_boxes.step')
const rows = (page: Page) => page.locator('[data-instance-row]')
const explorer = (page: Page) => page.getByRole('dialog', { name: 'Files' })
const insertPanel = (page: Page) => page.getByTestId('insert-panel')

async function newAssembly(page: Page, menuLabel: RegExp): Promise<void> {
  await openApp(page, { document: 'Hinge', model: false })
  await page.getByRole('button', { name: 'Add tab' }).click()
  await page.getByRole('menuitem', { name: menuLabel }).click()
  await expect(page.getByTestId('assembly-panel')).toBeVisible()
  await expect(page.locator('.tab-button[aria-current="page"]')).toHaveAttribute('data-kind', 'assembly')
  await expect(rows(page)).toHaveCount(0)
}

/** From file… in the Insert panel with the two-solid STEP chosen in the real file chooser. */
async function fromFile(page: Page): Promise<void> {
  const chooser = page.waitForEvent('filechooser')
  await insertPanel(page).getByRole('button', { name: 'From file…' }).click()
  const fc = await chooser
  expect(await fc.element().evaluate((el) => (el as HTMLInputElement).accept)).toBe('.step,.stp')
  await fc.setFiles(PAIR)
}

async function fromLibrary(page: Page): Promise<void> {
  await insertPanel(page).getByRole('button', { name: 'Component library…' }).click()
  await expect(explorer(page)).toBeVisible()
  await expect(insertPanel(page)).toHaveCount(0)
  await explorer(page).getByRole('button', { name: /^fasteners/ }).click()
  await explorer(page).getByRole('button', { name: /m3-nut-block/ }).click()
  await expect(explorer(page).getByRole('button', { name: 'Add to this Part Studio' })).toHaveCount(0)
  await explorer(page).getByRole('button', { name: 'Insert into this Assembly' }).click()
  await expect(explorer(page).getByTestId('ex-status')).toHaveText(/^Inserted /)
}

async function dropFile(page: Page, name: string, bytes: Buffer): Promise<void> {
  await page.evaluate(
    ({ name, b64 }) => {
      const el = document.querySelector('[data-testid="shell-viewport"]')!
      const dt = new DataTransfer()
      dt.items.add(new File([Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))], name))
      el.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }))
      el.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }))
    },
    { name, b64: bytes.toString('base64') },
  )
}

test.describe('Tablet mode', () => {
  test('From file… inserts both solids of a STEP, the first fixed; the explorer inserts a library entry and the panel comes back', async ({ page }) => {
    const library = await mockLibrary(page)
    await newAssembly(page, /^Assembly$/)
    await page.locator('[data-strip="assembly"] [data-tool="insert"]').click()
    await expect(insertPanel(page)).toBeVisible()
    await expect(insertPanel(page).getByRole('button', { name: 'From file…' })).toBeEnabled()
    await expect(insertPanel(page).getByRole('button', { name: 'Component library…' })).toBeEnabled()
    // no native input on the page until the picker is asked for (the number pad's rule)
    await expect(page.locator('input, textarea, select, [contenteditable]')).toHaveCount(0)

    await fromFile(page)
    await expect(insertPanel(page).getByTestId('insert-said')).toHaveText('Inserted 2 parts from two_boxes.step')
    await expect(page.locator('input[type="file"]')).toHaveCount(0)

    await fromLibrary(page)
    expect(library.imports).toHaveLength(1)
    expect(library.imports[0]!.url).toMatch(/\/tabs\/[^/]+\/import$/)                    // no new_tab
    expect(library.imports[0]!.body.root).toBe('lib')
    await explorer(page).getByRole('button', { name: 'Close dialog' }).click()
    await expect(explorer(page)).toHaveCount(0)
    await expect(insertPanel(page)).toBeVisible()                                          // back to the panel it came from
    await expect(insertPanel(page).getByTestId('insert-said')).toHaveText(/^Inserted /)

    await page.locator('[data-strip="assembly"] [data-tool="insert"]').click()
    await expect(insertPanel(page)).toHaveCount(0)
    await expect(rows(page)).toHaveCount(3)
    await expect(rows(page).nth(0)).toHaveAttribute('data-fixed', '')
    await expect(rows(page).nth(1)).not.toHaveAttribute('data-fixed', /.*/)
    await expect(rows(page).nth(2)).not.toHaveAttribute('data-fixed', /.*/)
  })

  test("a file that is not a STEP shows the server's reason and inserts nothing", async ({ page }) => {
    await newAssembly(page, /^Assembly$/)
    await page.locator('[data-strip="assembly"] [data-tool="insert"]').click()
    const chooser = page.waitForEvent('filechooser')
    await insertPanel(page).getByRole('button', { name: 'From file…' }).click()
    await (await chooser).setFiles({ name: 'notes.step', mimeType: 'application/octet-stream', buffer: Buffer.from('not a step file') })
    await expect(page.getByTestId('insert-error')).toContainText('not a STEP')
    await expect(rows(page)).toHaveCount(0)
  })

  test('a drop imports nothing and points to Insert, From file…', async ({ page }) => {
    await newAssembly(page, /^Assembly$/)
    await dropFile(page, 'two_boxes.step', readFileSync(PAIR))
    await expect(page.getByRole('dialog', { name: 'Import' })).toContainText('open Insert, then From file…')
    await expect(rows(page)).toHaveCount(0)
  })
})

test.describe('Desktop mode', () => {
  test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })

  test('From file… and the library insert; a STEP dropped on the view inserts; a BREP dropped says why', async ({ page }) => {
    const library = await mockLibrary(page)
    await newAssembly(page, /^Create Assembly/)
    await page.keyboard.press('i')
    await expect(page.getByRole('dialog', { name: 'Insert parts' })).toHaveClass(/dialog-floating/)
    await fromFile(page)
    await expect(rows(page)).toHaveCount(2)
    await expect(rows(page).nth(0)).toHaveAttribute('data-fixed', '')
    await expect(rows(page).nth(1)).not.toHaveAttribute('data-fixed', /.*/)

    await fromLibrary(page)
    expect(library.imports).toHaveLength(1)
    await expect(rows(page)).toHaveCount(3)
    await page.keyboard.press('Escape')                                                    // the explorer closes first
    await expect(explorer(page)).toHaveCount(0)
    await expect(insertPanel(page)).toBeVisible()
    await page.keyboard.press('i')
    await expect(insertPanel(page)).toHaveCount(0)

    await dropFile(page, 'two_boxes.step', readFileSync(PAIR))
    await expect(rows(page)).toHaveCount(5)
    await expect(rows(page).nth(0)).toHaveAttribute('data-fixed', '')
    for (const i of [1, 2, 3, 4]) await expect(rows(page).nth(i)).not.toHaveAttribute('data-fixed', /.*/)

    await dropFile(page, 'part.brep', Buffer.from('DBRep_DrawableShape'))
    await expect(page.getByRole('dialog', { name: 'Import' })).toContainText('“part.brep” is a BREP file. An Assembly takes STEP files only')
    await expect(rows(page)).toHaveCount(5)
  })
})
