import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { closeAll, idOf, measureTab, removeDocument } from './support/docs.ts'
import { featureRow } from './support/features.ts'
import { screenFor } from './support/draw.ts'
import { desktopMode } from './support/mode.ts'
import { pickAll, project } from './support/pick.ts'
import type { P3, Spot } from './support/pick.ts'
import { sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { screenOf, settledSketch } from './support/sketch.ts'
import { readViewport, settled, viewCanvas } from './support/viewport.ts'

// Slice S9 gate (card I9.12), the slice's scripted proof: a part made from nothing in Desktop mode with the mouse and the
// keyboard only, at 1440 x 900, against `freeshape serve`. From the home screen a Document is created; Alt+N (the desktop's
// Ctrl+N, which a browser keeps) adds a Part Studio; Shift+S sketches on the Top plane; L and clicks draw a closed profile, D
// dimensions it (one by press, drag and release, one by two clicks); Shift+E extrudes it; a box takes the top face's edges and
// Shift+F fillets them; Ctrl+Z and Ctrl+Y undo and redo the fillet; a right click on its row suppresses it. The volume the
// kernel measures is the one the numbers give at every step. Every state is waited for with expect.poll or a locator.
//
// The card's numbers (a 40 mm side, a 10 mm extrusion) were written before the box was designed. A window box takes what lies
// wholly inside it by screen position, behind other elements too (the desktop's own rule). In the isometric view the bottom
// edges are the top ones moved down by 0.82 of the depth on screen, and the top face's outline is 0.41 of (width + height)
// tall, so the top face's four edges are the only ones inside their own bounding box when width + height is under twice the
// depth: the vertical edge at the back corner would fit otherwise. The rectangle is 10 x 8 mm and the extrusion 10 mm.
test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })

const NAME = 'Desktop part'
const MOUSE = 20
const W = 10
const H = 8
const DEPTH = 10
const R = 2

test.beforeEach(() => removeDocument(NAME))
test.afterEach(async ({ page }) => {
  await closeAll(page.request)
  removeDocument(NAME)
})

const at = (p: Point) => ({ kind: 'mouse' as const, id: MOUSE, ...p })
/** A left click in the view with the mouse: hover, press, release. */
const click = (page: Page, p: Point) => sendPointer(page, [{ phase: 'move', ...at(p) }, { phase: 'down', ...at(p), button: 0 }, { phase: 'up', ...at(p), button: 0 }])
const features = (page: Page) => page.locator('.panel-row[data-feature]')
const sketchStrip = (page: Page) => page.locator('[data-strip="sketch"]')
const labels = (page: Page) => page.locator('.sketch-layer g[data-kind="dimension"]:not([data-preview]) text').evaluateAll((els) => els.map((e) => e.textContent ?? ''))
const toolOn = (page: Page, name: string) => expect(sketchStrip(page).getByRole('button', { name, exact: true })).toHaveAttribute('aria-pressed', 'true')

/** The volume of a W x H x DEPTH block whose top edges are rounded with radius r: the slices above the rounding shrink by the rounding at each side. */
const blockVolume = (w: number, h: number, d: number, r: number): number => {
  const rounded = r * r * (1 - Math.PI / 4) // the area one rounding takes from the section, per mm of edge
  const squares = r ** 3 * (5 / 3 - Math.PI / 2) // the corners' share: the integral of the inset squared
  return w * h * d - 2 * (w + h) * rounded + 4 * squares
}

/** The corners of the sketch now, on screen (view pixels): the sketch's points but the origin. */
async function corners(page: Page): Promise<Point[]> {
  const ids = await page.locator('.sketch-layer circle[data-kind="point"]').evaluateAll((els) => els.map((e) => e.getAttribute('data-id') ?? '').filter((id) => id !== 'origin'))
  return Promise.all(ids.map((id) => screenOf(page, 'point', id)))
}
const extent = (ps: Point[]) => ({ x0: Math.min(...ps.map((p) => p.x)), x1: Math.max(...ps.map((p) => p.x)), y0: Math.min(...ps.map((p) => p.y)), y1: Math.max(...ps.map((p) => p.y)) })

