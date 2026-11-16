import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { fingerTap, sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { openNewSketch, openSketchFeature, readSketch, screenOf, settledSketch, uvAt } from './support/sketch.ts'

// Slice S7, Trim, Corner fillet and Use edge (card I7.12) against `freeshape serve`: a tap with Trim on a line removes it, a tap on a
// corner with Corner fillet opens the radius on the pad and Enter rounds it, Use edge brings a model edge into a new sketch on the Top
// plane. The pen's Aim (a press without a lift) shows what a Commit would do. Nothing is committed: every test ends with the sketch
// cancelled or the Document closed.

test.afterEach(async ({ page }) => closeAll(page.request))

type Msg = Record<string, unknown>
function wire(page: Page) {
  const sent: Msg[] = []
  const got: Msg[] = []
  const take = (payload: string | Buffer, into: Msg[]) => {
    if (typeof payload !== 'string') return
    try {
      const m = JSON.parse(payload) as Msg
      if (typeof m['type'] === 'string' && m['type'].startsWith('sketch.')) into.push(m)
    } catch {
      // not JSON
    }
  }
  page.on('websocket', (ws) => {
    ws.on('framesent', (f) => take(f.payload, sent))
    ws.on('framereceived', (f) => take(f.payload, got))
  })
  return { sent, got }
}

const tool = async (page: Page, group: string, id: string) => {
  await page.locator('.tool-strip').getByRole('button', { name: new RegExp(`^${group}`) }).click()
  await page.locator(`.group-row[data-tool="${id}"]`).click()
}
const lines = (page: Page) => page.locator('.sketch-layer path[data-kind="line"]').count()

/** The screen position of a sketch point (u, v in mm), read off the view as the drawing spec does. */
async function screenFor(page: Page, u: number, v: number): Promise<Point> {
  const o = await screenOf(page, 'point', 'origin')
  const a = (await uvAt(page, o.x + 100, o.y))!
  const b = (await uvAt(page, o.x, o.y + 100))!
  const m = [
    [a[0] / 100, b[0] / 100],
    [a[1] / 100, b[1] / 100],
  ]
  const det = m[0]![0]! * m[1]![1]! - m[0]![1]! * m[1]![0]!
  return { x: o.x + (m[1]![1]! * u - m[0]![1]! * v) / det, y: o.y + (-m[1]![0]! * u + m[0]![0]! * v) / det }
}

type Opened = { sketch: { points: Record<string, [number, number]>; entities: Record<string, { type: string; p1?: string; p2?: string }> }; edges: { pick: unknown; points: [number, number][] }[] }
const opened = (w: ReturnType<typeof wire>): Opened => w.got.find((m) => m['type'] === 'sketch.opened') as unknown as Opened

test('Trim: Aim shows the piece, a tap on a line sends sketch.trim and the line is gone', async ({ page }) => {
  const w = wire(page)
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  const before = await lines(page)
  await tool(page, 'Draw', 'trim')
  await expect.poll(async () => (await readSketch(page)).draw.tool).toBe('trim')
  await expect(page.getByTestId('edit-hint')).toContainText('Trim')
  await settledSketch(page)
  const sk = opened(w).sketch
  const id = Object.keys(sk.entities).find((k) => sk.entities[k]!.type === 'line')!
  const a = await screenOf(page, 'point', sk.entities[id]!.p1!)
  const b = await screenOf(page, 'point', sk.entities[id]!.p2!)
  const at = { x: a.x + (b.x - a.x) * 0.3, y: a.y + (b.y - a.y) * 0.3 }
  // the pen presses and slides: the piece a Commit would remove is marked, nothing is sent
  await sendPointer(page, [
    { phase: 'down', kind: 'pen', id: 10, x: at.x - 4, y: at.y },
    { phase: 'move', kind: 'pen', id: 10, ...at },
  ])
  await expect(page.locator('.sketch-layer g[data-preview="trim"]')).toBeAttached()
  expect(w.sent.filter((m) => m['type'] === 'sketch.trim')).toHaveLength(0)
  await sendPointer(page, [{ phase: 'up', kind: 'pen', id: 10, ...at, pressure: 0 }])
  await expect.poll(() => w.sent.filter((m) => m['type'] === 'sketch.trim').length).toBe(1)
  const sent = w.sent.find((m) => m['type'] === 'sketch.trim')!
  expect(sent['entity']).toBe(id)
  expect(Array.isArray(sent['at'])).toBe(true)
  await expect.poll(() => lines(page)).toBeLessThan(before)
  await expect(page.locator('.sketch-layer g[data-preview]')).toHaveCount(0)
  await page.getByTestId('sketch-cancel').click()
})

test('Corner fillet: a tap on a corner opens the radius on the pad, 3 and Enter sends sketch.fillet and an arc appears', async ({ page }) => {
  const w = wire(page)
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await tool(page, 'Draw', 'corner_fillet')
  await settledSketch(page)
  const sk = opened(w).sketch
  const users = (pid: string) => Object.values(sk.entities).filter((e) => e.p1 === pid || e.p2 === pid)
  const corner = Object.keys(sk.points).find((p) => p !== 'origin' && users(p).length === 2 && users(p).every((e) => e.type === 'line'))!
  expect(corner).toBeDefined()
  await fingerTap(page, await screenOf(page, 'point', corner))
  await expect(page.getByRole('dialog', { name: 'Corner fillet' })).toBeVisible()
  await expect(page.locator('.pad-field-label')).toHaveText('Radius')
  await page.keyboard.type('3')
  await page.keyboard.press('Enter')
  await expect.poll(() => w.sent.find((m) => m['type'] === 'sketch.fillet')).toEqual({ type: 'sketch.fillet', point: corner, radius: 3 })
  await expect(page.getByTestId('pad')).toHaveCount(0)
  await expect(page.locator('.sketch-layer path[data-kind="arc"]')).toHaveCount(1)
  await page.getByTestId('sketch-cancel').click()
})

test('Corner fillet: a radius that is too large is refused in the words of the server and the pad stays', async ({ page }) => {
  const w = wire(page)
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await tool(page, 'Draw', 'corner_fillet')
  await settledSketch(page)
  const sk = opened(w).sketch
  const users = (pid: string) => Object.values(sk.entities).filter((e) => e.p1 === pid || e.p2 === pid)
  const corner = Object.keys(sk.points).find((p) => p !== 'origin' && users(p).length === 2 && users(p).every((e) => e.type === 'line'))!
  await fingerTap(page, await screenOf(page, 'point', corner))
  await page.getByTestId('pad').waitFor()
  await page.keyboard.type('500')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('fillet-refusal')).toBeVisible()
  await expect(page.getByTestId('pad')).toBeVisible()
  expect(await page.locator('.sketch-layer path[data-kind="arc"]').count()).toBe(0)
  await page.getByTestId('pad').getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(page.getByTestId('pad')).toHaveCount(0)
  await page.getByTestId('sketch-cancel').click()
})

