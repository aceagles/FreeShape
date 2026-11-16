import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { place, screenFor } from './support/draw.ts'
import { desktopMode } from './support/mode.ts'
import { sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { openNewSketch, readSketch } from './support/sketch.ts'

// Slice S14, Mirror in a sketch (card I14.2) against `freeshape serve`: in a new sketch on Top a circle of radius 5 at (30, 10) and a
// vertical construction line through the origin; the circle selected, Mirror chosen, a Commit on the line. The sketch then holds the
// circle's image across the line, a symmetric constraint naming both centres and the line, and an equal naming both circles, and one
// Undo takes all of it away. Once in Tablet mode with the pen and the finger, once in Desktop mode with the mouse. Nothing is
// committed: every test ends with the Document closed.

test.afterEach(async ({ page }) => closeAll(page.request))

type Msg = Record<string, unknown>
interface Sk {
  points: Record<string, [number, number]>
  entities: Record<string, { type: string; p1?: string; p2?: string; c?: string; r?: number; construction?: boolean }>
  constraints: { type: string; refs: string[] }[]
}

/** The sketch messages of the socket, both ways, and the sketch the last answer holds. */
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
  const sketch = (): Sk => [...got].reverse().find((m) => typeof m['sketch'] === 'object')!['sketch'] as Sk
  const circles = () => Object.entries(sketch().entities).filter(([, e]) => e.type === 'circle')
  return { sent, got, sketch, circles }
}

/** What the page holds of the sketch must show the circle, its image, the two constraints and the line; checked against the line the sketch holds. */
function expectMirrored(w: ReturnType<typeof wire>): void {
  const sk = w.sketch()
  const [line] = Object.entries(sk.entities).filter(([, e]) => e.type === 'line')
  const [lid, l] = line!
  expect(l.construction).toBe(true)
  const circles = w.circles()
  expect(circles).toHaveLength(2)
  const [[a, ca], [b, cb]] = circles as [[string, Sk['entities'][string]], [string, Sk['entities'][string]]]
  expect(cb.r).toBeCloseTo(ca.r!, 6)
  expect(ca.r).toBeCloseTo(5, 0)
  const c1 = sk.points[ca.c!]!
  const c2 = sk.points[cb.c!]!
  expect(c1[0]).toBeCloseTo(30, 0)
  expect(c1[1]).toBeCloseTo(10, 0)
  // the image across the line: the midpoint of the centres is on it and the span is square to it
  const p = sk.points[l.p1!]!
  const q = sk.points[l.p2!]!
  const d = [q[0] - p[0], q[1] - p[1]]
  const len = Math.hypot(d[0]!, d[1]!)
  const off = (x: [number, number]) => ((x[0] - p[0]) * d[1]! - (x[1] - p[1]) * d[0]!) / len
  expect(off([(c1[0] + c2[0]) / 2, (c1[1] + c2[1]) / 2])).toBeCloseTo(0, 4)
  expect((c2[0] - c1[0]) * d[0]! + (c2[1] - c1[1]) * d[1]!).toBeCloseTo(0, 4)
  expect(c2[0]).toBeLessThan(0)
  expect(c2[1]).toBeCloseTo(c1[1], 1)
  const sym = sk.constraints.find((c) => c.type === 'symmetric')
  expect(sym?.refs).toEqual(expect.arrayContaining([ca.c!, cb.c!, lid]))
  const eq = sk.constraints.find((c) => c.type === 'equal')
  expect(eq?.refs).toEqual(expect.arrayContaining([a, b]))
}

const mirrors = (w: ReturnType<typeof wire>, preview: boolean) => w.sent.filter((m) => m['type'] === 'sketch.mirror' && (m['preview'] === true) === preview)
const ghost = (page: Page) => page.locator('.sketch-layer g[data-preview="copies"]')
const tool = async (page: Page) => (await readSketch(page)).draw.tool

