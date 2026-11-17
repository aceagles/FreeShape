import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { openAssemblyTab, readAssembly, settledAssembly } from './support/assembly.ts'
import { desktopMode } from './support/mode.ts'

// Slice S9, card I9.5: the Assembly toolbar in both modes, on the hinge Assembly (I1 fixed, I2 and I3 free). Edits stay
// in the server's memory; the next openApp closes the Document and throws them away.
test.afterEach(async ({ page }) => closeAll(page.request))

const LEAF = 'I2'

async function openHinge(page: Page) {
  await openApp(page, { document: 'Hinge', model: false })
  await openAssemblyTab(page, 3)
}

/** Select the leaf, press Fix in `fix`, and check the instance list shows it fixed. Then Move shows the gizmo of the other free instance. */
async function fixAndMove(page: Page, fix: ReturnType<Page['locator']>, move: ReturnType<Page['locator']>) {
  await openHinge(page)
  await page.locator(`[data-instance="${LEAF}"]`).click()
  await page.getByTestId('gizmo').waitFor()
  await expect(page.locator(`[data-instance-row="${LEAF}"]`)).not.toHaveAttribute('data-fixed', /.*/)
  await fix.click()
  await expect(page.locator(`[data-instance-row="${LEAF}"]`)).toHaveAttribute('data-fixed', '')
  await expect(page.getByTestId('gizmo')).toHaveCount(0)
  // fixed: Move says why there is no gizmo; press Fix again to free it, and Move leaves the gizmo up
  await move.click()
  await expect(page.getByRole('dialog')).toContainText('fixed')
  await page.getByRole('dialog').getByRole('button').first().click()
  await fix.click()
  await expect(page.locator(`[data-instance-row="${LEAF}"]`)).not.toHaveAttribute('data-fixed', /.*/)
  await move.click()
  await page.getByTestId('gizmo').waitFor()
  await expect.poll(async () => (await settledAssembly(page)).gizmo?.instance).toBe(LEAF)
  expect((await readAssembly(page)).instances).toBe(3)
}

test.describe('Tablet mode', () => {
  test('the Assembly strip has Insert, Mate, Relation, Move, Fix and Fit; Fix fixes the selected instance and Move shows its gizmo', async ({ page }) => {
    await openHinge(page)
    await expect(page.locator('[data-strip="assembly"]')).toBeVisible()
    await expect(page.locator('[data-strip="feature"]')).toHaveCount(0)
    await expect(page.locator('[data-strip="assembly"] .strip-label')).toHaveText(['Insert', 'Mate', 'Relation', 'Move', 'Fix', 'Fit'])
    await expect(page.locator('[data-strip="assembly"] [data-tool="insert"]')).toBeEnabled()
    await fixAndMove(page, page.locator('[data-strip="assembly"] [data-tool="asm_fix"]'), page.locator('[data-strip="assembly"] [data-tool="move"]'))
  })
})

test.describe('Desktop mode', () => {
  test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })

  test('the desktop row is drawn from data: Insert and the mates are off with the S10 tooltip; Fix fixes the selected instance and Move shows its gizmo', async ({ page }) => {
    await openHinge(page)
    const bar = page.locator('.desktop-toolbar[data-strip="assembly"]')
    await expect(bar).toBeVisible()
    await expect(page.locator('[data-strip="feature"]')).toHaveCount(0)
    // the mate tools are live since I10.6: the title is the type's label and help from the types route, and its key
    await expect(bar.locator('[data-tool="m_fastened"]')).toBeEnabled()
    await expect(bar.locator('[data-tool="m_fastened"]')).toHaveAttribute('title', /^Fastened mate/)
    await expect(bar.locator('[data-tool="insert"]')).toBeEnabled()
    await expect(bar.locator('[data-tool="bom"]')).toHaveAttribute('title', 'Desktop only for now')
    await fixAndMove(page, bar.locator('[data-tool="asm_fix"]'), bar.locator('[data-tool="move"]'))
  })
})
