import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { fingerTap } from './support/pointer.ts'
import { openSketchFeature, screenOf, settledSketch } from './support/sketch.ts'

// F10: a hint or note over the sketch view never covers a dimension label or the sketch. The boxes are measured in the
// page, on the Bracket profile (four labels) in the 1024 x 768 window; nothing is committed.

test.afterEach(async ({ page }) => closeAll(page.request))

/** Bottom of the room src/sketch/band.ts keeps for the chips and the hints (TOP_BAND), in CSS pixels from the view's top. */
const TOP_BAND = 180

interface Box {
  name: string
  x: number
  y: number
  w: number
  h: number
}

/** Every box over the view (chip, status, note, hints) and every label, in CSS pixels from the view's top left. */
async function boxes(page: Page): Promise<{ over: Box[]; labels: Box[]; points: Box[] }> {
  return page.evaluate(() => {
    const view = document.querySelector('[data-testid="shell-viewport"]')!.getBoundingClientRect()
    const rect = (name: string, r: DOMRect): Box => ({ name, x: r.x - view.x, y: r.y - view.y, w: r.width, h: r.height })
    const over = [...document.querySelectorAll('.chip-slot > *')].map((e) => rect(e.getAttribute('data-testid') ?? e.className, e.getBoundingClientRect()))
    const labels = [...document.querySelectorAll('.sketch-layer g[data-kind="dimension"] rect')].map((e) => rect('label', e.getBoundingClientRect()))
    const points = [...document.querySelectorAll('.sketch-layer circle')].map((e) => rect('point', e.getBoundingClientRect()))
    return { over, labels, points }
  })
}

const meets = (a: Box, b: Box): boolean => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h

async function expectClear(page: Page, expectNames: string[]) {
  const { over, labels, points } = await boxes(page)
  const names = over.map((o) => o.name)
  for (const n of expectNames) expect(names).toContain(n)
  expect(labels.length).toBe(4)
  for (const o of over) {
    expect(o.y + o.h, `${o.name} ends inside the top band`).toBeLessThanOrEqual(TOP_BAND)
    for (const l of labels) expect(meets(o, l), `${o.name} on a label at ${Math.round(l.y)}`).toBe(false)
    for (const p of points) expect(meets(o, p), `${o.name} on a point at ${Math.round(p.y)}`).toBe(false)
  }
}

const dimensionTool = (page: Page) => page.locator('[data-strip="sketch"] [data-tool="dimension"]')

test('the Trim hint stays off the labels and the sketch', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await settledSketch(page)
  await page.locator('.tool-strip').getByRole('button', { name: /^Draw/ }).click()
  await page.locator('.group-row[data-tool="trim"]').click()
  await page.getByTestId('edit-hint').waitFor()
  await expectClear(page, ['edit-hint'])
})

test('the Dimension hint, with one item picked, stays off the labels and the sketch', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await settledSketch(page)
  await dimensionTool(page).click()
  await page.getByTestId('dimension-hint').waitFor()
  await expectClear(page, ['dimension-hint'])
  const a = await screenOf(page, 'point', 'origin')
  const b = await screenOf(page, 'point', 'p2')
  await fingerTap(page, { x: a.x + (b.x - a.x) / 4, y: a.y + (b.y - a.y) / 4 })
  await expect(page.getByTestId('dimension-hint')).toContainText('where the dimension goes')
  await expectClear(page, ['dimension-hint'])
})

test('a constraint note, alone and with the Dimension hint under it, stays off the labels and the sketch', async ({ page }) => {
  let opened: { sketch: { entities: Record<string, { p1: string; p2: string }>; constraints: { type: string; refs: string[] }[] } } | null = null
  page.on('websocket', (ws) =>
    ws.on('framereceived', (f) => {
      if (typeof f.payload !== 'string' || !f.payload.includes('sketch.opened')) return
      try {
        const m = JSON.parse(f.payload) as { type: string }
        if (m.type === 'sketch.opened') opened = m as unknown as NonNullable<typeof opened>
      } catch {
        // not JSON
      }
    }),
  )
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await settledSketch(page)
  // a line held horizontal, then Vertical on it: refused, the note shows
  const sk = opened!.sketch
  const ent = sk.entities[sk.constraints.find((c) => c.type === 'horizontal')!.refs[0]!]!
  const a = await screenOf(page, 'point', ent.p1)
  const b = await screenOf(page, 'point', ent.p2)
  await fingerTap(page, { x: a.x + (b.x - a.x) * 0.25, y: a.y + (b.y - a.y) * 0.25 })
  await expect(page.getByTestId('selection-count')).toHaveText('1 item')
  await page.locator('.tool-strip').getByRole('button', { name: /^Constrain/ }).click()
  await page.locator('.group-row[data-tool="vertical"][data-state="ok"]').waitFor()
  await page.locator('.group-row[data-tool="vertical"] .group-tool').click()
  await page.getByTestId('constraint-note').waitFor()
  await expectClear(page, ['constraint-note'])
  // the Dimension tool on: its hint goes under the note when the note is still up
  await dimensionTool(page).click()
  await page.getByTestId('dimension-hint').waitFor()
  await expectClear(page, ['dimension-hint'])
})
