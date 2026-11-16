import type { APIRequestContext, Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { decodeOutputs, isStudio } from '../src/wire/index.ts'
import type { StudioOutputs } from '../src/wire/index.ts'
import { openApp } from './support/app.ts'
import { closeAll, idOf } from './support/docs.ts'
import { dragRollbackTo, featureMenu } from './support/features.ts'
import { settled } from './support/viewport.ts'

// Card F16: a sketch, plane or axis can be shown and hidden from the feature list (the row's eye and its menu), and the
// Part Studio view draws and picks the sketch's regions only while it is shown. The Bracket's profile sketch is the one
// Extrude 1 uses; the desktop hides such a sketch when the feature takes it, and it used to be impossible to show it again.

test.afterEach(async ({ page }) => closeAll(page.request))

async function outputs(request: APIRequestContext): Promise<StudioOutputs> {
  const doc = await idOf(request, 'Bracket')
  const tab = (await (await request.get(`/documents/${doc}/tabs`)).json()).tabs[0].id as string
  const body = await (await request.get(`/documents/${doc}/tabs/${tab}/outputs`)).body()
  const out = decodeOutputs(body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength) as ArrayBuffer)
  if (!isStudio(out)) throw new Error('not a Part Studio')
  return out
}

const eyeOf = (page: Page, id: string) => page.locator(`[data-feature-eye="${id}"]`)

/** How many spots of a grid over the view a pen picks as a region of the sketch (a region's centroid can lie outside an L-shaped one). */
async function pickable(page: Page, _out: StudioOutputs, sketch: string): Promise<number> {
  const cam = (await settled(page)).camera
  const spots: { x: number; y: number }[] = []
  for (let i = 1; i < 40; i++) for (let j = 1; j < 30; j++) spots.push({ x: (cam.width * i) / 40, y: (cam.height * j) / 30 })
  const hits = await page.evaluate((g) => g.map((p) => window.__freeshapeViewport!.pickAt(p.x, p.y, 'pen', ['region'])), spots)
  return hits.filter((h) => h?.kind === 'region' && h.part === sketch).length
}

const regionsDrawn = async (page: Page) => (await settled(page)).sketchRegions

async function sketchOfExtrude(page: Page): Promise<{ id: string; out: StudioOutputs }> {
  const out = await outputs(page.request)
  const ex = out.features.find((f) => f.name === 'Extrude 1')!
  const id = out.features.slice(0, out.features.indexOf(ex)).filter((f) => f.type === 'sketch').at(-1)!.id
  return { id, out }
}

async function setEye(page: Page, id: string, shown: boolean): Promise<void> {
  const eye = eyeOf(page, id)
  const want = shown ? 'Hide sketch' : 'Show sketch'
  if ((await eye.getAttribute('aria-label')) !== want) {
    await eye.click()
    await expect(eye).toHaveAttribute('aria-label', want)
  }
}

test('hide then show the sketch Extrude 1 uses: its regions leave the view and come back, pickable', async ({ page }) => {
  await openApp(page)
  const { id, out } = await sketchOfExtrude(page)
  expect(out.sketches.find((k) => k.id === id)!.regions.length).toBeGreaterThan(0)
  await setEye(page, id, true)
  await expect.poll(() => regionsDrawn(page)).toBeGreaterThan(0)
  expect(await pickable(page, out, id)).toBeGreaterThan(0)

  // the eye hides it
  await eyeOf(page, id).click()
  await expect(eyeOf(page, id)).toHaveAttribute('aria-label', 'Show sketch')
  await expect(page.locator(`li[data-hidden] .panel-row[data-feature="${id}"]`)).toHaveCount(1)
  await expect.poll(() => regionsDrawn(page)).toBe(0)
  expect(await pickable(page, out, id)).toBe(0)

  // the row's menu shows it again
  await featureMenu(page, new RegExp(out.features.find((f) => f.id === id)!.name), 'Show')
  await expect(eyeOf(page, id)).toHaveAttribute('aria-label', 'Hide sketch')
  await expect.poll(() => regionsDrawn(page)).toBeGreaterThan(0)
  expect(await pickable(page, out, id)).toBeGreaterThan(0)

  // and the menu hides it
  await featureMenu(page, new RegExp(out.features.find((f) => f.id === id)!.name), 'Hide')
  await expect.poll(() => regionsDrawn(page)).toBe(0)
})

test('with the rollback bar just after a sketch, the sketch can be shown and its regions are drawn and picked', async ({ page }) => {
  await openApp(page)
  const { id, out } = await sketchOfExtrude(page)
  const next = out.features[out.features.findIndex((f) => f.id === id) + 1]!
  await dragRollbackTo(page, new RegExp(`^${next.name}$`))
  await expect(page.getByTestId('rollback-bar')).toBeVisible()
  await setEye(page, id, false)
  await expect.poll(() => regionsDrawn(page)).toBe(0)
  await setEye(page, id, true)
  await expect.poll(() => regionsDrawn(page)).toBeGreaterThan(0)
  const rolled = await outputs(page.request)
  expect(rolled.rollback).toBeLessThanOrEqual(out.features.findIndex((f) => f.id === id) + 1)
  expect(await pickable(page, rolled, id)).toBeGreaterThan(0)
})
