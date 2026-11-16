import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { openAssemblyTab, settledAssembly } from './support/assembly.ts'
import { closeAll } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'

// Slice S9, card I9.15: the left column at the desktop's density in Desktop mode, 1440 x 900. The literals are what the
// desktop program's visualItemRect gave on MainWindow(engine=True) at 1440 x 900 (features and mates 26 px, parts and
// instances 24 px, the rollback row 12 px with an 8 px bar); a row may be 1 px taller.
test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })
test.afterEach(async ({ page }) => closeAll(page.request))

const FEATURE = 26
const PART = 24
const MATE = 26

/** No element of `rows` is taller than `max` + 1, and there is at least one. */
async function noneTaller(rows: Locator, max: number, what: string): Promise<void> {
  const n = await rows.count()
  expect(n, `${what} rows`).toBeGreaterThan(0)
  for (let i = 0; i < n; i++) {
    const box = (await rows.nth(i).boundingBox())!
    expect(box.height, `${what} row ${i}: ${await rows.nth(i).textContent()}`).toBeLessThanOrEqual(max + 1)
    expect(box.height, `${what} row ${i} is not collapsed`).toBeGreaterThanOrEqual(max - 4)
  }
}

const mode = (page: Page) => page.getByTestId('shell').getAttribute('data-mode')

test('Part Studio (bracket): feature and part rows are at the desktop height and the rollback bar is 8 px in a 12 px row', async ({ page }) => {
  await openApp(page)
  expect(await mode(page)).toBe('desktop')
  await noneTaller(page.locator('[aria-label="Features"] > li[data-row]'), FEATURE, 'feature')
  await noneTaller(page.getByTestId('parts-list').locator('li'), PART, 'part')
  const bar = page.getByTestId('rollback-bar')
  expect((await bar.boundingBox())!.height).toBe(12)
  expect((await bar.locator('.rollback-line').boundingBox())!.height).toBe(8)
  // the font and the row's padding are the desktop's
  const row = page.locator('.panel-row[data-feature]').first()
  const css = await row.evaluate((el) => {
    const s = getComputedStyle(el)
    return { font: s.fontSize, pad: `${s.paddingTop} ${s.paddingRight}`, icon: el.querySelector('svg')!.getBoundingClientRect().width }
  })
  expect(css).toEqual({ font: '13px', pad: '3px 6px', icon: 18 })
  // a section header: 13 px bold
  const head = await page.locator('.parts-heading').evaluate((el) => ({ w: getComputedStyle(el).fontWeight, s: getComputedStyle(el).fontSize }))
  expect(head).toEqual({ w: '700', s: '13px' })
})

test('Part Studio: the eye of a part shows on the hovered row only, a hidden feature keeps its eye', async ({ page }) => {
  await openApp(page)
  const eye = page.locator('[data-part-eye]').first()
  await page.mouse.move(5, 5)
  await expect(eye).toHaveCSS('opacity', '0')
  await page.locator('.part-select[data-part]').first().hover()
  await expect(eye).toHaveCSS('opacity', '1')
  const hiddenSketchEye = page.locator('.feature-eyed[data-hidden] .feature-eye').first()
  await expect(hiddenSketchEye).toHaveCSS('opacity', '1')
})

test('Assembly (hinge): instance and mate rows are at the desktop height', async ({ page }) => {
  await openApp(page, { document: 'Hinge', model: false })
  await openAssemblyTab(page, 3)
  await settledAssembly(page)
  await noneTaller(page.getByTestId('instance-list').locator('li'), PART, 'instance')
  await noneTaller(page.getByTestId('mate-list').locator('li'), MATE, 'mate')
  expect((await page.locator('.instance-row [data-instance-eye]').first().evaluate((el) => el.querySelector('svg')!.getBoundingClientRect().width))).toBe(18)
})
