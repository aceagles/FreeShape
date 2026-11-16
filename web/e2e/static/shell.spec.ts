import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { fontSize } from '../../src/theme/tokens.ts'
import { singleDocument } from '../support/scripted.ts'

// The app shell against a scripted socket (no freeshape serve), at the iPad sizes.
async function open(page: Page, width: number, height: number) {
  await page.setViewportSize({ width, height })
  // a server over one Document: the client goes straight into it
  await singleDocument().install(page)
  await page.goto('/')
  await expect(page.getByTestId('link-status')).toHaveText('Connected')
  await expect(page.getByTestId('document-name')).toHaveText('Bracket')
}

const regionBox = async (page: Page, name: string) => {
  const box = await page.locator(`[data-region="${name}"]`).boundingBox()
  expect(box, name).not.toBeNull()
  return box as { x: number; y: number; width: number; height: number }
}

test('at 1024 x 768 nothing overflows the window and every top bar and strip control is at least 44 x 44', async ({ page }) => {
  await open(page, 1024, 768)
  const overflow = await page.evaluate(() => {
    const out: string[] = []
    const w = window.innerWidth
    const h = window.innerHeight
    // scrolling content is not clipped content: the tab list and the feature rows scroll on purpose
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect()
      if (r.width === 0 && r.height === 0) continue
      if (r.left < -0.5 || r.top < -0.5 || r.right > w + 0.5 || r.bottom > h + 0.5) out.push(`${el.tagName}.${el.getAttribute('class')} ${JSON.stringify(r.toJSON())}`)
    }
    return { out, scroll: [document.documentElement.scrollWidth, document.documentElement.scrollHeight] }
  })
  expect(overflow.out).toEqual([])
  expect(overflow.scroll).toEqual([1024, 768])
  const small = await page.locator('[data-region="top-bar"] button, [data-region="tool-strip"] button').evaluateAll((els) =>
    els
      .map((el) => {
        const r = el.getBoundingClientRect()
        return { name: el.getAttribute('aria-label') ?? el.textContent?.trim(), w: r.width, h: r.height }
      })
      .filter((b) => b.w < 44 || b.h < 44),
  )
  expect(small).toEqual([])
  expect(await page.locator('[data-region="top-bar"] button, [data-region="tool-strip"] button').count()).toBeGreaterThan(8)
})

for (const [width, height, panel] of [[1024, 768, 280], [1366, 1024, 320]] as const) {
  test(`regions have the layout's sizes at ${width} x ${height}`, async ({ page }) => {
    await open(page, width, height)
    expect((await regionBox(page, 'top-bar')).height).toBe(52)
    const strip = await regionBox(page, 'tool-strip')
    expect(strip.height).toBe(64)
    const column = await regionBox(page, 'left-column')
    expect(column.width).toBe(panel)
    const viewport = await regionBox(page, 'viewport')
    expect(viewport.x).toBe(panel)
    expect(viewport.width).toBe(width - panel)
    expect(viewport.y + viewport.height).toBe(height)
    const cube = await regionBox(page, 'view-cube')
    expect([cube.width, cube.height]).toEqual([72, 72])
    expect(cube.x + cube.width).toBe(width - 12)
    expect(cube.y).toBe(viewport.y + 12)
    for (const b of await page.locator('[data-region="tool-strip"] button').all()) {
      const r = await b.boundingBox()
      expect([r?.width, r?.height]).toEqual([72, 56])
    }
  })

  test(`collapsing the panel frees its width, and the dialog covers the panel without moving the viewport at ${width} x ${height}`, async ({ page }) => {
    await open(page, width, height)
    await page.getByRole('button', { name: 'Collapse feature panel' }).click()
    expect((await regionBox(page, 'left-column')).width).toBe(56)
    expect((await regionBox(page, 'viewport')).width).toBe(width - 56)
    await page.getByRole('button', { name: 'Expand feature panel' }).click()
    const before = await regionBox(page, 'viewport')
    await page.evaluate(() => window.__freeshape?.shell.openDialog('Dialog area'))
    const column = await regionBox(page, 'left-column')
    const dialog = await regionBox(page, 'dialog')
    // the column's 1 px right border stays visible beside the dialog
    expect(dialog).toEqual({ ...column, width: column.width - 1 })
    expect(await regionBox(page, 'viewport')).toEqual(before)
  })
}

test('a long strip label and the group mark do not touch', async ({ page }) => {
  await open(page, 1024, 768)
  await page.evaluate(() => {
    const strip = document.querySelector('[data-region="tool-strip"] .strip-button:nth-child(2)')
    const label = strip?.querySelector('.strip-label')
    if (label) label.textContent = 'Constrain'
  })
  const label = await page.locator('.strip-button:nth-child(2) .strip-label').boundingBox()
  const mark = await page.locator('.strip-button:nth-child(2) .strip-mark').boundingBox()
  expect(label && mark).toBeTruthy()
  const apart = (label?.x ?? 0) + (label?.width ?? 0) <= (mark?.x ?? 0) || (mark?.y ?? 0) + (mark?.height ?? 0) <= (label?.y ?? 0)
  expect(apart).toBe(true)
})

test('text sizes and weights are the layout tokens: strip label, Document name, tabs, Connected, panel header', async ({ page }) => {
  await open(page, 1024, 768)
  const style = (selector: string) =>
    page.locator(selector).first().evaluate((el) => {
      const c = getComputedStyle(el)
      return { size: c.fontSize, weight: c.fontWeight }
    })
  expect(await style('.strip-label')).toEqual({ size: `${fontSize.caption}px`, weight: '500' })
  expect(await style('[data-testid="document-name"]')).toEqual({ size: `${fontSize.title}px`, weight: '600' })
  expect(await style('.tab-button[aria-current="page"]')).toEqual({ size: `${fontSize.body}px`, weight: '600' })
  expect(await style('.tab-button:not([aria-current])')).toEqual({ size: `${fontSize.body}px`, weight: '500' })
  expect(await style('[data-testid="link-status"]')).toEqual({ size: `${fontSize.tab}px`, weight: '400' })
  expect(await style('.panel-title')).toEqual({ size: `${fontSize.body}px`, weight: '600' })
})
