import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp, openFromHome } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { partRow } from './support/parts.ts'

// Slice S9, card I9.1: Desktop and Tablet modes and the top-bar toggle. These tests start with nothing stored and a fine
// pointer with hover, which is how a desktop browser looks, so the client picks Desktop mode by itself.
test.use(desktopMode)
test.afterEach(async ({ page }) => closeAll(page.request))

const shellMode = (page: Page) => page.getByTestId('shell').getAttribute('data-mode')
const option = (page: Page, name: 'Desktop' | 'Tablet') => page.getByTestId('mode-toggle').getByRole('button', { name })

test('a page with nothing stored opens in Desktop mode', async ({ page }) => {
  await openApp(page)
  expect(await shellMode(page)).toBe('desktop')
  await expect(option(page, 'Desktop')).toHaveAttribute('aria-pressed', 'true')
  await expect(option(page, 'Tablet')).toHaveAttribute('aria-pressed', 'false')
})

test('the toggle switches Tablet and back with the open tab and the selection kept, and a reload keeps the last choice', async ({ page }) => {
  await openApp(page)
  const tab = page.locator('.tab-button[aria-current="page"]')
  const tabName = await tab.textContent()
  await partRow(page, /./).first().click()
  const selected = await page.getByTestId('selection-count').textContent()
  expect(selected).not.toBe('Nothing selected')
  const canvas = await page.locator('[data-region="viewport"] canvas').first().evaluateHandle((c) => c)

  await option(page, 'Tablet').click()
  expect(await shellMode(page)).toBe('tablet')
  expect(await page.evaluate(() => window.localStorage.getItem('freeshape.mode'))).toBe('tablet')
  await expect(tab).toHaveText(tabName ?? '')
  await expect(page.getByTestId('selection-count')).toHaveText(selected ?? '')
  expect(await canvas.evaluate((c) => c.isConnected)).toBe(true)

  await option(page, 'Desktop').click()
  expect(await shellMode(page)).toBe('desktop')
  await expect(tab).toHaveText(tabName ?? '')
  await expect(page.getByTestId('selection-count')).toHaveText(selected ?? '')

  await option(page, 'Tablet').click()
  await page.reload()
  // the reload may land on the home screen: open the Document again
  await expect(page.getByTestId('home').or(page.getByTestId('shell'))).toBeVisible({ timeout: 30_000 })
  if (await page.getByTestId('home').isVisible()) await openFromHome(page, 'Bracket')
  expect(await shellMode(page)).toBe('tablet')
})
