import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { readViewport } from './support/viewport.ts'

// The harness's own spec: the served client loads under WebKit at 1024 x 768 with touch, and the shell is there.
// (The model view's checks are I1.12's, in s1_view.spec.ts.)
test('the served client loads and shows the app shell', async ({ page }) => {
  await openApp(page, { model: false })
  for (const region of ['top-bar', 'tool-strip', 'left-column', 'viewport', 'view-cube']) {
    await expect(page.locator(`[data-region="${region}"]`), region).toBeVisible()
  }
  expect(page.viewportSize()).toEqual({ width: 1024, height: 768 })
})

test('the viewport hook answers once the page is up', async ({ page }) => {
  await openApp(page, { model: false })
  await expect.poll(async () => (await readViewport(page)).frames).toBeGreaterThan(0)
})
