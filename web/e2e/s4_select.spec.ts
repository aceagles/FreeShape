import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, idOf } from './support/docs.ts'
import { edgeSpot, emptySpot, faceSpots, pickAll, vertexSpot } from './support/pick.ts'
import { fingerTap, sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { readViewport, settled } from './support/viewport.ts'

// Slice S4, selection (card I4.2) against `freeshape serve`: Aim, Commit on release and the selection chip, with synthetic
// pen, finger and mouse sequences on the canvas. What is selected is read from the viewport hook (`selection`) and from
// the chip on the screen. The slice gate (I4.4) adds the gate cases at the end: the lift over empty space, the finger on
// an edge, and the reference the refs route gives for the selected item.

test.afterEach(async ({ page }) => closeAll(page.request))

const PEN = 10
const penDown = (page: Page, at: Point) => sendPointer(page, [{ phase: 'down', kind: 'pen', id: PEN, ...at }])
const penMove = (page: Page, at: Point) => sendPointer(page, [{ phase: 'move', kind: 'pen', id: PEN, ...at }])
const penUp = (page: Page, at: Point) => sendPointer(page, [{ phase: 'up', kind: 'pen', id: PEN, ...at, pressure: 0 }])
const penCancel = (page: Page, at: Point) => sendPointer(page, [{ phase: 'cancel', kind: 'pen', id: PEN, ...at }])
const penPick = async (page: Page, at: Point) => {
  await penDown(page, at)
  await penUp(page, at)
}
const selection = async (page: Page) => (await readViewport(page)).selection
const names = async (page: Page) => (await selection(page)).items.map((i) => i.name)
const chip = (page: Page) => page.getByTestId('selection-count')
const multi = (page: Page) => page.getByTestId('selection-multi')

test('the chip is there with nothing selected, and Multi is reachable before the first pick', async ({ page }) => {
  await openApp(page)
  await expect(page.getByTestId('selection-chip')).toBeVisible()
  await expect(chip(page)).toHaveText('Nothing selected')
  await expect(multi(page)).toHaveAttribute('aria-pressed', 'false')
  await multi(page).click()
  await expect(multi(page)).toHaveAttribute('aria-pressed', 'true')
  expect((await selection(page)).multi).toBe(true)
  const box = await page.getByTestId('selection-chip').boundingBox()
  expect(box?.height).toBe(52)
})

test('the pen Aims while it is down and Commits when it lifts: down on one face, over another, up selects the second', async ({ page, request }) => {
  await openApp(page)
  const [a, b] = await faceSpots(page, request, 'pen')
  expect(a!.face).not.toBe(b!.face)
  await penDown(page, a!)
  expect((await selection(page)).aimed).toMatchObject({ kind: 'face', name: a!.name })
  expect((await selection(page)).count).toBe(0)
  await penMove(page, b!)
  expect((await selection(page)).aimed).toMatchObject({ kind: 'face', name: b!.name })
  await penUp(page, b!)
  const s = await selection(page)
  expect(s.items).toMatchObject([{ kind: 'face', name: b!.name, index: b!.face }])
  expect(s.aimed).toBeNull()
  await expect(chip(page)).toHaveText('1 face')
})

test('single mode: a Commit replaces the selection, on the selected item it deselects, over empty space it clears', async ({ page, request }) => {
  await openApp(page)
  const [a, b] = await faceSpots(page, request, 'pen')
  const empty = await emptySpot(page, 'pen')
  await penPick(page, a!)
  await penPick(page, b!)
  expect(await names(page)).toEqual([b!.name])
  await penPick(page, b!)
  expect(await names(page)).toEqual([])
  await expect(chip(page)).toHaveText('Nothing selected')
  await penPick(page, a!)
  await penPick(page, empty)
  expect(await names(page)).toEqual([])
})

test('an edge and a vertex are selected, and the chip counts them', async ({ page, request }) => {
  await openApp(page)
  const e = await edgeSpot(page, request, 'pen')
  const v = await vertexSpot(page, request, 'pen')
  await penPick(page, e)
  expect((await selection(page)).items).toMatchObject([{ kind: 'edge', name: e.name }])
  await expect(chip(page)).toHaveText('1 edge')
  await penPick(page, v)
  expect((await selection(page)).items).toMatchObject([{ kind: 'vertex', name: v.name }])
  await expect(chip(page)).toHaveText('1 vertex')
})

test('a finger tap selects the item under it, and a finger drag selects nothing and orbits', async ({ page, request }) => {
  await openApp(page)
  const [a] = await faceSpots(page, request, 'touch')
  await fingerTap(page, a!)
  expect(await names(page)).toEqual([a!.name])
  await expect(chip(page)).toHaveText('1 face')
  const before = (await settled(page)).camera
  await sendPointer(page, [
    { phase: 'down', kind: 'touch', id: 1, x: a!.x, y: a!.y },
    ...[1, 2, 3, 4, 5, 6, 7, 8].map((i) => ({ phase: 'move' as const, kind: 'touch' as const, id: 1, x: a!.x + 5 * i, y: a!.y })),
    { phase: 'up', kind: 'touch', id: 1, x: a!.x + 40, y: a!.y },
  ])
  const after = (await settled(page)).camera
  expect(after.back).not.toEqual(before.back)
  expect(await names(page)).toEqual([a!.name]) // the drag neither selected nor cleared
})

test('Multi: two picks select both, a release over empty space changes nothing, Clear empties', async ({ page, request }) => {
  await openApp(page)
  const [a, b] = await faceSpots(page, request, 'pen')
  const empty = await emptySpot(page, 'pen')
  await multi(page).click()
  await penPick(page, a!)
  await penPick(page, b!)
  expect(await names(page)).toEqual([a!.name, b!.name])
  await expect(chip(page)).toHaveText('2 faces')
  await penDown(page, a!)
  await penMove(page, empty)
  await penUp(page, empty)
  expect(await names(page)).toEqual([a!.name, b!.name])
  await expect(chip(page)).toHaveText('2 faces')
  await penPick(page, a!)
  expect(await names(page)).toEqual([b!.name])
  await page.getByTestId('selection-clear').click()
  expect((await selection(page)).count).toBe(0)
  await expect(chip(page)).toHaveText('Nothing selected')
  // turning Multi off with several selected keeps them until the next Commit
  await penPick(page, a!)
  await penPick(page, b!)
  await multi(page).click()
  expect((await selection(page)).count).toBe(2)
  await penPick(page, a!)
  expect(await names(page)).toEqual([a!.name])
})

test('a pointercancel never commits and never clears', async ({ page, request }) => {
  await openApp(page)
  const [a, b] = await faceSpots(page, request, 'pen')
  await penPick(page, b!)
  await penDown(page, a!)
  expect((await selection(page)).aimed).not.toBeNull()
  await penCancel(page, a!)
  const s = await selection(page)
  expect(s.aimed).toBeNull()
  expect(s.items.map((i) => i.name)).toEqual([b!.name])
})

test('the view is drawn again when the Aim changes item, and not when it moves within one', async ({ page, request }) => {
  await openApp(page)
  const [a, b] = await faceSpots(page, request, 'pen')
  await penDown(page, a!)
  const first = await settled(page)
  // six more positions on the same face
  const near = Array.from({ length: 12 }, (_, i) => ({ x: a!.x + ((i % 4) - 2) * 2, y: a!.y + (Math.floor(i / 4) - 1) * 2 }))
  const onA = (await pickAll(page, near, 'pen')).flatMap((h, i) => (h?.kind === 'face' && h.name === a!.name ? [near[i]!] : []))
  expect(onA.length).toBeGreaterThan(5)
  for (const p of onA) await penMove(page, p)
  const same = await settled(page)
  expect(same.frames).toBe(first.frames)
  expect(same.selection.looks).toBe(first.selection.looks)
  await penMove(page, b!)
  const next = await settled(page)
  expect(next.selection.looks).toBe(first.selection.looks + 1)
  expect(next.frames).toBe(first.frames + 1)
  await penCancel(page, b!)
})

test('the mouse highlights on a move and Commits on a click', async ({ page, request }) => {
  await openApp(page)
  const [a] = await faceSpots(page, request, 'mouse')
  await sendPointer(page, [{ phase: 'move', kind: 'mouse', id: 20, ...a! }])
  expect((await selection(page)).aimed).toMatchObject({ name: a!.name })
  expect((await selection(page)).count).toBe(0)
  await sendPointer(page, [
    { phase: 'down', kind: 'mouse', id: 20, ...a!, button: 0 },
    { phase: 'up', kind: 'mouse', id: 20, ...a!, button: 0 },
  ])
  expect(await names(page)).toEqual([a!.name])
})

// --- gate (I4.4) ---

test('single mode: a pen down on the model and a lift over empty space clears a selected face', async ({ page, request }) => {
  await openApp(page)
  const [a, b] = await faceSpots(page, request, 'pen')
  const empty = await emptySpot(page, 'pen')
  await penPick(page, a!)
  expect(await names(page)).toEqual([a!.name])
  await penDown(page, b!)
  expect((await selection(page)).aimed).toMatchObject({ name: b!.name })
  await penMove(page, empty)
  await penUp(page, empty)
  expect((await selection(page)).count).toBe(0)
  await expect(chip(page)).toHaveText('Nothing selected')
})

test('Multi with two faces selected: down on the model and a lift over empty space leaves both, and Clear empties', async ({ page, request }) => {
  await openApp(page)
  const [a, b] = await faceSpots(page, request, 'pen')
  const empty = await emptySpot(page, 'pen')
  await multi(page).click()
  await penPick(page, a!)
  await penPick(page, b!)
  await penDown(page, a!)
  await penMove(page, empty)
  await penUp(page, empty)
  expect(await names(page)).toEqual([a!.name, b!.name])
  await page.getByTestId('selection-clear').click()
  expect((await selection(page)).count).toBe(0)
})

test('a finger tap on an edge selects the edge; a finger drag from it selects nothing', async ({ page, request }) => {
  await openApp(page)
  const e = await edgeSpot(page, request, 'touch')
  await fingerTap(page, e)
  expect((await selection(page)).items).toMatchObject([{ kind: 'edge', name: e.name, index: e.edge }])
  await expect(chip(page)).toHaveText('1 edge')
  await page.getByTestId('selection-clear').click()
  expect((await selection(page)).count).toBe(0)
  await sendPointer(page, [
    { phase: 'down', kind: 'touch', id: 2, x: e.x, y: e.y },
    ...[1, 2, 3, 4, 5, 6, 7, 8].map((i) => ({ phase: 'move' as const, kind: 'touch' as const, id: 2, x: e.x + 5 * i, y: e.y })),
    { phase: 'up', kind: 'touch', id: 2, x: e.x + 40, y: e.y },
  ])
  await settled(page)
  expect((await selection(page)).count).toBe(0)
  await expect(chip(page)).toHaveText('Nothing selected')
})

test("the selected face's reference from the refs route carries the Element name the view showed", async ({ page, request }) => {
  await openApp(page)
  const [a] = await faceSpots(page, request, 'pen')
  await penPick(page, a!)
  const item = (await selection(page)).items[0]!
  expect(item.kind).toBe('face')
  const doc = await idOf(request, 'Bracket')
  const tab = ((await (await request.get(`/documents/${doc}/tabs`)).json()).tabs[0] as { id: string }).id
  // the client counts from 0, the route from 1
  const res = await request.post(`/documents/${doc}/tabs/${tab}/refs`, { data: { picks: [{ part: item.part, kind: 'face', index: item.index + 1, want: ['face'] }] } })
  expect(res.status()).toBe(200)
  const [got] = (await res.json()).items as { ok: boolean; kind: string; name: string; ref: { name?: string } }[]
  expect(got).toMatchObject({ ok: true, kind: 'face', name: item.name })
  expect(got!.ref.name).toBe(item.name)
})
