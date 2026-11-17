import { expect, test } from '@playwright/test'
import type { Page, WebSocketRoute } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, idOf, measureTab, removeDocument } from './support/docs.ts'
import { featureMenu } from './support/features.ts'
import { chooseWithRing, place, regionSpot, screenFor } from './support/draw.ts'
import { pickAll, project } from './support/pick.ts'
import type { P3 } from './support/pick.ts'
import { canvasCentre, fingerDrag, fingerTap, penStroke, sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { handleOf, openNewSketch, readSketch, screenOf, settledSketch, uvAt } from './support/sketch.ts'
import { readViewport, settled } from './support/viewport.ts'

// Slice S7 gate (card I7.14), the milestone's scripted proof: a part made from nothing in the browser, against `freeshape
// serve`. From the home screen a Document is created; a sketch on the Top plane is drawn with the pen (aim and release),
// dimensioned on the pad and finished with Done; its region is picked with the pen, extruded and filleted; the volume the
// kernel measures is the one the numbers give. The sketch is then reopened and moved by the pen and by a finger; the view is
// panned, orbited and squared again; a socket that drops in the middle of a sketch brings it back. Pointer events stand in for
// the Pencil and the finger, as in every browser spec.

const NAME = 'Part from nothing'
const PEN = 10

test.beforeEach(() => removeDocument(NAME))
test.afterEach(async ({ page }) => {
  await closeAll(page.request)
  removeDocument(NAME)
})

type Msg = Record<string, unknown>
type Uv = [number, number]
interface Sketch {
  points: Record<string, Uv>
  entities: Record<string, { type: string; p1?: string; p2?: string; construction?: boolean }>
}

/**
 * The page's socket goes through a proxy the spec holds, so it can read every sketch.* message either way and close the
 * socket from outside. Call it before the page loads.
 */
async function socketLog(page: Page) {
  const sent: Msg[] = []
  const got: Msg[] = []
  const routes: WebSocketRoute[] = []
  const take = (payload: string | Buffer, into: Msg[]) => {
    if (typeof payload !== 'string') return
    try {
      const m = JSON.parse(payload) as Msg
      if (typeof m['type'] === 'string' && m['type'].startsWith('sketch.')) into.push(m)
    } catch {
      // not JSON
    }
  }
  await page.routeWebSocket(/\/ws\?/, (ws) => {
    const server = ws.connectToServer()
    routes.push(ws, server as unknown as WebSocketRoute)
    ws.onMessage((m) => {
      take(m, sent)
      server.send(m)
    })
    server.onMessage((m) => {
      take(m, got)
      ws.send(m)
    })
  })
  return {
    sent,
    got,
    /** The sketch as the server last gave it. */
    sketch: (): Sketch => {
      const m = [...got].reverse().find((x) => typeof x['sketch'] === 'object' && x['sketch'] !== null)
      if (!m) throw new Error('the server has sent no sketch yet')
      return m['sketch'] as Sketch
    },
    /** Close the page's socket and the proxy's one to the server. */
    drop: async () => {
      for (const r of routes.splice(0)) await r.close()
    },
  }
}

const labels = (page: Page) => page.locator('.sketch-layer g[data-kind="dimension"] text').evaluateAll((els) => els.map((e) => e.textContent ?? ''))
const features = (page: Page) => page.locator('.panel-row[data-feature]')
const lineIds = (s: Sketch) => Object.keys(s.entities).filter((k) => s.entities[k]!.type === 'line' && !s.entities[k]!.construction)
const uvOf = (s: Sketch, id: string) => [s.points[s.entities[id]!.p1!]!, s.points[s.entities[id]!.p2!]!] as const
const dist = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y)
const key = async (page: Page, ...names: string[]) => {
  for (const n of names) await page.getByTestId('pad').getByRole('button', { name: n, exact: true }).click()
}

async function penPick(page: Page, at: Point): Promise<void> {
  await sendPointer(page, [{ phase: 'down', kind: 'pen', id: PEN, ...at }])
  await sendPointer(page, [{ phase: 'up', kind: 'pen', id: PEN, ...at, pressure: 0 }])
}

/** A spot a fraction of the way along a line of the sketch, in the view as it is now. */
async function onLine(page: Page, s: Sketch, id: string, t: number): Promise<Point> {
  const [a, b] = uvOf(s, id)
  return screenFor(page, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
}

/** Dimension a line with the pad: a finger taps the line, then where the label goes, the number is typed, Enter. */
async function dimensionLine(page: Page, line: Point, label: Point, value: string): Promise<void> {
  await fingerTap(page, line)
  await expect(page.getByTestId('dimension-hint')).toContainText('Tap where the dimension goes')
  await fingerTap(page, label)
  await expect(page.getByTestId('pad')).toBeVisible()
  await page.keyboard.type(value)
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('pad')).toHaveCount(0)
}