test('Use edge: Aim marks a model edge through the solid and a tap sends sketch.use_edge with its pick', async ({ page }) => {
  const w = wire(page)
  await openApp(page)
  await openNewSketch(page, 'Top')
  await tool(page, 'Draw', 'use_edge')
  await settledSketch(page)
  const edges = opened(w).edges
  expect(edges.length).toBeGreaterThan(0)
  // a straight edge on the plane: the middle of its first segment
  const e = edges.find((x) => x.points.length === 2) ?? edges[0]!
  const mid = { u: (e.points[0]![0] + e.points[1]![0]) / 2, v: (e.points[0]![1] + e.points[1]![1]) / 2 }
  const at = await screenFor(page, mid.u, mid.v)
  await sendPointer(page, [
    { phase: 'down', kind: 'pen', id: 10, x: at.x - 3, y: at.y },
    { phase: 'move', kind: 'pen', id: 10, ...at },
  ])
  await expect(page.locator('.sketch-layer g[data-preview="edge"]')).toBeAttached()
  await sendPointer(page, [{ phase: 'up', kind: 'pen', id: 10, ...at, pressure: 0 }])
  await expect.poll(() => w.sent.find((m) => m['type'] === 'sketch.use_edge')).toBeDefined()
  // the box's top and bottom edges overlap in the plane's view: the tool takes whichever lies nearest, so the pick is one of the edges listed
  expect(edges.map((x) => x.pick)).toContainEqual(w.sent.find((m) => m['type'] === 'sketch.use_edge')!['pick'])
  await expect.poll(() => lines(page)).toBeGreaterThan(0)
  await page.getByTestId('sketch-cancel').click()
})
