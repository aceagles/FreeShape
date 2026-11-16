import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { fingerTap, sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { openNewSketch, openSketchFeature, screenOf, settledSketch, uvAt } from './support/sketch.ts'

// Slice S7, constraints (card I7.10) against `freeshape serve`: the Constrain group enables what the server's sketch.fits
// lists for the selection, a tap on a tool sends sketch.constrain, a conflict is refused with a message and changes nothing,
// and Delete removes a selected constraint glyph. The Bracket profile sketch is opened through the test hook and never committed.

test.afterEach(async ({ page }) => closeAll(page.request))

/** What the client sent and the server answered on the live channel, parsed. */
function wire(page: Page) {
  const sent: Record<string, unknown>[] = []
  const got: Record<string, unknown>[] = []
  page.on('websocket', (ws) => {
    ws.on('framesent', (f) => void tryParse(f.payload, sent))
    ws.on('framereceived', (f) => void tryParse(f.payload, got))
  })
  return { sent, got }
}
function tryParse(payload: string | Buffer, into: Record<string, unknown>[]) {
  if (typeof payload !== 'string') return
  try {
    const m = JSON.parse(payload) as Record<string, unknown>
    if (typeof m['type'] === 'string' && m['type'].startsWith('sketch.')) into.push(m)
  } catch {
    // not JSON
  }
}

const lineIds = (page: Page) => page.locator('.sketch-layer path[data-kind="line"]').evaluateAll((els) => els.map((e) => e.getAttribute('data-id')!))
const glyphs = (page: Page, type: string) => page.locator(`.sketch-layer g[data-kind="constraint"][data-type="${type}"]`)
const group = (page: Page) => page.locator('.tool-strip').getByRole('button', { name: /^Constrain/ })
const rows = (page: Page) => page.getByRole('menu', { name: 'Constrain' }).locator('.group-row')

/** Tap the line `id` and wait for it to be the selection. */
async function tapLine(page: Page, w: ReturnType<typeof wire>, id: string) {
  // a quarter of the way along: the middle is where a constraint's glyph sits
  const ent = (w.got.find((m) => m['type'] === 'sketch.opened')!['sketch'] as { entities: Record<string, { p1: string; p2: string }> }).entities[id]!
  const a = await screenOf(page, 'point', ent.p1)
  const b = await screenOf(page, 'point', ent.p2)
  await fingerTap(page, { x: a.x + (b.x - a.x) * 0.25, y: a.y + (b.y - a.y) * 0.25 })
  await expect(page.getByTestId('selection-count')).toHaveText('1 item')
}

test('every Constrain tool is on, and the ones sketch.fits lists for the selection carry a marker', async ({ page }) => {
  const w = wire(page)
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await group(page).click()
  await expect(rows(page)).toHaveCount(12)
  // with nothing selected every tool can be chosen, and none is marked as fitting
  await expect(page.locator('.group-row[data-state="ok"]')).toHaveCount(12)
  await expect(page.locator('.group-row[data-fit]')).toHaveCount(0)
  await page.getByTestId('strip-scrim').click({ position: { x: 5, y: 700 } })
  const [first] = await lineIds(page)
  await tapLine(page, w, first!)
  await expect.poll(() => w.got.filter((m) => m['type'] === 'sketch.fitted').at(-1)?.['selection']).toEqual([first])
  const tools = w.got.filter((m) => m['type'] === 'sketch.fitted').at(-1)!['tools'] as string[]
  expect(tools.length).toBeGreaterThan(0)
  await group(page).click()
  await expect(page.locator('.group-row[data-state="ok"]')).toHaveCount(12)
  await expect(page.locator('.group-row[data-fit]')).toHaveCount(tools.length)
  for (const t of tools) await expect(page.locator(`.group-row[data-tool="${t}"]`)).toHaveAttribute('data-fit', '')
})

test('a conflict shows a message and changes nothing; Delete on a selected glyph removes that constraint', async ({ page }) => {
  const w = wire(page)
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await settledSketch(page)
  // a line that is held horizontal already, then Vertical on it: the solve cannot meet both
  const opened = w.got.find((m) => m['type'] === 'sketch.opened')!
  const cons = (opened['sketch'] as { constraints: { id: string; type: string; refs: string[] }[] }).constraints
  const horizontal = cons.find((c) => c.type === 'horizontal')!
  const line = horizontal.refs[0]!
  await tapLine(page, w, line)
  await expect.poll(() => (w.got.filter((m) => m['type'] === 'sketch.fitted').at(-1)?.['selection'] as string[] | undefined)?.[0]).toBe(line)
  const fits = w.got.filter((m) => m['type'] === 'sketch.fitted').at(-1)!['tools'] as string[]
  expect(fits).toContain('vertical')
  const glyphCount = () => page.locator('.sketch-layer g[data-kind="constraint"]').count()
  const before = { glyphs: await glyphCount(), status: await page.getByTestId('sketch-status').innerText() }
  await group(page).click()
  await page.locator('.group-row[data-tool="vertical"] .group-tool').click()
  await expect(page.getByTestId('constraint-note')).toContainText('Vertical conflicts')
  expect(w.sent.filter((m) => m['type'] === 'sketch.constrain').at(-1)).toEqual({ type: 'sketch.constrain', tool: 'vertical', picks: [line] })
  expect(w.got.filter((m) => m['type'] === 'sketch.constrained').at(-1)!['accepted']).toBe(false)
  // the drawn sketch is as it was, and the selection is cleared
  await expect(page.getByTestId('selection-count')).toHaveText('Nothing selected')
  expect({ glyphs: await glyphCount(), status: await page.getByTestId('sketch-status').innerText() }).toEqual(before)
  expect(w.got.filter((m) => m['type'] === 'sketch.changed')).toHaveLength(0)

  // Delete: tap the horizontal glyph and press Delete
  const n = await glyphs(page, 'horizontal').count()
  expect(n).toBeGreaterThan(0)
  const at = await screenOf(page, 'constraint', horizontal.id)
  await fingerTap(page, at)
  await expect(page.getByTestId('selection-count')).toHaveText('1 constraint')
  await page.keyboard.press('Delete')
  await expect.poll(() => w.sent.filter((m) => m['type'] === 'sketch.unconstrain').at(-1)).toEqual({ type: 'sketch.unconstrain', constraint: horizontal.id })
  await expect(glyphs(page, 'horizontal')).toHaveCount(n - 1)
})

/** The screen position of a sketch point (u, v in mm): the plane's axes are read off the view. */
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

test('with one point selected, choosing Coincident waits for a second point and then makes the constraint; Esc leaves the mode', async ({ page }) => {
  const w = wire(page)
  await openApp(page)
  await openNewSketch(page, 'Top')
  // two free points, put down with the pen
  const place = async (p: Point) => sendPointer(page, [{ phase: 'down', kind: 'pen', id: 10, x: p.x - 3, y: p.y }, { phase: 'move', kind: 'pen', id: 10, ...p }, { phase: 'up', kind: 'pen', id: 10, ...p, pressure: 0 }])
  const a = await screenFor(page, 20, 20)
  const b = await screenFor(page, -10, 30)
  const pointTool = async () => {
    await page.locator('.tool-strip').getByRole('button', { name: /^Draw/ }).click()
    await page.locator('.group-row[data-tool="point"] .group-tool').click()
  }
  const added = () => w.sent.filter((m) => m['type'] === 'sketch.add').length
  await pointTool()
  await place(a)
  await expect.poll(added).toBe(1)
  // the Point tool is still on (choosing it again would go back to Select, F20)
  await place(b)
  await expect.poll(added).toBe(2)
  await page.locator('.tool-strip [data-tool="select"]').click()
  await fingerTap(page, a)
  await expect(page.getByTestId('selection-count')).toHaveText('1 point')
  await expect.poll(() => w.got.filter((m) => m['type'] === 'sketch.fitted').length).toBeGreaterThan(0)
  // Coincident does not fit one point: it is still chosen, and says what to pick next
  await group(page).click()
  await page.locator('.group-row[data-tool="coincident"] .group-tool').click()
  await expect(page.getByTestId('constraint-hint')).toContainText('Coincident')
  await expect(page.locator('.tool-strip [data-group="constrain"]')).toBeVisible()
  expect(w.sent.filter((m) => m['type'] === 'sketch.constrain')).toEqual([])
  // the second point completes it
  await fingerTap(page, b)
  await expect.poll(() => w.sent.filter((m) => m['type'] === 'sketch.constrain').length).toBe(1)
  const sent = w.sent.filter((m) => m['type'] === 'sketch.constrain')[0]!
  expect(sent['tool']).toBe('coincident')
  expect((sent['picks'] as string[]).length).toBe(2)
  await expect.poll(() => w.got.filter((m) => m['type'] === 'sketch.constrained').at(-1)?.['accepted']).toBe(true)
  expect(w.got.filter((m) => m['type'] === 'sketch.constrained').at(-1)!['made']).toHaveLength(1)
  // Esc leaves the mode
  await expect(page.getByTestId('constraint-hint')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('constraint-hint')).toHaveCount(0)
})