test.describe('Tablet mode: pen and finger', () => {
  const strip = (page: Page) => page.locator('[data-strip="sketch"]')
  const chooseDraw = async (page: Page, id: string) => {
    await strip(page).locator('[data-group="draw"]').tap()
    await page.locator(`.group-row[data-tool="${id}"] .group-tool`).tap()
  }

  test('select the circle, choose Mirror, hold the pen on the line, lift: the copy, its two constraints, one Undo', async ({ page }) => {
    const w = wire(page)
    await openApp(page)
    await openNewSketch(page, 'Top')
    // a construction line through the origin: Construct with nothing selected sets the mode, then Line, two points and a tap on the last
    await strip(page).locator('[data-tool="construct"]').tap()
    await chooseDraw(page, 'line')
    const a = await screenFor(page, 0, -20)
    const b = await screenFor(page, 0, 20)
    await place(page, a)
    await place(page, b)
    await place(page, b)
    await expect.poll(() => Object.values(w.sketch().entities).filter((e) => e.type === 'line').length).toBe(1)
    await strip(page).locator('[data-tool="construct"]').tap()
    // the circle: its centre, then a point 5 mm out
    await chooseDraw(page, 'circle')
    await place(page, await screenFor(page, 30, 10))
    await place(page, await screenFor(page, 35, 10))
    await expect.poll(() => w.circles().length).toBe(1)
    await strip(page).locator('[data-tool="select"]').tap()
    await expect.poll(() => tool(page)).toBe('select')
    // select it with the pen on its rim
    await place(page, await screenFor(page, 35, 10))
    await expect.poll(async () => (await readSketch(page)).selected.length).toBe(1)
    await chooseDraw(page, 'sk_mirror')
    await expect.poll(() => tool(page)).toBe('sk_mirror')
    await expect(page.getByTestId('copy-hint')).toHaveText('Mirror: tap the line to mirror about.')
    expect((await readSketch(page)).selected).toHaveLength(1)

    // the pen down on the line, not lifted: the ghost of the copy, and nothing added
    const on = await screenFor(page, 0, 5)
    await sendPointer(page, [
      { phase: 'down', kind: 'pen', id: 10, x: on.x - 3, y: on.y },
      { phase: 'move', kind: 'pen', id: 10, ...on },
    ])
    await expect(ghost(page)).toBeAttached()
    await expect.poll(() => mirrors(w, true).length).toBe(1)
    expect(mirrors(w, false)).toHaveLength(0)
    expect(w.circles()).toHaveLength(1)
    // the lift commits: the tool is Select again and the copy is there
    await sendPointer(page, [{ phase: 'up', kind: 'pen', id: 10, ...on, pressure: 0 }])
    await expect.poll(() => mirrors(w, false).length).toBe(1)
    await expect.poll(() => w.circles().length).toBe(2)
    await expect.poll(() => tool(page)).toBe('select')
    await expect(ghost(page)).toHaveCount(0)
    await expect(page.getByTestId('copy-hint')).toHaveCount(0)
    expectMirrored(w)

    // one Undo takes the copy and both constraints away
    const undo = page.getByRole('button', { name: 'Undo', exact: true })
    await undo.tap()
    await expect.poll(() => w.circles().length).toBe(1)
    expect(w.sketch().constraints.filter((c) => c.type === 'symmetric' || c.type === 'equal')).toHaveLength(0)
    await page.getByTestId('sketch-cancel').tap()
  })

  test('Mirror with nothing selected shows the first hint, and Mirror pressed again goes back to Select', async ({ page }) => {
    const w = wire(page)
    await openApp(page)
    await openNewSketch(page, 'Top')
    await chooseDraw(page, 'sk_mirror')
    await expect(page.getByTestId('copy-hint')).toHaveText('Mirror: tap the geometry to mirror, then the mirror line.')
    await chooseDraw(page, 'sk_mirror')
    await expect.poll(() => tool(page)).toBe('select')
    expect(mirrors(w, true).length + mirrors(w, false).length).toBe(0)
    await page.getByTestId('sketch-cancel').tap()
  })
})

test.describe('Desktop mode: the mouse', () => {
  test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })
  const MOUSE = 20
  const bar = (page: Page) => page.locator('[data-strip="sketch"]')
  const move = (page: Page, p: Point) => sendPointer(page, [{ phase: 'move', kind: 'mouse', id: MOUSE, ...p }])
  const click = (page: Page, p: Point) =>
    sendPointer(page, [
      { phase: 'move', kind: 'mouse', id: MOUSE, ...p },
      { phase: 'down', kind: 'mouse', id: MOUSE, ...p, button: 0 },
      { phase: 'up', kind: 'mouse', id: MOUSE, ...p, button: 0 },
    ])
  const button = (page: Page, name: string) => bar(page).getByRole('button', { name, exact: true })

  test('click the circle, Mirror on the toolbar, rest the mouse on the line, click: the copy, its two constraints, one Undo', async ({ page }) => {
    const w = wire(page)
    await openApp(page)
    await openNewSketch(page, 'Top')
    await button(page, 'Construction').click()
    await button(page, 'Line').click()
    const a = await screenFor(page, 0, -20)
    const b = await screenFor(page, 0, 20)
    await click(page, a)
    await click(page, b)
    await click(page, b)
    await expect.poll(() => Object.values(w.sketch().entities).filter((e) => e.type === 'line').length).toBe(1)
    await button(page, 'Construction').click()
    await button(page, 'Circle').click()
    await click(page, await screenFor(page, 30, 10))
    await click(page, await screenFor(page, 35, 10))
    await expect.poll(() => w.circles().length).toBe(1)
    await button(page, 'Select').click()
    await click(page, await screenFor(page, 35, 10))
    await expect.poll(async () => (await readSketch(page)).selected.length).toBe(1)
    await expect(button(page, 'Mirror')).toBeEnabled()
    await button(page, 'Mirror').click()
    await expect(button(page, 'Mirror')).toHaveAttribute('aria-pressed', 'true')
    await expect.poll(() => tool(page)).toBe('sk_mirror')
    await expect(page.getByTestId('copy-hint')).toHaveText('Mirror: tap the line to mirror about.')

    // the mouse rests on the line: the ghost, nothing added
    const on = await screenFor(page, 0, 5)
    await move(page, { x: on.x - 40, y: on.y })
    await move(page, on)
    await expect(ghost(page)).toBeAttached()
    expect(mirrors(w, false)).toHaveLength(0)
    expect(w.circles()).toHaveLength(1)
    await click(page, on)
    await expect.poll(() => mirrors(w, false).length).toBe(1)
    await expect.poll(() => w.circles().length).toBe(2)
    await expect.poll(() => tool(page)).toBe('select')
    await expect(button(page, 'Mirror')).toHaveAttribute('aria-pressed', 'false')
    expectMirrored(w)

    await button(page, 'Undo').click()
    await expect.poll(() => w.circles().length).toBe(1)
    expect(w.sketch().constraints.filter((c) => c.type === 'symmetric' || c.type === 'equal')).toHaveLength(0)
  })
})

