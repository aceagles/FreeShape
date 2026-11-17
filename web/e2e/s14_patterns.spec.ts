import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { place, screenFor } from './support/draw.ts'
import { desktopMode } from './support/mode.ts'
import { sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { openNewSketch, readSketch, screenOf } from './support/sketch.ts'

// Slice S14, the linear and circular pattern in a sketch (card I14.3) against `freeshape serve`, in a new sketch on Top.
// Linear: a circle of radius 2 at the origin and a horizontal construction line at v = 10; the circle selected, Linear
// pattern, a Commit on the line right of the circle, 4 and 12, Accept: circles centred at 0, 12, 24 and 36; a Commit left of
// it gives 0, -12, -24 and -36. Circular: a circle of radius 2 at (20, 0) about the origin, 6 over 360 (60 degree steps on
// radius 20), then 4 over 90 (0, 30, 60 and 90 degrees). These are I14.1's parity values against the desktop. While the
// panel is open the ghost follows the numbers and the sketch gains nothing; Cancel leaves it as it was; each pattern goes
// with one Undo. Once in Tablet mode with the pen and the finger, once in Desktop mode with the mouse and the keyboard.
// Nothing is committed: every test ends with the Document closed.

test.afterEach(async ({ page }) => closeAll(page.request))

type Msg = Record<string, unknown>
interface Sk {
  points: Record<string, [number, number]>
  entities: Record<string, { type: string; p1?: string; p2?: string; c?: string; r?: number; construction?: boolean }>
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
  const circles = () => Object.values(sketch().entities).filter((e) => e.type === 'circle')
  /** The centres of the circles, sorted by angle about the origin then by u. */
  const centres = () => circles().map((c) => sketch().points[c.c!]!)
  const lines = () => Object.values(sketch().entities).filter((e) => e.type === 'line')
  const of = (type: string, preview: boolean) => sent.filter((m) => m['type'] === type && (m['preview'] === true) === preview)
  return { sent, got, sketch, circles, centres, lines, of }
}
type Wire = ReturnType<typeof wire>

const LP = 'sketch.linear_pattern'
const CP = 'sketch.circular_pattern'
const ghost = (page: Page) => page.locator('.sketch-layer g[data-preview="copies"]')
const copyPanel = (page: Page) => page.getByTestId('copy-panel')
const tool = async (page: Page) => (await readSketch(page)).draw.tool

/** The circles' centres along u (all on v = 0), sorted. */
function expectAlong(w: Wire, us: number[]): void {
  const cs = w.centres()
  expect(cs).toHaveLength(us.length)
  for (const c of cs) expect(c[1]).toBeCloseTo(0, 2)
  const got = cs.map((c) => c[0]).sort((a, b) => a - b)
  us.forEach((u, i) => expect(got[i]).toBeCloseTo(u, 2))
  for (const c of w.circles()) expect(c.r).toBeCloseTo(2, 3)
}

/** The circles' centres on radius 20 about the origin, at these angles in degrees. */
function expectRound(w: Wire, degrees: number[]): void {
  const cs = w.centres()
  expect(cs).toHaveLength(degrees.length)
  for (const c of cs) expect(Math.hypot(c[0], c[1])).toBeCloseTo(20, 2)
  const angle = (c: [number, number]) => (((Math.atan2(c[1], c[0]) * 180) / Math.PI + 360.0001) % 360) - 0.0001
  const got = cs.map(angle).sort((a, b) => a - b)
  degrees.forEach((d, i) => expect(got[i]).toBeCloseTo(d, 1))
}

test.describe('Tablet mode: pen and finger', () => {
  const strip = (page: Page) => page.locator('[data-strip="sketch"]')
  const chooseDraw = async (page: Page, id: string) => {
    await strip(page).locator('[data-group="draw"]').tap()
    await page.locator(`.group-row[data-tool="${id}"] .group-tool`).tap()
  }
  // the pad's keys as the other pad specs press them (s5_pad): a key stops its pointerdown, so a synthetic touch tap gives no click
  const pad = (page: Page, name: string) => page.getByTestId('pad').getByRole('button', { name, exact: true }).click()
  const row = (page: Page, label: string) => copyPanel(page).getByRole('button', { name: new RegExp(`^${label}, `) })
  /** Select the circle centred at (u, v) with the pen on its rim, with Select on. */
  const selectCircle = async (page: Page, u: number, v: number) => {
    await strip(page).locator('[data-tool="select"]').tap()
    await expect.poll(() => tool(page)).toBe('select')
    await place(page, await screenFor(page, u, v - 2))
    await expect.poll(async () => (await readSketch(page)).selected.map((h) => h.kind)).toEqual(['entity'])
  }
  const circle = async (page: Page, w: Wire, u: number, v: number) => {
    await chooseDraw(page, 'circle')
    await place(page, await screenFor(page, u, v))
    await place(page, await screenFor(page, u + 2, v))
    await expect.poll(() => w.circles().length).toBe(1)
  }

  test('linear: 4 at 12 to the right, then to the left; the ghost follows the pad, Cancel and the panel add nothing; one Undo each', async ({ page }) => {
    const w = wire(page)
    await openApp(page)
    await openNewSketch(page, 'Top')
    // a horizontal construction line at v = 10: Construct with nothing selected sets the mode, then Line, two points, a tap on the last
    await strip(page).locator('[data-tool="construct"]').tap()
    await chooseDraw(page, 'line')
    const a = await screenFor(page, -45, 10)
    const b = await screenFor(page, 45, 10)
    await place(page, a)
    await place(page, b)
    await place(page, b)
    await expect.poll(() => w.lines().length).toBe(1)
    await strip(page).locator('[data-tool="construct"]').tap()
    await circle(page, w, 0, 0)
    await selectCircle(page, 0, 0)
    await chooseDraw(page, 'sk_lpattern')
    await expect.poll(() => tool(page)).toBe('sk_lpattern')
    await expect(page.getByTestId('copy-hint')).toHaveText('Linear pattern: tap a line for the direction.')

    // the line, right of the circle: the panel with the pad on Instances, the first preview without spacing, the ghost
    await place(page, await screenFor(page, 30, 10))
    await expect(copyPanel(page)).toBeVisible()
    await expect(page.getByRole('dialog', { name: 'Linear pattern' })).toBeVisible()
    await expect(page.getByRole('textbox', { name: 'Instances, editing on the pad' })).toBeVisible()
    await expect.poll(() => w.of(LP, true).length).toBe(1)
    expect(w.of(LP, true)[0]).not.toHaveProperty('spacing')
    expect(w.of(LP, true)[0]!['floor']).toBeGreaterThan(0)
    await expect(ghost(page)).toBeAttached()
    await expect(row(page, 'Spacing')).toHaveText(/\d mm$/)
    expect(w.circles()).toHaveLength(1)

    // Cancel: nothing sent, nothing added, the tool still on with the circle selected
    await page.getByRole('dialog', { name: 'Linear pattern' }).locator('.dialog-header').getByRole('button', { name: 'Cancel' }).tap()
    await expect(copyPanel(page)).toHaveCount(0)
    await expect(ghost(page)).toHaveCount(0)
    expect(w.of(LP, false)).toHaveLength(0)
    expect(w.circles()).toHaveLength(1)
    expect(await tool(page)).toBe('sk_lpattern')
    expect((await readSketch(page)).selected).toHaveLength(1)

    // again: 4 on the pad, then Spacing 12; the ghost follows each and the sketch gains nothing
    await place(page, await screenFor(page, 30, 10))
    await expect(copyPanel(page)).toBeVisible()
    await pad(page, '4')
    await expect.poll(() => w.of(LP, true).at(-1)?.['count']).toBe(4)
    await pad(page, 'Enter')
    await row(page, 'Spacing').tap()
    await pad(page, '1')
    await pad(page, '2')
    await expect.poll(() => w.of(LP, true).at(-1)?.['spacing']).toBe(12)
    await expect(ghost(page)).toBeAttached()
    await pad(page, 'Enter')
    await expect(row(page, 'Spacing')).toHaveText('12 mm')
    expect(w.of(LP, false)).toHaveLength(0)
    expect(w.circles()).toHaveLength(1)
    await page.getByRole('button', { name: 'Accept' }).tap()
    await expect.poll(() => w.circles().length).toBe(4)
    expect(w.of(LP, false).at(-1)).toMatchObject({ count: 4, spacing: 12 })
    expectAlong(w, [0, 12, 24, 36])
    await expect(copyPanel(page)).toHaveCount(0)
    await expect.poll(() => tool(page)).toBe('select')
    expect((await readSketch(page)).selected).toHaveLength(0)
    await expect(ghost(page)).toHaveCount(0)

    // one Undo takes the three copies away
    const undo = page.getByRole('button', { name: 'Undo', exact: true })
    await undo.tap()
    await expect.poll(() => w.circles().length).toBe(1)

    // left of the circle: the copies go left
    await selectCircle(page, 0, 0)
    await chooseDraw(page, 'sk_lpattern')
    await place(page, await screenFor(page, -30, 10))
    await expect(copyPanel(page)).toBeVisible()
    await pad(page, '4')
    await pad(page, 'Enter')
    await row(page, 'Spacing').tap()
    await pad(page, '1')
    await pad(page, '2')
    await pad(page, 'Enter')
    await expect(row(page, 'Spacing')).toHaveText('12 mm')
    await page.getByRole('button', { name: 'Accept' }).tap()
    await expect.poll(() => w.circles().length).toBe(4)
    expectAlong(w, [-36, -24, -12, 0])
    await undo.tap()
    await expect.poll(() => w.circles().length).toBe(1)
    await page.getByTestId('sketch-cancel').tap()
  })

  test('circular: about the origin point 6 over 360, then 4 over 90; one Undo each', async ({ page }) => {
    const w = wire(page)
    await openApp(page)
    await openNewSketch(page, 'Top')
    await circle(page, w, 20, 0)
    await selectCircle(page, 20, 0)
    await chooseDraw(page, 'sk_cpattern')
    await expect(page.getByTestId('copy-hint')).toHaveText('Circular pattern: tap the centre point.')
    // the origin point is the centre
    await place(page, await screenOf(page, 'point', 'origin'))
    await expect(copyPanel(page)).toBeVisible()
    await expect.poll(() => w.of(CP, true).length).toBe(1)
    expect(w.of(CP, true)[0]).toMatchObject({ count: 6, angle: 360, centre: { point: 'origin' } })
    await expect(ghost(page)).toBeAttached()
    await expect(row(page, 'Total angle')).toHaveText('360 deg')
    expect(w.circles()).toHaveLength(1)
    await pad(page, 'Enter')
    await page.getByRole('button', { name: 'Accept' }).tap()
    await expect.poll(() => w.circles().length).toBe(6)
    expectRound(w, [0, 60, 120, 180, 240, 300])
    const undo = page.getByRole('button', { name: 'Undo', exact: true })
    await undo.tap()
    await expect.poll(() => w.circles().length).toBe(1)

    await selectCircle(page, 20, 0)
    await chooseDraw(page, 'sk_cpattern')
    await place(page, await screenOf(page, 'point', 'origin'))
    await expect(copyPanel(page)).toBeVisible()
    await pad(page, '4')
    await pad(page, 'Enter')
    await row(page, 'Total angle').tap()
    await pad(page, '9')
    await pad(page, '0')
    await expect.poll(() => w.of(CP, true).at(-1)).toMatchObject({ count: 4, angle: 90 })
    expect(w.circles()).toHaveLength(1)
    await pad(page, 'Enter')
    await page.getByRole('button', { name: 'Accept' }).tap()
    await expect.poll(() => w.circles().length).toBe(4)
    expectRound(w, [0, 30, 60, 90])
    await undo.tap()
    await expect.poll(() => w.circles().length).toBe(1)
    await page.getByTestId('sketch-cancel').tap()
  })
})

test.describe('Desktop mode: the mouse and the keyboard', () => {
  test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })
  const MOUSE = 20
  const bar = (page: Page) => page.locator('[data-strip="sketch"]')
  const click = (page: Page, p: Point) =>
    sendPointer(page, [
      { phase: 'move', kind: 'mouse', id: MOUSE, ...p },
      { phase: 'down', kind: 'mouse', id: MOUSE, ...p, button: 0 },
      { phase: 'up', kind: 'mouse', id: MOUSE, ...p, button: 0 },
    ])
  const button = (page: Page, name: string) => bar(page).getByRole('button', { name, exact: true })
  const field = (page: Page, name: string) => copyPanel(page).getByRole('textbox', { name, exact: true })
  const selectCircle = async (page: Page, u: number, v: number) => {
    await button(page, 'Select').click()
    await click(page, await screenFor(page, u, v - 2))
    await expect.poll(async () => (await readSketch(page)).selected.map((h) => h.kind)).toEqual(['entity'])
  }
  const circle = async (page: Page, w: Wire, u: number, v: number) => {
    await button(page, 'Circle').click()
    await click(page, await screenFor(page, u, v))
    await click(page, await screenFor(page, u + 2, v))
    await expect.poll(() => w.circles().length).toBe(1)
  }

  test('linear: count, Enter, spacing, Enter, to the right and to the left; Esc cancels; one Undo each', async ({ page }) => {
    const w = wire(page)
    await openApp(page)
    await openNewSketch(page, 'Top')
    await button(page, 'Construction').click()
    await button(page, 'Line').click()
    const a = await screenFor(page, -45, 10)
    const b = await screenFor(page, 45, 10)
    await click(page, a)
    await click(page, b)
    await click(page, b)
    await expect.poll(() => w.lines().length).toBe(1)
    await button(page, 'Construction').click()
    await circle(page, w, 0, 0)
    await selectCircle(page, 0, 0)
    await expect(button(page, 'Linear pattern')).toBeEnabled()
    await button(page, 'Linear pattern').click()
    await expect(button(page, 'Linear pattern')).toHaveAttribute('aria-pressed', 'true')

    // Esc with the focus off the fields: nothing sent, the tool still on
    await click(page, await screenFor(page, 30, 10))
    await expect(copyPanel(page)).toBeVisible()
    await expect(field(page, 'Instances')).toBeFocused()
    await expect.poll(() => w.of(LP, true).length).toBe(1)
    await expect(ghost(page)).toBeAttached()
    await field(page, 'Instances').blur()
    await page.keyboard.press('Escape')
    await expect(copyPanel(page)).toHaveCount(0)
    await expect(ghost(page)).toHaveCount(0)
    expect(w.of(LP, false)).toHaveLength(0)
    expect(w.circles()).toHaveLength(1)
    expect(await tool(page)).toBe('sk_lpattern')

    // the desktop's keystrokes: 4, Enter, 12, Enter
    await click(page, await screenFor(page, 30, 10))
    await expect(field(page, 'Instances')).toBeFocused()
    await page.keyboard.type('4')
    await page.keyboard.press('Enter')
    await expect.poll(() => w.of(LP, true).at(-1)?.['count']).toBe(4)
    await expect(field(page, 'Spacing')).toBeFocused()
    await page.keyboard.type('12')
    await page.keyboard.press('Tab')
    await expect.poll(() => w.of(LP, true).at(-1)?.['spacing']).toBe(12)
    await expect(field(page, 'Instances')).toBeFocused()
    expect(w.circles()).toHaveLength(1)
    expect(w.of(LP, false)).toHaveLength(0)
    await page.keyboard.press('Tab')
    await expect(field(page, 'Spacing')).toBeFocused()
    await page.keyboard.press('Enter')
    await expect.poll(() => w.circles().length).toBe(4)
    expectAlong(w, [0, 12, 24, 36])
    await expect(copyPanel(page)).toHaveCount(0)
    await expect.poll(() => tool(page)).toBe('select')
    await expect(button(page, 'Linear pattern')).toHaveAttribute('aria-pressed', 'false')
    await button(page, 'Undo').click()
    await expect.poll(() => w.circles().length).toBe(1)

    await selectCircle(page, 0, 0)
    await button(page, 'Linear pattern').click()
    await click(page, await screenFor(page, -30, 10))
    await expect(field(page, 'Instances')).toBeFocused()
    await page.keyboard.type('4')
    await page.keyboard.press('Enter')
    await expect(field(page, 'Spacing')).toBeFocused()
    await page.keyboard.type('12')
    await page.keyboard.press('Enter')
    await expect.poll(() => w.circles().length).toBe(4)
    expectAlong(w, [-36, -24, -12, 0])
    await button(page, 'Undo').click()
    await expect.poll(() => w.circles().length).toBe(1)
  })

  test('circular: a click on the origin, 6 over 360, then 4 over 90; one Undo each', async ({ page }) => {
    const w = wire(page)
    await openApp(page)
    await openNewSketch(page, 'Top')
    await circle(page, w, 20, 0)
    await selectCircle(page, 20, 0)
    await button(page, 'Circular pattern').click()
    await click(page, await screenOf(page, 'point', 'origin'))
    await expect(field(page, 'Instances')).toBeFocused()
    await expect(field(page, 'Instances')).toHaveValue('6')
    await expect(field(page, 'Total angle')).toHaveValue('360')
    await expect(ghost(page)).toBeAttached()
    expect(w.circles()).toHaveLength(1)
    await page.getByRole('button', { name: 'Accept' }).click()
    await expect.poll(() => w.circles().length).toBe(6)
    expectRound(w, [0, 60, 120, 180, 240, 300])
    await button(page, 'Undo').click()
    await expect.poll(() => w.circles().length).toBe(1)

    await selectCircle(page, 20, 0)
    await button(page, 'Circular pattern').click()
    await click(page, await screenOf(page, 'point', 'origin'))
    await expect(field(page, 'Instances')).toBeFocused()
    await page.keyboard.type('4')
    await page.keyboard.press('Enter')
    await expect(field(page, 'Total angle')).toBeFocused()
    await page.keyboard.type('90')
    await page.keyboard.press('Enter')
    await expect.poll(() => w.circles().length).toBe(4)
    expect(w.of(CP, false).at(-1)).toMatchObject({ count: 4, angle: 90, centre: { point: 'origin' } })
    expectRound(w, [0, 30, 60, 90])
    await button(page, 'Undo').click()
    await expect.poll(() => w.circles().length).toBe(1)
  })
})
