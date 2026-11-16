import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { place, screenFor } from './support/draw.ts'
import { openNewSketch, readSketch, settledSketch } from './support/sketch.ts'

// Slice S7, F20: Construct with nothing selected sets a construction mode, and the tool that is on, tapped again, goes back to
// Select. Tablet mode, WebKit with touch: the strip is tapped with a finger, the points are placed with the pen.

test.afterEach(async ({ page }) => closeAll(page.request))

const strip = (page: Page) => page.locator('[data-strip="sketch"]')
const construct = (page: Page) => strip(page).locator('[data-tool="construct"]')
const tool = async (page: Page) => (await readSketch(page)).draw.tool

/** Choose a tool from the Draw group with a finger: press the group, tap the row. */
async function chooseDraw(page: Page, id: string): Promise<void> {
  await strip(page).locator('[data-group="draw"]').tap()
  await page.locator(`.group-row[data-tool="${id}"] .group-tool`).tap()
}

test('Construct with nothing selected makes the next line construction in one undo step, and Line again goes back to Select', async ({ page }) => {
  await openApp(page)
  await openNewSketch(page, 'Top')

  // Construct is on the strip with no group to open, at least 44 x 44
  const box = (await construct(page).boundingBox())!
  expect(box.width).toBeGreaterThanOrEqual(44)
  expect(box.height).toBeGreaterThanOrEqual(44)
  await expect(construct(page)).toHaveAttribute('aria-pressed', 'false')
  await chooseDraw(page, 'line')
  expect(await tool(page)).toBe('line')
  await construct(page).tap()
  await expect(construct(page)).toHaveAttribute('aria-pressed', 'true')
  // pressing it changed no tool
  expect(await tool(page)).toBe('line')

  // two pen points and a tap on the last one: one construction line, drawn dashed
  const a = await screenFor(page, -30, -10)
  const b = await screenFor(page, 20, 15)
  await place(page, a)
  await place(page, b)
  await place(page, b)
  await expect.poll(async () => (await settledSketch(page)).stats.dashed).toBe(1)
  expect((await settledSketch(page)).stats.lines).toBeGreaterThanOrEqual(1)

  // one Undo takes it away whole: it was made construction in the message that made it
  const undo = page.getByRole('button', { name: 'Undo', exact: true })
  await expect(undo).toBeEnabled()
  await undo.tap()
  await expect.poll(async () => (await settledSketch(page)).stats.dashed).toBe(0)
  await expect(undo).toBeDisabled()
  // the mode is not an edit: it is still on
  await expect(construct(page)).toHaveAttribute('aria-pressed', 'true')

  // a chain half drawn: Line again cancels it and goes back to Select
  await place(page, a)
  await expect.poll(async () => (await settledSketch(page)).draw.picks.length).toBe(1)
  await chooseDraw(page, 'line')
  await expect.poll(() => tool(page)).toBe('select')
  expect((await readSketch(page)).draw.picks).toEqual([])
  await expect(strip(page).locator('[data-tool="select"]')).toHaveAttribute('data-active', '')

  // Select mid-chain does the same
  await chooseDraw(page, 'line')
  await place(page, a)
  await expect.poll(async () => (await settledSketch(page)).draw.picks.length).toBe(1)
  await strip(page).locator('[data-tool="select"]').tap()
  await expect.poll(() => tool(page)).toBe('select')
  expect((await readSketch(page)).draw.picks).toEqual([])

  // and the mode turns off with a second press
  await construct(page).tap()
  await expect(construct(page)).toHaveAttribute('aria-pressed', 'false')
})
