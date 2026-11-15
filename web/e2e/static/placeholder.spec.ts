import { expect, test } from '@playwright/test'

test('the built placeholder page shows the server version', async ({ page }) => {
  await page.route('**/health', (route) => route.fulfill({ json: { ok: true, version: '1.2.3' } }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'FreeShape' })).toBeVisible()
  await expect(page.getByTestId('version')).toHaveText('Server version 1.2.3')
})