/** Click the Top plane with the mouse where nothing is in front of it; returns where. */
async function selectTop(page: Page): Promise<Point> {
  const cam = (await settled(page)).camera
  const grid: Spot[] = []
  for (let y = 100; y < cam.height - 20; y += 16) for (let x = 20; x < cam.width - 20; x += 16) grid.push({ x, y })
  const hits = await pickAll(page, grid, 'mouse')
  const i = hits.findIndex((h) => h?.kind === 'plane' && (h as { key: string }).key === 'Top')
  expect(i, 'the Top plane is picked somewhere in the view').toBeGreaterThanOrEqual(0)
  await click(page, grid[i]!)
  await expect.poll(async () => (await readViewport(page)).selection.items.map((s) => `${s.kind}:${s.key}`)).toEqual(['plane:Top'])
  return grid[i]!
}

test('from the home screen to a filleted solid with the mouse and keys: Document, Part Studio, sketch, dimensions, extrude, box-select, fillet, undo, redo, suppress', async ({ page }) => {
  test.setTimeout(300_000)
  await closeAll(page.request)
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()

  // a Document with its Part Studio, made on the home screen
  await page.getByRole('button', { name: 'New Document' }).click()
  await page.getByRole('textbox', { name: 'Name' }).fill(NAME)
  await page.getByRole('button', { name: 'Create' }).click()
  await expect(page.getByTestId('shell')).toBeVisible()
  await expect(page.getByTestId('document-name')).toHaveText(NAME)
  await expect(page.getByTestId('link-status')).toHaveText('Connected', { timeout: 30_000 })
  await expect.poll(() => page.getByTestId('shell').getAttribute('data-mode')).toBe('desktop')
  await expect(page.locator('.tab-button')).toHaveCount(1)
  await page.waitForFunction(() => window.__freeshapeViewport !== undefined)
  const id = await idOf(page.request, NAME)
  const tabIds = async () => ((await (await page.request.get(`/documents/${id}/tabs`)).json()) as { tabs: { id: string }[] }).tabs.map((t) => t.id)
  const first = await tabIds()
  expect(first).toHaveLength(1)

  // New Part Studio from the bindings table: Alt+N (Ctrl+N is the browser's)
  await page.keyboard.press('Alt+n')
  await expect(page.locator('.tab-button')).toHaveCount(2)
  await expect.poll(async () => (await tabIds()).length).toBe(2)
  const tab = (await tabIds()).find((t) => !first.includes(t))!
  await expect(features(page)).toHaveCount(0)
  await expect.poll(async () => (await readViewport(page)).parts).toBe(0)

  // Top, then Shift+S: a sketch on it
  await selectTop(page)
  await page.keyboard.press('Shift+S')
  await expect(sketchStrip(page)).toBeVisible({ timeout: 20_000 })
  await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.paints ?? 0) > 0)

  // L and five clicks: four lines, closed on the first point, which ends the chain; Esc then leaves the tool for Select
  await page.keyboard.press('l')
  await toolOn(page, 'Line')
  const a = await screenFor(page, -12, -8)
  const b = await screenFor(page, 12, -8)
  const c = await screenFor(page, 12, 8)
  const d = await screenFor(page, -12, 8)
  await click(page, a)
  await click(page, b)
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(1)
  await click(page, c)
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(2)
  await click(page, d)
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(3)
  await click(page, a)
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(4)
  await expect.poll(async () => (await settledSketch(page)).draw.picks).toHaveLength(0)
  await page.keyboard.press('Escape')
  await toolOn(page, 'Select')

  // the closing line took no inferred direction (a point it meets wins over the inference): V and a click on it make the left side vertical
  const vertical = page.locator('.sketch-layer g[data-kind="constraint"][data-type="vertical"]')
  await expect(vertical).toHaveCount(1)
  await page.keyboard.press('v')
  await toolOn(page, 'Vertical')
  await click(page, await screenFor(page, -12, 3))
  await expect(vertical).toHaveCount(2)
  await page.keyboard.press('Escape')
  await toolOn(page, 'Select')

  // D: press on the bottom side, drag, release where the label goes (F22): the box takes 10 and Enter
  await page.keyboard.press('d')
  await toolOn(page, 'Dimension')
  const bottom = await screenFor(page, -6, -8)
  const near = await screenFor(page, -6, -11)
  const label = await screenFor(page, -6, -14)
  await sendPointer(page, [{ phase: 'move', ...at(bottom) }, { phase: 'down', ...at(bottom), button: 0 }, { phase: 'move', ...at(near) }, { phase: 'move', ...at(label) }])
  await expect(page.locator('.sketch-layer g[data-preview="1"]')).toHaveCount(1)
  await expect(page.locator('.sketch-layer g[data-preview="1"] text')).toHaveText('24 mm')
  await sendPointer(page, [{ phase: 'up', ...at(label), button: 0 }])
  const box = page.locator('[data-testid="dimension-inline"] input')
  await expect(box).toBeFocused()
  await page.keyboard.type(String(W))
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('dimension-inline')).toHaveCount(0)
  await expect.poll(() => labels(page)).toEqual([`${W} mm`])

  // the second one by two clicks: a click on the right side picks it and the box opens at the second click
  await expect.poll(async () => { const e = extent(await corners(page)); return Math.abs((e.x1 - e.x0) / (e.y1 - e.y0) - W / 16) < 0.02 }).toBe(true)
  const right = extent(await corners(page))
  const side = { x: right.x1, y: right.y0 + (right.y1 - right.y0) * 0.25 }
  await click(page, side)
  await expect(page.getByTestId('selection-count')).toHaveText('1 item')
  await click(page, { x: side.x + 70, y: side.y })
  await expect(page.locator('[data-testid="dimension-inline"] input')).toBeFocused()
  await page.keyboard.type(String(H))
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('dimension-inline')).toHaveCount(0)
  await expect.poll(async () => (await labels(page)).sort()).toEqual([`${H} mm`, `${W} mm`].sort())
  await expect.poll(async () => { const e = extent(await corners(page)); return Math.abs((e.x1 - e.x0) / (e.y1 - e.y0) - W / H) < 0.02 }).toBe(true)

  // Esc leaves Dimension for Select
  await page.keyboard.press('Escape')
  await toolOn(page, 'Select')

  // Shift+E: the Extrude opens with the sketch's region picked; 10 and Enter accept
  await page.keyboard.press('Shift+E')
  const dialog = page.getByRole('dialog', { name: /^Extrude/ })
  await expect(dialog).toBeVisible({ timeout: 20_000 })
  await expect(dialog.getByTestId('dialog-form')).toContainText(/Region 1 of Sketch/)
  const depth = dialog.getByRole('textbox', { name: 'Depth' })
  await depth.click()
  await page.keyboard.press('Control+A')
  await page.keyboard.type(String(DEPTH))
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(features(page)).toHaveCount(2)
  await expect.poll(async () => (await readViewport(page)).parts).toBe(1)
  await expect.poll(async () => (await measureTab(page.request, NAME, tab))[0]?.volume ?? 0).toBeGreaterThan(0)
  let measured = await measureTab(page.request, NAME, tab)
  expect(measured).toHaveLength(1)
  const block = W * H * DEPTH
  expect(Math.abs(measured[0]!.volume - block) / block).toBeLessThan(1e-6)
  expect(measured[0]!.faces).toBe(6)

  // the isometric view, and the Select menu with Faces unticked so a box takes edges only
  await page.keyboard.press('Shift+7')
  await page.keyboard.press('f')
  await page.getByRole('button', { name: 'Select' }).click()
  await page.getByRole('menuitemcheckbox', { name: /^Faces/ }).click()
  await expect.poll(async () => (await readViewport(page)).selection.count).toBe(0)

  // the top face's four edges, boxed left to right: the box is the bounding box of their ends on screen, a few pixels larger
  const top = measured[0]!.edges.filter((e) => Math.abs(e.centre[2] - DEPTH) < 1e-6)
  expect(top).toHaveLength(4)
  const cam = (await settled(page)).camera
  const ends = top.flatMap((e) => [-1, 1].map((s) => project(cam, e.centre.map((v, i) => v + (s * e.length * e.direction[i]!) / 2) as unknown as P3)))
  const e = extent(ends)
  const from = { x: Math.floor(e.x0) - 4, y: Math.floor(e.y0) - 4 }
  const to = { x: Math.ceil(e.x1) + 4, y: Math.ceil(e.y1) + 4 }
  const wanted = await page.evaluate((r) => window.__freeshapeViewport!.boxHits(r.from.x, r.from.y, r.to.x, r.to.y), { from, to })
  expect(wanted.map((i) => i.kind)).toEqual(['edge', 'edge', 'edge', 'edge'])
  const canvas = (await viewCanvas(page).boundingBox())!
  await page.mouse.move(canvas.x + from.x, canvas.y + from.y)
  await page.mouse.down()
  await page.mouse.move(canvas.x + to.x, canvas.y + to.y, { steps: 8 })
  await expect(page.getByTestId('select-box')).toBeVisible()
  await page.mouse.up()
  await expect.poll(async () => (await readViewport(page)).selection.items.map((i) => i.kind)).toEqual(['edge', 'edge', 'edge', 'edge'])
  await expect(page.getByTestId('select-count-badge')).toHaveText('4')

  // Shift+F: the Fillet opens with them; 2 and Enter accept
  await page.keyboard.press('Shift+F')
  const fillet = page.getByRole('dialog', { name: /^Fillet/ })
  await expect(fillet).toBeVisible({ timeout: 20_000 })
  const radius = fillet.getByRole('textbox', { name: 'Radius' })
  await radius.click()
  await page.keyboard.press('Control+A')
  await page.keyboard.type(String(R))
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(features(page)).toHaveCount(3)
  const rounded = blockVolume(W, H, DEPTH, R)
  await expect.poll(async () => (await measureTab(page.request, NAME, tab))[0]?.faces ?? 0).toBe(10)
  measured = await measureTab(page.request, NAME, tab)
  console.log(`volume ${measured[0]!.volume}, wanted ${rounded}`)
  expect(Math.abs(measured[0]!.volume - rounded) / rounded).toBeLessThan(1e-6)

  // Ctrl+Z takes the fillet away, Ctrl+Y puts it back
  await page.keyboard.press('Control+z')
  await expect(features(page)).toHaveCount(2)
  await expect.poll(async () => Math.round((await measureTab(page.request, NAME, tab))[0]?.volume ?? 0)).toBe(block)
  await page.keyboard.press('Control+y')
  await expect(features(page)).toHaveCount(3)
  await expect.poll(async () => Math.abs(((await measureTab(page.request, NAME, tab))[0]?.volume ?? 0) - rounded) / rounded).toBeLessThan(1e-6)

  // a right click on the Fillet row: Suppress. The solid is the plain block again; Unsuppress brings the fillet back
  const row = featureRow(page, /^Fillet/)
  await row.click({ button: 'right' })
  const menu = page.getByTestId('context-menu')
  await expect(menu).toBeVisible()
  await menu.getByRole('menuitem', { name: 'Suppress', exact: true }).click()
  await expect(menu).toHaveCount(0)
  await expect(row).toHaveAttribute('data-state', 'suppressed')
  await expect.poll(async () => Math.round((await measureTab(page.request, NAME, tab))[0]?.volume ?? 0)).toBe(block)
  await row.click({ button: 'right' })
  await menu.getByRole('menuitem', { name: 'Unsuppress', exact: true }).click()
  await expect(row).not.toHaveAttribute('data-state', 'suppressed')
  await expect.poll(async () => Math.abs(((await measureTab(page.request, NAME, tab))[0]?.volume ?? 0) - rounded) / rounded).toBeLessThan(1e-6)
})
