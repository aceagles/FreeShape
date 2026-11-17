import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'

// Slice S10, card I10.1: the Insert panel on the hinge Document, in both modes. A new Assembly tab, two parts inserted from the
// panel: the instance list shows two instances, the first fixed. Edits stay in the server's memory; the next openApp closes the
// Document and throws them away. Every check polls the page's state: an insert is answered by the server, not by the next frame.
test.afterEach(async ({ page }) => closeAll(page.request))

const rows = (page: Page) => page.locator('[data-instance-row]')
const part = (page: Page, name: string) => page.locator('[data-picker-part]', { hasText: new RegExp(`^${name}$`) })

/** Open the hinge Document and add an empty Assembly tab, which opens. */
async function newAssembly(page: Page, menuLabel: RegExp): Promise<void> {
  await openApp(page, { document: 'Hinge', model: false })
  await page.getByRole('button', { name: 'Add tab' }).click()
  await page.getByRole('menuitem', { name: menuLabel }).click()
  await expect(page.getByTestId('assembly-panel')).toBeVisible()
  await expect(page.locator('.tab-button[aria-current="page"]')).toHaveAttribute('data-kind', 'assembly')
  await expect(page.getByTestId('instance-list')).toBeAttached()
  await expect(rows(page)).toHaveCount(0)
}

async function checkTwoInstances(page: Page): Promise<void> {
  await expect(rows(page)).toHaveCount(2)
  await expect(rows(page).nth(0)).toHaveAttribute('data-fixed', '')
  await expect(rows(page).nth(1)).not.toHaveAttribute('data-fixed', /.*/)
  await expect(rows(page).nth(0)).toContainText('Base plate')
  await expect(rows(page).nth(1)).toContainText('Arm')
}

test.describe('Tablet mode', () => {
  test('the strip\'s Insert opens the panel over the column; a tap and Insert make a fixed first instance and a free second', async ({ page }) => {
    await newAssembly(page, /^Assembly$/)
    const insert = page.locator('[data-strip="assembly"] [data-tool="insert"]')
    await expect(insert).toBeEnabled()
    await insert.click()
    const panel = page.getByTestId('insert-panel')
    await expect(panel).toBeVisible()
    await expect(page.locator('[data-picker-studio]')).toHaveText('hinge_parts')
    await expect(page.locator('[data-picker-part]')).toHaveCount(3)
    await expect(insert).toHaveAttribute('aria-pressed', 'true')
    const go = page.locator('[data-insert-go]')
    await expect(go).toBeDisabled()
    for (const name of ['Base plate', 'Arm']) {
      await part(page, name).click()
      await expect(go).toBeEnabled()
      await go.click()
      await expect(page.getByTestId('insert-said')).toHaveText(`Inserted ${name}`)
    }
    await expect(panel).toBeVisible()                    // it stays open
    await insert.click()
    await expect(panel).toHaveCount(0)
    await checkTwoInstances(page)
  })
})

test.describe('Desktop mode', () => {
  test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })

  test('the I key opens the floating panel; a double-click on a part inserts it', async ({ page }) => {
    await newAssembly(page, /^Create Assembly/)
    await page.keyboard.press('i')
    const panel = page.getByTestId('insert-panel')
    await expect(panel).toBeVisible()
    await expect(page.getByRole('dialog', { name: 'Insert parts' })).toHaveClass(/dialog-floating/)
    await expect(page.locator('[data-picker-part]')).toHaveCount(3)
    for (const name of ['Base plate', 'Arm']) {
      await part(page, name).dblclick()
      await expect(page.getByTestId('insert-said')).toHaveText(`Inserted ${name}`)
    }
    await expect(panel).toBeVisible()
    await expect(rows(page)).toHaveCount(2)              // the left column stays beside the floating panel
    await checkTwoInstances(page)
    await page.keyboard.press('i')
    await expect(panel).toHaveCount(0)
    await page.keyboard.press('i')
    await expect(panel).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(panel).toHaveCount(0)
  })
})
