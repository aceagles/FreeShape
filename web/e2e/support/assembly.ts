import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import type { AssemblyReport } from '../../src/assembly/controller.ts'

/** Reads `window.__freeshapeAssembly` (instances, drawn, hidden, geometries, camera, frames, pickAt). */
export async function readAssembly(page: Page): Promise<AssemblyReport> {
  const report = await page.evaluate(() => window.__freeshapeAssembly ?? null)
  if (report === null) throw new Error('window.__freeshapeAssembly is not there: the Assembly view has not mounted')
  return report
}

/** Opens the Assembly tab of the open Document and waits until its instances are drawn (at least `drawn` of them). */
export async function openAssemblyTab(page: Page, drawn = 1): Promise<AssemblyReport> {
  await page.locator('.tab-button[data-kind="assembly"]').click()
  await page.getByTestId('instance-list').waitFor()
  await page.waitForFunction((n) => {
    const v = window.__freeshapeAssembly
    return v !== undefined && v.drawn >= n && v.frames > 0
  }, drawn, { timeout: 30_000 })
  return readAssembly(page)
}

/** Lets two frames pass (so the last change has been drawn), then reads the Assembly hook. */
export async function settledAssembly(page: Page): Promise<AssemblyReport> {
  await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))))
  return readAssembly(page)
}

/** The gizmo the hook reports once it is there: one read both checks and returns it, so a later read cannot find it gone. */
export async function gizmoOf(page: Page): Promise<NonNullable<AssemblyReport['gizmo']>> {
  let found: AssemblyReport['gizmo'] = null
  await expect
    .poll(async () => {
      found = (await readAssembly(page)).gizmo
      return found !== null
    })
    .toBe(true)
  return found!
}
