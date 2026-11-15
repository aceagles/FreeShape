import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { waitForModel } from './viewport.ts'

/** Opens the client served by `freeshape serve` (baseURL), waits for the shell and, unless told not to, for the model. */
export async function openApp(page: Page, opts: { model?: boolean } = {}): Promise<void> {
  await page.goto('/')
  await expect(page.getByTestId('shell')).toBeVisible()
  await expect(page.getByTestId('link-status')).toHaveText('Connected', { timeout: 30_000 })
  if (opts.model !== false) await waitForModel(page)
}
