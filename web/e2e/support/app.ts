import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { closeAll } from './docs.ts'
import { waitForModel } from './viewport.ts'

/**
 * Opens a Document of the served Documents root (`freeshape serve`, baseURL) from the home screen, waits for the shell and,
 * unless told not to, for the model. Every Document is closed first (what was not saved is dropped), so the page always
 * starts on the home screen, whatever an earlier spec left open. `document` is the Document's name; the default is Bracket.
 */
export async function openApp(page: Page, opts: { model?: boolean; document?: string } = {}): Promise<void> {
  await closeAll(page.request)
  await page.goto('/')
  await openFromHome(page, opts.document ?? 'Bracket')
  await expect(page.getByTestId('link-status')).toHaveText('Connected', { timeout: 30_000 })
  if (opts.model !== false) await waitForModel(page)
}

/** From the home screen: tap the Document's row and wait for the shell. */
export async function openFromHome(page: Page, name: string): Promise<void> {
  await expect(page.getByTestId('home')).toBeVisible()
  await page.locator('.doc-row', { has: page.locator('.doc-name', { hasText: new RegExp(`^${name}$`) }) }).click()
  await expect(page.getByTestId('shell')).toBeVisible()
}
