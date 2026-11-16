import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { canvasCentre, fingerTap, sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { openNewSketch, readSketch, screenOf, settledSketch, uvAt } from './support/sketch.ts'

// Slice S7, the drawing tools (card I7.8) against `freeshape serve`: a new sketch on the Top plane, drawn with the pen the way
// the iPad does it (a pen down Aims, the pen up Commits) and with the quick menu to choose the tool. Pointer events stand in for
// the Pencil, as in every browser spec. Nothing is committed: every test ends with the Document closed by the next openApp.

test.afterEach(async ({ page }) => closeAll(page.request))

const PEN = 10

/** The screen position of a sketch point (u, v in mm): the plane's axes are read off the view, so it holds in any 2D turn. */
async function screenFor(page: Page, u: number, v: number): Promise<Point> {
  const o = await screenOf(page, 'point', 'origin')
  const a = (await uvAt(page, o.x + 100, o.y))!
  const b = (await uvAt(page, o.x, o.y + 100))!
  // (u, v) = A * (dx, dy) / 100 ; invert A
  const m = [
    [a[0] / 100, b[0] / 100],
    [a[1] / 100, b[1] / 100],
  ]
  const det = m[0]![0]! * m[1]![1]! - m[0]![1]! * m[1]![0]!
  return { x: o.x + (m[1]![1]! * u - m[0]![1]! * v) / det, y: o.y + (-m[1]![0]! * u + m[0]![0]! * v) / det }
}

const down = (p: Point) => ({ phase: 'down' as const, kind: 'pen' as const, id: PEN, ...p })
const move = (p: Point) => ({ phase: 'move' as const, kind: 'pen' as const, id: PEN, ...p })
const up = (p: Point) => ({ phase: 'up' as const, kind: 'pen' as const, id: PEN, ...p, pressure: 0 })

/** The pen puts a point down: press, a short slide, lift. */
const place = (page: Page, p: Point) => sendPointer(page, [down({ x: p.x - 3, y: p.y }), move(p), up(p)])

/** Choose a tool with the quick menu: hold the pen at the middle of the view, slide towards the button, lift on it. */
async function chooseWithRing(page: Page, id: string): Promise<void> {
  const c = await canvasCentre(page)
  await sendPointer(page, [down(c)])
  await expect(page.getByTestId('quick-line')).toBeVisible()
  const b = (await page.getByTestId(`quick-${id}`).boundingBox())!
  const box = (await page.getByTestId('viewport').boundingBox())!
  const to = { x: b.x + b.width / 2 - box.x, y: b.y + b.height / 2 - box.y }
  await sendPointer(page, [move({ x: (c.x + to.x) / 2, y: (c.y + to.y) / 2 }), move(to), up(to)])
  await expect(page.getByTestId('quick-menu')).toHaveCount(0)
}

const tool = (page: Page) => expect.poll(async () => (await readSketch(page)).draw.tool)
const stats = async (page: Page) => (await settledSketch(page)).stats

test('the quick menu opens on a hold, never while panning, and its release places no point', async ({ page }) => {
  await openApp(page)
  await openNewSketch(page, 'Top')
  const c = await canvasCentre(page)
  // a finger that moves sooner pans: no ring
  await sendPointer(page, [
    { phase: 'down', kind: 'touch', id: 1, ...c },
    { phase: 'move', kind: 'touch', id: 1, x: c.x + 40, y: c.y },
    { phase: 'up', kind: 'touch', id: 1, x: c.x + 40, y: c.y },
  ])
  await expect(page.getByTestId('quick-menu')).toHaveCount(0)
  // a deliberate hold opens it with all eight buttons on screen; Select is on, so the pen would select
  await sendPointer(page, [down({ x: 30, y: 30 })])
  await expect(page.getByTestId('quick-line')).toBeVisible()
  const view = (await page.getByTestId('viewport').boundingBox())!
  const boxes = await page.locator('.quick-menu button').evaluateAll((els) => els.map((e) => e.getBoundingClientRect().toJSON()))
  expect(boxes).toHaveLength(8)
  for (const b of boxes) {
    expect(b.left).toBeGreaterThanOrEqual(view.x - 0.5)
    expect(b.top).toBeGreaterThanOrEqual(view.y - 0.5)
    expect(b.right).toBeLessThanOrEqual(view.x + view.width + 0.5)
    expect(b.bottom).toBeLessThanOrEqual(view.y + view.height + 0.5)
  }
  await sendPointer(page, [up({ x: 30, y: 30 })])
  await expect(page.getByTestId('quick-line')).toBeVisible() // released without sliding: it stays for a tap
  await expect(page.getByTestId('quick-construct')).toHaveAttribute('aria-disabled', 'true') // a later card builds Construct
  await expect(page.getByTestId('quick-line')).not.toHaveAttribute('aria-disabled', 'true')
  await page.getByTestId('quick-circle').click()
  await tool(page).toBe('circle')
  await expect(page.getByTestId('quick-menu')).toHaveCount(0)
  expect((await settledSketch(page)).draw.picks).toHaveLength(0)
})

test('a line from two pen Commits: the preview shows the snap, the points land where it showed, and a line close to level is made level', async ({ page }) => {
  await openApp(page)
  await openNewSketch(page, 'Top')
  await chooseWithRing(page, 'line')
  await tool(page).toBe('line')
  const a = await screenFor(page, -30, 10)
  await place(page, a)
  await expect.poll(async () => (await settledSketch(page)).draw.picks).toHaveLength(1)
  // Aim 2 degrees off level: the preview says level, and the mark is on the level
  const to = await screenFor(page, 20, 10 + 50 * Math.tan((2 * Math.PI) / 180))
  await sendPointer(page, [down({ x: to.x - 20, y: to.y }), move(to)])
  const aim = (await settledSketch(page)).draw.aim!
  expect(aim.infer).toBe('H')
  await expect(page.locator('[data-kind="aim"][data-infer="H"]')).toBeVisible()
  const shown = aim.at
  await sendPointer(page, [up(to)])
  await expect.poll(async () => (await stats(page)).lines).toBe(1)
  // the line's end point is where the preview's mark was; both ends are at the same height (the server solved the horizontal)
  await expect(page.locator('.sketch-layer circle[data-kind="point"]')).toHaveCount(3)
  const ends = await page.locator('.sketch-layer circle[data-kind="point"]').evaluateAll((els) => els.map((e) => e.getAttribute('data-id')!).filter((id) => id !== 'origin'))
  expect(ends).toHaveLength(2)
  const [p0, p1] = await Promise.all(ends.map((id) => screenOf(page, 'point', id)))
  const end = Math.hypot(p0!.x - shown.x, p0!.y - shown.y) < Math.hypot(p1!.x - shown.x, p1!.y - shown.y) ? p0! : p1!
  expect(Math.hypot(end.x - shown.x, end.y - shown.y)).toBeLessThan(1)
  const v0 = (await uvAt(page, p0!.x, p0!.y))![1]
  const v1 = (await uvAt(page, p1!.x, p1!.y))![1]
  expect(Math.abs(v0 - v1)).toBeLessThan(1e-6)
  expect((await settledSketch(page)).stats.glyphs).toBeGreaterThan(0) // the horizontal constraint's glyph
})

test('a line that chains ends on a point it meets: the points are joined, not doubled', async ({ page }) => {
  await openApp(page)
  await openNewSketch(page, 'Top')
  await chooseWithRing(page, 'line')
  const a = await screenFor(page, -30, 10)
  const b = await screenFor(page, 20, 30)
  const c = await screenFor(page, 40, -20)
  await place(page, a)
  await place(page, b)
  await expect.poll(async () => (await stats(page)).lines).toBe(1)
  await place(page, c)
  await expect.poll(async () => (await stats(page)).lines).toBe(2)
  // back to the first point, a little off: it snaps there (a coincident), closing the triangle without a new point
  const near = { x: a.x + 4, y: a.y - 3 }
  await sendPointer(page, [down({ x: near.x + 20, y: near.y }), move(near)])
  expect((await settledSketch(page)).draw.aim!.snap).toBe('point')
  await sendPointer(page, [up(near)])
  await expect.poll(async () => (await stats(page)).lines).toBe(3)
  await expect(page.locator('.sketch-layer circle[data-kind="point"]')).toHaveCount(4) // the origin and the triangle's three corners
  await expect.poll(async () => (await settledSketch(page)).draw.picks).toHaveLength(0) // closing on a point ends the chain
})

test('rectangle, circle, arc and point each make their shape', async ({ page }) => {
  await openApp(page)
  await openNewSketch(page, 'Top')
  await chooseWithRing(page, 'rectangle')
  await place(page, await screenFor(page, -30, -10))
  await place(page, await screenFor(page, -10, 10))
  await expect.poll(async () => (await stats(page)).lines).toBe(4)
  await chooseWithRing(page, 'circle')
  await place(page, await screenFor(page, 15, 15))
  await place(page, await screenFor(page, 23, 15))
  await expect.poll(async () => (await stats(page)).circles).toBe(1)
  await chooseWithRing(page, 'arc')
  await place(page, await screenFor(page, -10, -20))
  await place(page, await screenFor(page, 20, -20))
  await place(page, await screenFor(page, 5, -28))
  await expect.poll(async () => (await stats(page)).arcs).toBe(1)
  await chooseWithRing(page, 'point')
  const before = await page.locator('.sketch-layer circle[data-kind="point"]').count()
  await place(page, await screenFor(page, 28, -15))
  await expect(page.locator('.sketch-layer [data-kind="point"]')).not.toHaveCount(before)
})

test('a finger never places a point, and a pen lifted far outside the view drops the point', async ({ page }) => {
  await openApp(page)
  await openNewSketch(page, 'Top')
  await chooseWithRing(page, 'line')
  const a = await screenFor(page, -30, 10)
  await fingerTap(page, a)
  expect((await settledSketch(page)).draw.picks).toHaveLength(0)
  await place(page, a)
  await expect.poll(async () => (await settledSketch(page)).draw.picks).toHaveLength(1)
  await sendPointer(page, [down(await screenFor(page, 20, 30)), up({ x: -200, y: -200 })])
  const s = await settledSketch(page)
  expect(s.draw.picks).toHaveLength(1)
  expect(s.draw.aim).toBeNull()
  expect(s.stats.lines).toBe(0)
})

test('the Draw group on the strip has its five tools lit, tapping one turns it on, and tapping Rectangle again makes it a centre rectangle', async ({ page }) => {
  await openApp(page)
  await openNewSketch(page, 'Top')
  await page.locator('.tool-strip').getByRole('button', { name: /^Draw/ }).click()
  const rows = page.locator('.group-row')
  await expect(rows).toHaveCount(5)
  for (const id of ['line', 'rectangle', 'circle', 'arc', 'point']) await expect(page.locator(`.group-row[data-tool="${id}"]`)).toHaveAttribute('data-state', 'ok')
  await page.locator('.group-row[data-tool="rectangle"]').click()
  await tool(page).toBe('rectangle')
  await expect(page.getByTestId('aim-hint')).toContainText('Rectangle')
  await page.locator('.tool-strip').getByRole('button', { name: /^Draw/ }).click()
  await page.locator('.group-row[data-tool="rectangle"]').click()
  await tool(page).toBe('center_rectangle')
  await expect(page.getByTestId('aim-hint')).toContainText('Centre rectangle')
})
