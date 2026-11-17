import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { mockLibrary } from './support/library.ts'
import { desktopMode } from './support/mode.ts'
import { partRow } from './support/parts.ts'

// Slice S11, card I11.7: the file explorer, in both modes, against `freeshape serve`. The e2e server has no file root
// (scripts/web/common.sh passes no --file-root), so the page's /files routes are answered from web/e2e/fixtures/library/
// (support/library.ts); the import itself is the real route, fed the fixture's bytes, so the Part Studio really gets the part.
test.afterEach(async ({ page }) => closeAll(page.request))

const explorer = (page: Page) => page.getByRole('dialog', { name: 'Files' })
const partCount = (page: Page) => partRow(page, /./).count()

test.describe('Tablet mode', () => {
  test("the strip's Import: find a part by search, see its licence, add it to the bracket's Part Studio", async ({ page }) => {
    const library = await mockLibrary(page)
    await openApp(page)
    await expect.poll(() => partCount(page)).toBeGreaterThan(0)
    const before = await partCount(page)

    await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
    await page.getByRole('menuitem', { name: /^Import/ }).click()
    await expect(explorer(page)).toBeVisible()
    // the first file root is on, listing its folders
    await expect(explorer(page).getByRole('button', { name: /^bearings/ })).toBeVisible()
    await expect(explorer(page).getByRole('navigation', { name: 'Roots' }).getByRole('button')).toHaveText([/Documents/, /lib/])

    // Tablet mode types the search in a prompt, as a tab's name is typed
    await explorer(page).getByRole('button', { name: 'Search', exact: true }).click()
    const field = page.getByRole('dialog', { name: 'Search' }).getByRole('textbox')
    await field.fill('skate')
    await field.press('Enter')
    await expect(explorer(page).getByText('1 match')).toBeVisible()
    const row = explorer(page).getByRole('button', { name: /skate-608/ })
    await expect(row).toContainText('CC-BY-4.0')
    await row.click()
    const detail = explorer(page).getByTestId('ex-detail')
    await expect(detail).toContainText('CC-BY-4.0')
    await expect(detail).toContainText('Ann Author, Bo Writer')
    await expect(detail).toContainText('https://example.org/skate-608')

    await explorer(page).getByRole('button', { name: 'Add to this Part Studio' }).click()
    await expect(explorer(page).getByTestId('ex-status')).toHaveText('Imported skate-608')
    expect(library.imports.map((i) => i.url.replace(/\?.*/, ''))).toEqual([expect.stringMatching(/^\/documents\/[^/]+\/tabs\/T1\/import$/)])
    expect(library.imports[0]!.body.root).toBe('lib')

    await explorer(page).getByRole('button', { name: 'Close dialog' }).click()
    await expect(explorer(page)).toHaveCount(0)
    await expect.poll(() => partCount(page)).toBe(before + 1)
  })

  test('Open in a new Part Studio makes a tab named after the file and shows it', async ({ page }) => {
    const library = await mockLibrary(page)
    await openApp(page)
    await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
    await page.getByRole('menuitem', { name: /^Import/ }).click()
    await explorer(page).getByRole('button', { name: /^fasteners/ }).click()
    await explorer(page).getByRole('button', { name: /m3-nut-block/ }).click()
    await explorer(page).getByRole('button', { name: 'Open in a new Part Studio' }).click()
    await expect(explorer(page).getByTestId('ex-status')).toHaveText('Imported m3-nut-block')
    expect(library.imports[0]!.url).toMatch(/\/import\?new_tab=1$/)
    await expect(page.locator('.top-bar-tabs [aria-current="page"]')).toHaveText('m3-nut-block')
  })

  test("an entry the server refuses says the server's reason", async ({ page }) => {
    await mockLibrary(page)
    await openApp(page)
    await page.route(/\/tabs\/[^/]+\/import/, (route) => route.fulfill({ status: 409, json: { error: 'the file changed on disk: rescan the root' } }), { times: 1 })
    await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
    await page.getByRole('menuitem', { name: /^Import/ }).click()
    await explorer(page).getByRole('button', { name: /^fasteners/ }).click()
    await explorer(page).getByRole('button', { name: /m3-nut-block/ }).click()
    await explorer(page).getByRole('button', { name: 'Add to this Part Studio' }).click()
    await expect(explorer(page).getByTestId('ex-status')).toHaveText('the file changed on disk: rescan the root')
  })

  test("the home screen's Files button browses without an import, and a Document in its Documents root opens", async ({ page }) => {
    await mockLibrary(page)
    await closeAll(page.request)
    await page.goto('/')
    await page.getByRole('button', { name: 'Files', exact: true }).click()
    await expect(explorer(page)).toBeVisible()
    await explorer(page).getByRole('button', { name: /^fasteners/ }).click()
    await explorer(page).getByRole('button', { name: /m3-nut-block/ }).click()
    await expect(explorer(page).getByRole('button', { name: 'Add to this Part Studio' })).toBeDisabled()
    await explorer(page).getByRole('navigation', { name: 'Roots' }).getByRole('button', { name: /^Documents/ }).click()
    await explorer(page).getByRole('button', { name: /^Bracket/ }).click()
    await expect(page.getByTestId('shell')).toBeVisible({ timeout: 30_000 })
  })
})

test.describe('Desktop mode', () => {
  test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })

  test("Ctrl+Shift+L: search types in the box, shows the licence, and adds the part to the bracket's Part Studio", async ({ page }) => {
    const library = await mockLibrary(page)
    await openApp(page)
    await expect.poll(() => partCount(page)).toBeGreaterThan(0)
    const before = await partCount(page)

    await page.keyboard.press('Control+Shift+L')
    await expect(explorer(page)).toBeVisible()
    await expect(explorer(page)).toHaveClass(/dialog-floating/)
    const box = explorer(page).getByRole('textbox', { name: 'Search' })
    await box.fill('skate')
    await expect(explorer(page).getByText('1 match')).toBeVisible()
    const row = explorer(page).getByRole('button', { name: /skate-608/ })
    await expect(row).toContainText('CC-BY-4.0')
    await row.click()
    await expect(explorer(page).getByTestId('ex-detail')).toContainText('Ann Author, Bo Writer')
    await explorer(page).getByRole('button', { name: 'Add to this Part Studio' }).click()
    await expect(explorer(page).getByTestId('ex-status')).toHaveText('Imported skate-608')
    expect(library.imports).toHaveLength(1)
    await explorer(page).getByRole('button', { name: 'Close dialog' }).click()
    await expect(explorer(page)).toHaveCount(0)
    await expect.poll(() => partCount(page)).toBe(before + 1)
  })

  test('the Document menu items and the toolbar open it, and Escape closes it', async ({ page }) => {
    await mockLibrary(page)
    await openApp(page)
    const bar = page.locator('[data-region="top-bar"]')
    for (const [item, mode] of [
      ['Import STEP / BREP (new tab)…', 'new'],
      ['Import STEP / BREP into this tab…', 'add'],
      ['Component library…', 'browse'],
    ] as const) {
      await bar.getByRole('button', { name: 'Document', exact: true }).click()
      await page.getByRole('menuitem', { name: item }).click()
      await expect(page.getByTestId('explorer')).toHaveAttribute('data-open-mode', mode)
      await page.keyboard.press('Escape')
      await expect(explorer(page)).toHaveCount(0)
    }
    await page.locator('.desktop-toolbar [data-tool="import"]').click()
    await expect(page.getByTestId('explorer')).toHaveAttribute('data-open-mode', 'new')
  })
})
