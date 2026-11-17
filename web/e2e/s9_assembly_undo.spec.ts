import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { openAssemblyTab } from './support/assembly.ts'
import { desktopMode } from './support/mode.ts'

// Slice S9, fix F30: Ctrl+Z, Ctrl+Y and Ctrl+Shift+Z undo and redo an Assembly step in Desktop mode, on the hinge Assembly.
// Edits stay in the server's memory; the next openApp closes the Document and throws them away.
test.afterEach(async ({ page }) => closeAll(page.request))
test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })

const LEAF = 'I2'

test('Ctrl+Z takes back Fix on an instance; Ctrl+Y and Ctrl+Shift+Z bring it back', async ({ page }) => {
  await openApp(page, { document: 'Hinge', model: false })
  await openAssemblyTab(page, 3)
  const row = page.locator(`[data-instance-row="${LEAF}"]`)
  await page.locator(`[data-instance="${LEAF}"]`).click()
  await page.getByTestId('gizmo').waitFor()
  await page.locator('.desktop-toolbar[data-strip="assembly"] [data-tool="asm_fix"]').click()
  await expect(row).toHaveAttribute('data-fixed', '')

  // focus is on the page, not on a button or a field
  await page.keyboard.press('Control+z')
  await expect(row).not.toHaveAttribute('data-fixed', /.*/)
  await page.keyboard.press('Control+y')
  await expect(row).toHaveAttribute('data-fixed', '')
  await page.keyboard.press('Control+z')
  await expect(row).not.toHaveAttribute('data-fixed', /.*/)
  await page.keyboard.press('Control+Shift+z')
  await expect(row).toHaveAttribute('data-fixed', '')
})