test('from the home screen to a filleted solid: create, sketch with the pen, dimension, Done, extrude, fillet; then move the sketch by pen and finger', async ({ page }) => {
  test.setTimeout(300_000)
  const sock = await socketLog(page)
  await closeAll(page.request)
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()

  // a Document with its Part Studio: nothing in it yet
  await page.getByRole('button', { name: 'New Document' }).click()
  await page.getByRole('textbox', { name: 'Name' }).fill(NAME)
  await page.getByRole('button', { name: 'Create' }).click()
  await expect(page.getByTestId('shell')).toBeVisible()
  await expect(page.getByTestId('document-name')).toHaveText(NAME)
  await expect(page.getByTestId('link-status')).toHaveText('Connected', { timeout: 30_000 })
  await expect(page.locator('.tab-button')).toHaveCount(1)
  await page.waitForFunction(() => window.__freeshapeViewport !== undefined)
  await expect(features(page)).toHaveCount(0)
  expect((await readViewport(page)).parts).toBe(0)
  const tabs = (await (await page.request.get(`/documents/${await idOf(page.request, NAME)}/tabs`)).json()) as { tabs: { id: string; kind?: string }[] }
  console.log(`tabs: ${JSON.stringify(tabs.tabs)}`)
  expect(tabs.tabs).toHaveLength(1)
  const tab = tabs.tabs[0]!.id

  // a sketch on the Top plane, asked for because nothing is selected
  await page.locator('[data-tool="sketch"]').click()
  await page.getByRole('dialog', { name: 'Sketch on which plane?' }).getByRole('button', { name: 'Top', exact: true }).click()
  await expect(page.locator('[data-strip="sketch"]')).toBeVisible({ timeout: 20_000 })
  await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.paints ?? 0) > 0)

  // a rectangle with the pen, an aim and a release at each corner: 16 x 8 mm (the empty sketch shows about 35 mm across), away from the origin so it is free to move
  await chooseWithRing(page, 'rectangle')
  await place(page, await screenFor(page, -10, -5))
  await place(page, await screenFor(page, 6, 3))
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(4)

  // two dimensions on the pad turn it into 20 x 10
  await page.locator('[data-strip="sketch"] [data-tool="dimension"]').click()
  await expect(page.getByTestId('dimension-hint')).toContainText('Pick a line')
  await settledSketch(page)
  const bottom = lineIds(sock.sketch()).find((id) => {
    const [a, b] = uvOf(sock.sketch(), id)
    return Math.abs(a[1] - b[1]) < 1e-6 && Math.abs(a[1] + 5) < 1e-6
  })!
  expect(bottom).toBeDefined()
  const at = await onLine(page, sock.sketch(), bottom, 0.25)
  await dimensionLine(page, at, { x: at.x, y: at.y + 70 }, '20')
  await expect.poll(() => labels(page)).toEqual(['20 mm'])
  await settledSketch(page)
  const right = lineIds(sock.sketch()).find((id) => {
    const [a, b] = uvOf(sock.sketch(), id)
    return Math.abs(a[0] - b[0]) < 1e-6 && a[0] > 0
  })!
  expect(right).toBeDefined()
  const at2 = await onLine(page, sock.sketch(), right, 0.25)
  await dimensionLine(page, at2, { x: at2.x + 70, y: at2.y }, '10')
  await expect.poll(async () => (await labels(page)).sort()).toEqual(['10 mm', '20 mm'])
  const drawn = sock.sketch()
  const us = Object.entries(drawn.points).filter(([id]) => id !== 'origin').map(([, p]) => p)
  const box = { u0: Math.min(...us.map((p) => p[0])), u1: Math.max(...us.map((p) => p[0])), v0: Math.min(...us.map((p) => p[1])), v1: Math.max(...us.map((p) => p[1])) }
  expect(box.u1 - box.u0).toBeCloseTo(20, 6)
  expect(box.v1 - box.v0).toBeCloseTo(10, 6)

  // Done: back in the Part Studio with a sketch feature and a region to pick
  const rev = (await readViewport(page)).revision ?? 0
  await page.getByTestId('sketch-done').click()
  await expect(page.locator('[data-strip="feature"]')).toBeVisible({ timeout: 20_000 })
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r, rev)
  await expect(features(page)).toHaveCount(1)

  // the region is picked with the pen and extruded 10 mm
  const inner = { u0: box.u0 + 3, u1: box.u1 - 3, v0: box.v0 + 2, v1: box.v1 - 2 }
  const spot = await regionSpot(page, inner)
  await penPick(page, spot)
  await expect(page.getByTestId('selection-count')).toHaveText('1 region')
  await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
  await page.getByRole('menuitem', { name: /^Extrude/ }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByTestId('dialog-form')).toContainText(/Region 1 of Sketch/)
  await key(page, '1', '0', 'Enter')
  await expect(page.getByRole('button', { name: /^Depth, 10 mm/ })).toBeVisible()
  await expect.poll(async () => (await readViewport(page)).preview, { timeout: 15_000 }).not.toBeNull()
  const rev2 = (await readViewport(page)).revision!
  await dialog.getByRole('button', { name: 'Accept', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r && window.__freeshapeViewport?.preview === null, rev2)
  await expect(features(page)).toHaveCount(2)
  await page.waitForFunction(() => (window.__freeshapeViewport?.parts ?? 0) === 1)
  let measured = await measureTab(page.request, NAME, tab)
  expect(measured).toHaveLength(1)
  expect(Math.abs(measured[0]!.volume - 2000) / 2000).toBeLessThan(1e-6)
  expect(measured[0]!.faces).toBe(6)

  // the view is still square to the sketch plane, where a vertical edge is a point: one finger orbits it, then an edge is picked with the pen,
  // the nearest vertical edge the view picks, and filleted 2 mm
  const centre = await canvasCentre(page)
  await fingerDrag(page, centre, { x: centre.x + 70, y: centre.y - 90 })
  const cam = (await settled(page)).camera
  const vertical = measured[0]!.edges
    .filter((e) => Math.abs(Math.abs(e.direction[2]) - 1) < 1e-9)
    .map((e) => ({ e, at: project(cam, e.centre as P3) }))
    .sort((a, b) => b.at.z - a.at.z)
  expect(vertical).toHaveLength(4)
  const hits = await pickAll(page, vertical.map((v) => v.at), 'pen')
  const chosen = vertical.find((v, i) => hits[i]?.kind === 'edge' && hits[i]?.index === v.e.index - 1) // the view counts from 0, the kernel's edges from 1
  expect(chosen, 'the pen picks at least one vertical edge at its middle').toBeDefined()
  expect(chosen!.e.length).toBeCloseTo(10, 6)
  await penPick(page, chosen!.at)
  await expect(page.getByTestId('selection-count')).toHaveText('1 edge')
  await page.locator('.tool-strip').getByRole('button', { name: /^Modify/ }).click()
  await page.getByRole('menu', { name: 'Modify' }).getByRole('menuitem', { name: /^Fillet/ }).click()
  await expect(page.getByRole('dialog').getByTestId('dialog-form')).toContainText(/Edge \d+ of /)
  await key(page, 'Backspace', '2', 'Enter')
  await expect(page.getByRole('button', { name: /^Radius, 2 mm/ })).toBeVisible()
  await page.waitForFunction(() => (window.__freeshapeViewport?.preview ?? 0) >= 1)
  const rev3 = (await readViewport(page)).revision!
  await page.locator('.dialog-header').getByRole('button', { name: 'Accept', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r && window.__freeshapeViewport?.preview === null, rev3)
  await expect(features(page)).toHaveCount(3)

  // one part, and the volume the kernel measures is the block (20 x 10 x 10) less the corner's share: 10 * (4 - pi)
  await page.waitForFunction(() => (window.__freeshapeViewport?.faces ?? 0) === 7)
  expect((await readViewport(page)).parts).toBe(1)
  measured = await measureTab(page.request, NAME, tab)
  expect(measured).toHaveLength(1)
  const want = 2000 - (4 - Math.PI) * 10
  console.log(`volume ${measured[0]!.volume}, wanted ${want}`)
  expect(Math.abs(measured[0]!.volume - want) / want).toBeLessThan(1e-6)
  expect(measured[0]!.faces).toBe(7)

  // reopen the sketch: Edit on its row
  await featureMenu(page, /Sketch 1/, 'Edit')
  await expect(page.locator('[data-strip="sketch"]')).toBeVisible({ timeout: 20_000 })
  await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.paints ?? 0) > 0)
  await expect.poll(() => labels(page)).toHaveLength(2)
  await settledSketch(page)
  const sk = sock.sketch()
  const sides = lineIds(sk)
  expect(sides).toHaveLength(4)
  const keepsDimensions = async (a: string, b: string, apart: number) => {
    await expect.poll(async () => (await labels(page)).sort()).toEqual(['10 mm', '20 mm'])
    await expect.poll(async () => dist(await screenOf(page, 'point', a), await screenOf(page, 'point', b))).toBeCloseTo(apart, 0)
  }
  // a rectangle that is 20 by 10 and has no other constraint on its place: a corner dragged takes all four with it
  const corner = sk.entities[sides[0]!]!.p1!
  const next = sk.entities[sides[0]!]!.p2!
  const apart = dist(await screenOf(page, 'point', corner), await screenOf(page, 'point', next))

  // a corner dragged directly with the pen
  const c0 = await screenOf(page, 'point', corner)
  const n0 = await screenOf(page, 'point', next)
  await penStroke(page, c0, { x: c0.x + 30, y: c0.y + 20 }, { moves: 10 })
  await expect.poll(async () => (await screenOf(page, 'point', corner)).x).toBeGreaterThan(c0.x + 15)
  // the other corner follows only when the server's solve comes back
  await expect.poll(async () => (await screenOf(page, 'point', next)).x - n0.x).toBeCloseTo(30, 0)
  await settledSketch(page)
  const c1 = await screenOf(page, 'point', corner)
  const n1 = await screenOf(page, 'point', next)
  expect(c1.x - c0.x).toBeCloseTo(30, 0)
  expect(c1.y - c0.y).toBeCloseTo(20, 0)
  expect(n1.x - n0.x).toBeCloseTo(30, 0)
  expect(n1.y - n0.y).toBeCloseTo(20, 0)
  await keepsDimensions(corner, next, apart)

  // the same corner by its Drag handle, with a finger
  await fingerTap(page, c1)
  await expect.poll(async () => (await readSketch(page)).selected).toEqual([{ kind: 'point', id: corner }])
  await settledSketch(page)
  await expect.poll(() => handleOf(page, 'point', corner)).not.toBeNull()
  const h = await handleOf(page, 'point', corner)
  await fingerDrag(page, h!, { x: h!.x - 25, y: h!.y + 15 }, { moves: 10 })
  await expect.poll(async () => (await screenOf(page, 'point', corner)).x).toBeLessThan(c1.x - 12)
  await settledSketch(page)
  const c2 = await screenOf(page, 'point', corner)
  expect(c2.x - c1.x).toBeCloseTo(-25, 0)
  expect(c2.y - c1.y).toBeCloseTo(15, 0)
  await keepsDimensions(corner, next, apart)

  // a side dragged directly with the pen: the top side (the one with the larger v), both of its end points together
  const top = sides.find((id) => {
    const [a, b] = uvOf(sock.sketch(), id)
    return Math.abs(a[1] - b[1]) < 1e-6 && a[1] > (box.v0 + box.v1) / 2
  })!
  expect(top).toBeDefined()
  const [t1, t2] = [sk.entities[top]!.p1!, sk.entities[top]!.p2!]
  const s1 = await screenOf(page, 'point', t1)
  const s2 = await screenOf(page, 'point', t2)
  const grip = { x: s1.x + (s2.x - s1.x) * 0.25, y: s1.y + (s2.y - s1.y) * 0.25 }
  await fingerTap(page, { x: 8, y: (await page.getByTestId('viewport').boundingBox())!.height - 8 })
  await penStroke(page, grip, { x: grip.x - 20, y: grip.y + 35 }, { moves: 10 })
  await expect.poll(async () => (await screenOf(page, 'point', t1)).y).toBeGreaterThan(s1.y + 15)
  // the other end of the side follows only when the server's solve comes back
  await expect.poll(async () => (await screenOf(page, 'point', t2)).y - s2.y).toBeCloseTo(35, 0)
  await settledSketch(page)
  const e1 = await screenOf(page, 'point', t1)
  const e2 = await screenOf(page, 'point', t2)
  expect(e1.x - s1.x).toBeCloseTo(-20, 0)
  expect(e1.y - s1.y).toBeCloseTo(35, 0)
  expect(e2.x - s2.x).toBeCloseTo(e1.x - s1.x, 0)
  expect(e2.y - s2.y).toBeCloseTo(e1.y - s1.y, 0)
  await keepsDimensions(corner, next, apart)

  // the changes are given up: the Part Studio and its solid are as they were
  await page.getByTestId('sketch-cancel').click()
  await page.getByRole('dialog', { name: 'Discard this sketch?' }).getByRole('button', { name: 'Discard' }).click()
  await expect(page.locator('[data-strip="feature"]')).toBeVisible()
  await expect(features(page)).toHaveCount(3)
  expect(Math.abs((await measureTab(page.request, NAME, tab))[0]!.volume - want) / want).toBeLessThan(1e-6)
})

