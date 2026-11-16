import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { dropFixture, FIXTURE, makeFixture } from './support/create.ts'
import { idOf } from './support/docs.ts'
import { edgeSpot, faceSpots } from './support/pick.ts'
import { sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { readViewport } from './support/viewport.ts'

// Slice S6 gate (card I6.6): creating features in the real client against `freeshape serve`, on a Document the spec
// builds through the server: a Part Studio with one rectangle sketch. The view cannot pick a sketch region yet (nothing
// draws or hits one), so the first solid is made by the server's create route, as the client would send it; every
// feature made in the client is made from a face or an edge picked with the pen, a tool tapped on the strip, a value
// entered on the pad or dragged on an arrow, and Accept.

test.afterEach(async ({ page }) => dropFixture(page.request))

const PEN = 10
const penPick = async (page: Page, at: Point) => {
  await sendPointer(page, [{ phase: 'down', kind: 'pen', id: PEN, ...at }])
  await sendPointer(page, [{ phase: 'up', kind: 'pen', id: PEN, ...at, pressure: 0 }])
}
const features = (page: Page) => page.locator('.panel-row[data-feature]')
const strip = (page: Page) => page.locator('.tool-strip')
const group = (page: Page, name: string) => strip(page).getByRole('button', { name: new RegExp(`^${name}`) })
const header = (page: Page, name: string) => page.locator('.dialog-header').getByRole('button', { name, exact: true })
const key = async (page: Page, ...names: string[]) => {
  for (const n of names) await page.getByTestId('pad').getByRole('button', { name: n, exact: true }).click()
}

/** Tap a tool: open its group, tap its row. */
async function startTool(page: Page, groupName: string, tool: RegExp): Promise<void> {
  await group(page, groupName).click()
  await page.getByRole('menu', { name: groupName }).getByRole('menuitem', { name: tool }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
}

const revisionOnServer = async (page: Page): Promise<number> => {
  const id = await idOf(page.request, FIXTURE)
  return ((await (await page.request.get(`/documents/${id}/tabs`)).json()).tabs as Array<{ revision: number }>)[0]!.revision
}

async function open(page: Page, solid: boolean): Promise<void> {
  await makeFixture(page.request, { solid })
  await openApp(page, { document: FIXTURE, model: solid })
}

test('a sketch with no solid: Extrude starts with an empty profile field, and the first solid made from its region shows as one part', async ({ page }) => {
  await open(page, false)
  expect((await readViewport(page)).parts).toBe(0)
  await expect(features(page)).toHaveCount(1)
  await startTool(page, 'Create', /^Extrude/)
  // nothing is selected and the view has no region to pick: the field is waiting for a pick, and its preview has no model
  await expect(page.getByRole('dialog').getByTestId('dialog-form')).toContainText('Pick in the view')
  await header(page, 'Cancel').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  // the first solid comes from the sketch region over the create route (as the client sends it); the open view follows
  const id = await idOf(page.request, FIXTURE)
  const tab = `/documents/${id}/tabs/T1`
  const headers = { 'X-Client-Id': 'client-s6-create-region' }
  const dialog = (await (await page.request.post(`${tab}/features/dialog`, { data: { type: 'extrude', picks: [{ part: 'F1', kind: 'region', index: 1 }] }, headers })).json()) as { dialog: string }
  await page.request.post(`${tab}/dialog/${dialog.dialog}/update`, { data: { values: { depth: 10 } }, headers })
  expect((await page.request.post(`${tab}/dialog/${dialog.dialog}/accept`, { data: {}, headers })).ok()).toBe(true)
  await page.waitForFunction(() => (window.__freeshapeViewport?.parts ?? 0) === 1)
  await expect(features(page)).toHaveCount(2)
  expect((await readViewport(page)).faces).toBe(6)
})

test('select a face with a pen aim-and-release, tap Extrude, set the depth on the pad, Accept: a feature is added', async ({ page, request }) => {
  await open(page, true)
  const before = await features(page).count()
  const rev = (await readViewport(page)).revision!
  const [spot] = await faceSpots(page, request, 'pen')
  await penPick(page, spot!)
  await expect(page.getByTestId('selection-count')).toHaveText('1 face')
  await startTool(page, 'Create', /^Extrude/)
  await expect(page.getByRole('dialog').getByTestId('dialog-form')).toContainText(/Face \d+ of /)
  // the pad is on Depth; 15 mm, Enter
  await key(page, '1', '5', 'Enter')
  await expect(page.getByRole('button', { name: /^Depth, 15 mm/ })).toBeVisible()
  await page.waitForFunction(() => (window.__freeshapeViewport?.preview ?? 0) >= 1)
  expect(await revisionOnServer(page)).toBe(rev)
  await header(page, 'Accept').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r && window.__freeshapeViewport?.preview === null, rev)
  await expect(features(page)).toHaveCount(before + 1)
  expect((await readViewport(page)).parts).toBeGreaterThanOrEqual(1)
})

test('start Extrude on another face and drag the arrow handle: the previewed depth changes and Accept adds the feature', async ({ page, request }) => {
  await open(page, true)
  const before = await features(page).count()
  const rev = (await readViewport(page)).revision!
  const spots = await faceSpots(page, request, 'pen')
  await penPick(page, spots[1] ?? spots[0]!)
  await startTool(page, 'Create', /^Extrude/)
  const handle = page.locator('.viewport-manipulators [data-part="handle"]')
  const label = page.locator('.viewport-manipulators [data-part="label"]')
  await expect(handle).toHaveCount(1)
  await expect(label).toHaveText(/^[\d.]+ mm$/)
  const start = await label.textContent()
  const first = (await readViewport(page)).preview ?? 0
  const from = { x: Number(await handle.getAttribute('cx')), y: Number(await handle.getAttribute('cy')) }
  const [bx, by] = (await page.locator('.viewport-manipulators path').first().getAttribute('d'))!.match(/-?[\d.]+/g)!.map(Number)
  const len = Math.hypot(from.x - bx!, from.y - by!)
  const along = (px: number) => ({ x: from.x + ((from.x - bx!) / len) * px, y: from.y + ((from.y - by!) / len) * px })
  await sendPointer(page, [
    { phase: 'down', kind: 'pen', id: PEN, ...from },
    ...[1, 2, 3, 4].map((i) => ({ phase: 'move' as const, kind: 'pen' as const, id: PEN, ...along(10 * i) })),
  ])
  await expect(label).not.toHaveText(start!)
  const dragged = await label.textContent()
  await sendPointer(page, [{ phase: 'up', kind: 'pen', id: PEN, ...along(40), pressure: 0 }])
  await expect(page.getByRole('button', { name: new RegExp(`^Depth, ${dragged!.replace(' mm', '')} mm`) })).toBeVisible()
  await page.waitForFunction((n) => (window.__freeshapeViewport?.preview ?? 0) > n, first)
  expect(await revisionOnServer(page)).toBe(rev)
  await header(page, 'Accept').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r && window.__freeshapeViewport?.preview === null, rev)
  await expect(features(page)).toHaveCount(before + 1)
})

