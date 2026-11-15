import { pathToFileURL } from 'node:url'
import { resolve } from 'node:path'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

// web/design/layout.html is a static page: it is opened from disk, with no server and no network.
const page_url = (frame: string): string => pathToFileURL(resolve(import.meta.dirname, '../../design/layout.html')).href + '#' + frame

// Frames that are not the target are hidden but still in the DOM: scope every region to the one shown.
const region = (page: Page, name: string) => page.locator(`.sheet:target [data-region="${name}"]`)

const modes = ['browsing', 'flyout', 'dialog', 'sketch', 'constrain', 'radial', 'collapsed']
const sizes = [
  { key: '1024', width: 1024, height: 768 },
  { key: '1366', width: 1366, height: 1024 },
]

test('the page loads nothing from the network and shows all seven states at both sizes', async ({ page }) => {
  const remote: string[] = []
  page.on('request', (request) => {
    if (!request.url().startsWith('file:')) remote.push(request.url())
  })
  await page.goto(page_url(''))
  for (const s of sizes) {
    for (const mode of modes) {
      const box = await page.locator(`#${mode}-${s.key} > div`).boundingBox()
      expect(box, `${mode} ${s.key}`).not.toBeNull()
      expect([box?.width, box?.height]).toEqual([s.width, s.height])
    }
  }
  expect(remote).toEqual([])
})

for (const s of sizes) {
  for (const mode of modes) {
    test(`no button is smaller than 44 x 44: ${mode} at ${s.width} x ${s.height}`, async ({ page }) => {
      await page.setViewportSize({ width: s.width, height: s.height })
      await page.goto(page_url(`${mode}-${s.key}`))
      const buttons = page.locator('button:visible')
      expect(await buttons.count()).toBeGreaterThan(10)
      const small = await buttons.evaluateAll((els) =>
        els
          .map((el) => {
            const r = el.getBoundingClientRect()
            return { name: el.getAttribute('aria-label') ?? el.textContent?.trim(), w: r.width, h: r.height }
          })
          .filter((b) => b.w < 44 || b.h < 44),
      )
      expect(small).toEqual([])
    })

    test(`nothing is clipped or overlapping: ${mode} at ${s.width} x ${s.height}`, async ({ page }) => {
      await page.setViewportSize({ width: s.width, height: s.height })
      await page.goto(page_url(`${mode}-${s.key}`))
      const frame = page.locator(`#${mode}-${s.key} > div`)
      expect(await frame.evaluate((el) => [el.scrollWidth, el.scrollHeight])).toEqual([s.width, s.height])
      // Every region sits inside the window.
      const regions = await page.locator('.sheet:target [data-region]').evaluateAll((els) =>
        els.map((el) => ({ name: el.getAttribute('data-region'), r: el.getBoundingClientRect().toJSON() })),
      )
      for (const { name, r } of regions) {
        if (name === 'quick-menu') continue
        expect(r.left, `${name} left`).toBeGreaterThanOrEqual(0)
        expect(r.top, `${name} top`).toBeGreaterThanOrEqual(0)
        expect(r.right, `${name} right`).toBeLessThanOrEqual(s.width + 0.5)
        expect(r.bottom, `${name} bottom`).toBeLessThanOrEqual(s.height + 0.5)
      }
      // The chip and the view cube do not meet.
      const chip = await region(page, 'selection-chip').boundingBox()
      const cube = await region(page, 'view-cube').boundingBox()
      expect((chip?.x ?? 0) + (chip?.width ?? 0)).toBeLessThan(cube?.x ?? 0)
    })
  }
}

test('with a dialog open at 1024 x 768 the model area is at least 60 percent of the width', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 })
  await page.goto(page_url('dialog-1024'))
  const viewport = await region(page, 'viewport').boundingBox()
  expect((viewport?.width ?? 0) / 1024).toBeGreaterThanOrEqual(0.6)
})

test('the dialog covers the feature panel exactly and the pad sits at its foot', async ({ page }) => {
  for (const s of sizes) {
    await page.setViewportSize({ width: s.width, height: s.height })
    await page.goto(page_url(`dialog-${s.key}`))
    const column = await region(page, 'left-column').boundingBox()
    const dialog = await region(page, 'dialog').boundingBox()
    const pad = await region(page, 'pad').boundingBox()
    expect(dialog).toEqual(expect.objectContaining({ x: column?.x, y: column?.y }))
    expect(Math.abs((dialog?.height ?? 0) - (column?.height ?? 1))).toBeLessThanOrEqual(1)
    expect(Math.abs((pad?.y ?? 0) + (pad?.height ?? 0) - ((dialog?.y ?? 0) + (dialog?.height ?? 0)))).toBeLessThanOrEqual(1)
  }
})

test('the strip is 64 high, the top bar 52, the feature panel and rail have the card widths', async ({ page }) => {
  for (const s of sizes) {
    await page.setViewportSize({ width: s.width, height: s.height })
    await page.goto(page_url(`browsing-${s.key}`))
    expect((await region(page, 'top-bar').boundingBox())?.height).toBe(52)
    expect((await region(page, 'tool-strip').boundingBox())?.height).toBe(64)
    expect((await region(page, 'left-column').boundingBox())?.width).toBe(s.key === '1024' ? 280 : 320)
    await page.goto(page_url(`collapsed-${s.key}`))
    expect((await region(page, 'left-column').boundingBox())?.width).toBe(56)
  }
})