test('one finger pans an open sketch without tipping it; after the 3D toggle it orbits, a line drawn lies in the sketch plane, and 2D squares the view again', async ({ page }) => {
  const sock = await socketLog(page)
  await openApp(page)
  await openNewSketch(page, 'Top')
  const c = await canvasCentre(page)
  const origin0 = await screenOf(page, 'point', 'origin')
  await fingerDrag(page, c, { x: c.x + 80, y: c.y + 40 })
  const panned = await settledSketch(page)
  const origin1 = await screenOf(page, 'point', 'origin')
  expect(origin1.x - origin0.x).toBeCloseTo(80, 3)
  expect(origin1.y - origin0.y).toBeCloseTo(40, 3)
  expect(panned.nav).toBe('2d')
  expect(panned.tilt).toBeLessThan(1e-6)

  // the same drag in 3D orbits
  await page.getByTestId('nav-3d').click()
  await expect(page.getByTestId('nav-3d')).toHaveAttribute('aria-pressed', 'true')
  await fingerDrag(page, c, { x: c.x + 80, y: c.y - 40 })
  const orbited = await settledSketch(page)
  expect(orbited.nav).toBe('3d')
  expect(orbited.tilt).toBeGreaterThan(0.1)

  // a line drawn with the pen in the tipped view: each end is where the pointer's ray meets the sketch plane
  await chooseWithRing(page, 'line')
  const a = { x: c.x - 120, y: c.y + 60 }
  const b = { x: c.x + 20, y: c.y + 110 }
  const uvA = (await uvAt(page, a.x, a.y))!
  const uvB = (await uvAt(page, b.x, b.y))!
  await place(page, a)
  await place(page, b)
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(1)
  const add = sock.sent.find((m) => m['type'] === 'sketch.add' && m['shape'] === 'line')!
  expect(add).toBeDefined()
  const picks = (add['picks'] as (Uv | { at: Uv })[]).map((p) => (Array.isArray(p) ? p : p.at))
  for (const [got, want] of [[picks[0]!, uvA], [picks[1]!, uvB]] as const) {
    expect(got[0]).toBeCloseTo(want[0], 6)
    expect(got[1]).toBeCloseTo(want[1], 6)
  }
  // and the line the server made is drawn where the pen put it
  const ids = Object.keys(sock.sketch().points).filter((p) => p !== 'origin')
  expect(ids).toHaveLength(2)
  const drawnAt = await Promise.all(ids.map((id) => screenOf(page, 'point', id)))
  const nearest = (p: Point) => Math.min(...drawnAt.map((d) => dist(d, p)))
  expect(nearest(a)).toBeLessThan(1)
  expect(nearest(b)).toBeLessThan(1)

  // 2D squares the view to the plane again
  await page.getByTestId('nav-2d').click()
  await expect.poll(async () => (await settledSketch(page)).nav).toBe('2d')
  await expect.poll(async () => (await settledSketch(page)).tilt).toBeLessThan(1e-6)
})

