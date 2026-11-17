import { expect, test } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, idOf, measureTab } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'

// Slice S10, card I10.2: the Derived feature in the bracket Document, in both modes. A new Part Studio, Derived started from the
// tool strip (Tablet) or the toolbar (Desktop), the bracket Part Studio chosen from the Part Studio list, its part ticked and the
// dialog accepted: the parts list shows the derived part and the kernel measures the bracket's volume in the new tab. Edits stay
// in the server's memory; closeAll throws them away. Every check polls the page or the server: each action is answered by the
// server, not by the next frame.
test.afterEach(async ({ page }) => closeAll(page.request))

const dialog = (page: Page) => page.getByRole('dialog', { name: /^Derived/ })
const partRows = (page: Page) => page.locator('.part-select[data-part]')

/** Open the bracket and add an empty Part Studio, which opens; returns its tab id (the server's newest Part Studio). */
async function newStudio(page: Page, request: APIRequestContext, menuLabel: RegExp): Promise<string> {
  await openApp(page)
  const doc = await idOf(request, 'Bracket')
  await page.getByRole('button', { name: 'Add tab' }).click()
  await page.getByRole('menuitem', { name: menuLabel }).click()
  let tab = ''
  await expect
    .poll(async () => {
      const tabs = (await (await request.get(`/documents/${doc}/tabs`)).json()).tabs as { id: string; kind: string }[]
      tab = tabs.filter((t) => t.kind === 'studio').at(-1)?.id ?? ''
      return tabs.length
    })
    .toBe(2)
  await expect(page.locator('.tab-button[aria-current="page"]')).toHaveAttribute('data-kind', 'studio')
  await expect(page.locator('.tab-button[aria-current="page"]')).not.toHaveText(/bracket/)
  await expect(partRows(page)).toHaveCount(0)
  return tab
}

/** In the open Derived dialog: choose the bracket Part Studio and tick its part (Tablet: a tick button; Desktop: a checkbox). */
async function chooseAndTick(page: Page): Promise<void> {
  const d = dialog(page)
  await expect(d).toBeVisible()
  const studio = d.locator('[data-picker-studio="T1"]')
  await expect(studio).toHaveText('bracket')
  await studio.click()
  await expect(studio).toHaveAttribute('aria-current', 'true')
  const tick = d.getByRole('checkbox', { name: 'Bracket' })
  await expect(tick).toBeVisible()
  await tick.click()
  await expect(d.getByRole('tab', { name: 'Parts (1)' })).toBeVisible()
}

async function acceptAndMeasure(page: Page, request: APIRequestContext, tab: string): Promise<void> {
  await page.locator('.dialog-header').getByRole('button', { name: 'Accept', exact: true }).click()
  await expect(dialog(page)).toHaveCount(0)
  await expect(partRows(page)).toHaveCount(1)
  await expect(partRows(page).first()).toContainText('Bracket')
  const [source] = await measureTab(request, 'Bracket', 'T1')
  await expect.poll(async () => (await measureTab(request, 'Bracket', tab)).length).toBe(1)
  const [made] = await measureTab(request, 'Bracket', tab)
  expect(made!.volume).toBeCloseTo(source!.volume, 3)
  expect(made!.faces).toBe(source!.faces)
}

test.describe('Tablet mode', () => {
  test('Derived from the Create group brings the bracket\'s part into a new Part Studio', async ({ page, request }) => {
    const tab = await newStudio(page, request, /^Part Studio$/)
    await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
    await expect(page.getByRole('menu', { name: 'Create' })).toBeVisible()
    await page.getByRole('menuitem', { name: /^Derived/ }).click()
    await chooseAndTick(page)
    // the tick is a 44 px row of the Parts tab
    const row = (await dialog(page).getByRole('checkbox', { name: 'Bracket' }).boundingBox())!
    expect(Math.round(row.height)).toBeGreaterThanOrEqual(44)
    await acceptAndMeasure(page, request, tab)
  })
})

test.describe('Desktop mode', () => {
  test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })

  test('Derived on the toolbar brings the bracket\'s part into a new Part Studio', async ({ page, request }) => {
    const tab = await newStudio(page, request, /^Create Part Studio/)
    const button = page.locator('.desktop-toolbar').getByRole('button', { name: 'Derived', exact: true })
    await expect(button).toBeEnabled()
    await button.click()
    await chooseAndTick(page)
    await expect(dialog(page).getByRole('checkbox', { name: 'Bracket' })).toBeChecked()
    await acceptAndMeasure(page, request, tab)
  })
})
