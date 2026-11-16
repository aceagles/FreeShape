import { expect, test } from '@playwright/test'

// Card F21: the page's manifest link resolves to the manifest type, and its icons load. Not testable here: whether a
// browser accepts the manifest for installing, and what iPadOS does on "Add to Home Screen" (hand check).
test('the manifest link and the touch icon resolve to the built files', async ({ page, request }) => {
  await page.goto('/')
  const link = page.locator('link[rel="manifest"]')
  expect(await link.getAttribute('crossorigin')).toBe('use-credentials')
  const res = await request.get(new URL((await link.getAttribute('href'))!, page.url()).toString())
  expect(res.status()).toBe(200)
  const manifest = await res.json()
  expect(manifest.name).toBe('FreeShape')
  for (const src of [...manifest.icons.map((i: { src: string }) => i.src), await page.locator('link[rel="apple-touch-icon"]').getAttribute('href')]) {
    const icon = await request.get(new URL(src!, page.url()).toString())
    expect(icon.status()).toBe(200)
    expect(icon.headers()['content-type']).toBe('image/png')
  }
})