test('a socket that closes in the middle of a sketch brings back the rectangle that was drawn and not finished', async ({ page }) => {
  const sock = await socketLog(page)
  await openApp(page)
  await openNewSketch(page, 'Top')
  await chooseWithRing(page, 'rectangle')
  await place(page, await screenFor(page, -30, -10))
  await place(page, await screenFor(page, -10, 10))
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(4)
  const marks = { sent: sock.sent.length, got: sock.got.length }
  await sock.drop()
  // the client reconnects with its id, asks the server for the sketch and draws what comes back
  await expect.poll(() => sock.sent.slice(marks.sent).some((m) => m['type'] === 'sketch.state'), { timeout: 30_000 }).toBe(true)
  await expect.poll(() => sock.got.slice(marks.got).some((m) => m['type'] === 'sketch.state'), { timeout: 30_000 }).toBe(true)
  const back = sock.got.slice(marks.got).find((m) => m['type'] === 'sketch.state')!['sketch'] as Sketch
  expect(Object.values(back.entities).filter((e) => e.type === 'line')).toHaveLength(4)
  await expect(page.getByTestId('link-status')).toHaveText('Connected', { timeout: 30_000 })
  await expect(page.locator('[data-strip="sketch"]')).toBeVisible()
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(4)
  await expect(page.locator('.sketch-layer path[data-kind="line"]')).toHaveCount(4)
})