test('pick an edge, tap Fillet, enter 2 mm, Accept: the face count rises', async ({ page, request }) => {
  await open(page, true)
  const rev = (await readViewport(page)).revision!
  const faces = (await readViewport(page)).faces
  const spot = await edgeSpot(page, request, 'pen')
  await penPick(page, spot)
  await expect(page.getByTestId('selection-count')).toHaveText('1 edge')
  await startTool(page, 'Modify', /^Fillet/)
  await expect(page.getByRole('dialog').getByTestId('dialog-form')).toContainText(/Edge \d+ of /)
  await key(page, 'Backspace', '2', 'Enter')
  await expect(page.getByRole('button', { name: /^Radius, 2 mm/ })).toBeVisible()
  await page.waitForFunction(() => (window.__freeshapeViewport?.preview ?? 0) >= 1)
  await header(page, 'Accept').click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r && window.__freeshapeViewport?.preview === null, rev)
  await page.waitForFunction((n) => (window.__freeshapeViewport?.faces ?? 0) > n, faces)
  await expect(features(page)).toHaveCount(3)
})

test('a tool the client cannot draw is disabled and shows its note', async ({ page }) => {
  await open(page, true)
  await group(page, 'Modify').click()
  const list = page.getByRole('menu', { name: 'Modify' })
  await expect(list).toBeVisible()
  // a row reads 'off' until the types route has answered
  await expect.poll(() => list.locator('.group-row[data-state="off"]').count()).toBe(0)
  const rows = list.locator('.group-row[data-state="desktop"]')
  expect(await rows.count()).toBeGreaterThan(0)
  const row = rows.first()
  await expect(row).toContainText('Desktop only for now')
  await expect(list.getByRole('menuitem', { name: /Desktop only for now/ }).first()).toBeDisabled()
  // tapping it starts nothing
  await row.click({ force: true })
  await expect(page.getByRole('dialog')).toHaveCount(0)
})
