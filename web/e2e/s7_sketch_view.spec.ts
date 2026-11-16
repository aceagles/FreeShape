import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { canvasCentre, fingerDrag, fingerTap, twoFingers } from './support/pointer.ts'
import { openNewSketch, openSketchFeature, pickSketchAt, readSketch, screenOf, settledSketch, uvAt } from './support/sketch.ts'
import { readViewport, settled } from './support/viewport.ts'

// Slice S7, drawing the sketch in the view (card I7.7) against `freeshape serve`: the bracket's Bracket profile sketch is opened through the
// test hook (the way in from the screen is card I7.13), drawn from the server's session, and navigated in 2D and in 3D.
// Nothing is committed: every test ends with Cancel or leaves the Document to the next openApp, which closes it.

test.afterEach(async ({ page }) => closeAll(page.request))

const deg = (r: number) => (r * 180) / Math.PI

test('opening a sketch faces its plane, makes the solids see-through and draws the sketch from the session', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  // the sketch strip replaces the feature strip: Select, Draw, Dimension, Constrain, Construct, Delete, and the right end
  await expect(page.locator('[data-strip="feature"]')).toHaveCount(0)
  await expect(page.locator('[data-strip="sketch"] > .strip-button .strip-label')).toHaveText(['Select', 'Draw', 'Dimension', 'Constrain', 'Construct', 'Delete'])
  await expect(page.getByTestId('nav-2d')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('sketch-status')).toContainText('Bracket profile')
  const s = await settledSketch(page)
  console.log(`sketch: ${JSON.stringify({ name: s.name, stats: s.stats, tilt: s.tilt })}`)
  expect(s.nav).toBe('2d')
  expect(s.tilt).toBeLessThan(1e-6)
  expect(s.xAxisAngle).toBeCloseTo(0, 6)
  expect(s.stats.lines).toBeGreaterThan(0)
  // what the report counts is on screen
  await expect(page.locator('.sketch-layer path[data-kind="line"]')).toHaveCount(s.stats.lines)
  await expect(page.locator('.sketch-layer g[data-kind="dimension"]')).toHaveCount(s.stats.labels)
  // the sketch lies inside the view
  const box = (await page.getByTestId('viewport').boundingBox())!
  const at = await screenOf(page, 'point', 'origin')
  expect(at.x).toBeGreaterThan(0)
  expect(at.x).toBeLessThan(box.width)
  expect(at.y).toBeGreaterThan(0)
  expect(at.y).toBeLessThan(box.height)
  await page.getByTestId('sketch-cancel').click()
  await expect(page.locator('[data-strip="feature"]')).toBeVisible()
  await expect(page.locator('.sketch-layer')).toHaveCount(0)
  expect((await readViewport(page)).sketch).toBeNull()
})

test('2D: one finger pans and a two-finger twist turns the sketch on screen, the camera staying normal to the plane', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  const c = await canvasCentre(page)
  const before = await settledSketch(page)
  const at = await screenOf(page, 'point', 'origin')
  await fingerDrag(page, c, { x: c.x + 120, y: c.y + 50 })
  const panned = await settledSketch(page)
  const now = await screenOf(page, 'point', 'origin')
  expect(now.x - at.x).toBeCloseTo(120, 3)
  expect(now.y - at.y).toBeCloseTo(50, 3)
  expect(panned.tilt).toBeLessThan(1e-6)
  expect(panned.xAxisAngle).toBeCloseTo(before.xAxisAngle, 6)
  // twist 30 degrees about the midpoint: fingers 200 px apart, turned clockwise on screen
  const r = 100
  const at30 = (a: number) => ({ a: { x: c.x - r * Math.cos(a), y: c.y - r * Math.sin(a) }, b: { x: c.x + r * Math.cos(a), y: c.y + r * Math.sin(a) } })
  const end = at30(Math.PI / 6)
  await twoFingers(page, at30(0).a, at30(0).b, end.a, end.b, 15)
  const turned = await settledSketch(page)
  expect(deg(turned.xAxisAngle - panned.xAxisAngle)).toBeCloseTo(30, 0)
  expect(turned.tilt).toBeLessThan(1e-6)
  expect(turned.nav).toBe('2d')
})

test('3D: one finger orbits, picks and points still lie on the sketch plane; 2D and Normal to turn the view square again', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await page.getByTestId('nav-3d').click()
  await expect(page.getByTestId('nav-3d')).toHaveAttribute('aria-pressed', 'true')
  const c = await canvasCentre(page)
  await fingerDrag(page, c, { x: c.x + 90, y: c.y - 60 })
  const s = await settledSketch(page)
  expect(s.nav).toBe('3d')
  expect(s.tilt).toBeGreaterThan(0.1)
  // a pick at a point of the sketch still returns it
  const o = await screenOf(page, 'point', 'origin')
  expect(await pickSketchAt(page, o.x, o.y, 'touch')).toEqual({ kind: 'point', id: 'origin' })
  // a point placed under the pointer is in the sketch plane: the origin's own place gives back (0, 0), a place 40 px on gives a point off it
  const uv0 = (await uvAt(page, o.x, o.y))!
  expect(Math.hypot(uv0[0], uv0[1])).toBeLessThan(1e-6)
  expect(Math.hypot(...(await uvAt(page, o.x + 40, o.y + 30))!)).toBeGreaterThan(0.1)
  await page.getByTestId('normal-to').click()
  expect((await settledSketch(page)).tilt).toBeLessThan(1e-6)
  await expect(page.getByTestId('nav-3d')).toHaveAttribute('aria-pressed', 'true')
  await fingerDrag(page, c, { x: c.x - 70, y: c.y + 80 })
  expect((await settledSketch(page)).tilt).toBeGreaterThan(0.1)
  await page.getByTestId('nav-2d').click()
  const back = await settledSketch(page)
  expect(back.tilt).toBeLessThan(1e-6)
  expect(back.nav).toBe('2d')
})

test('a tap selects a sketch item and the chip says so; a tap on empty space clears it', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await settledSketch(page)
  const o = await screenOf(page, 'point', 'origin')
  await fingerTap(page, o)
  await expect(page.getByTestId('selection-count')).toHaveText('1 point')
  await expect.poll(async () => (await readSketch(page)).selected).toEqual([{ kind: 'point', id: 'origin' }])
  await fingerTap(page, { x: 8, y: (await page.getByTestId('viewport').boundingBox())!.height - 8 })
  await expect(page.getByTestId('selection-count')).toHaveText('Nothing selected')
})

test('a new sketch on the Top plane opens empty, in 2D and square to the plane; Cancel closes it', async ({ page }) => {
  await openApp(page)
  await openNewSketch(page, 'Top')
  const s = await settledSketch(page)
  expect(s.stats.lines + s.stats.circles + s.stats.arcs).toBe(0)
  expect(s.tilt).toBeLessThan(1e-6)
  await expect(page.getByTestId('sketch-status')).toContainText('Empty')
  await page.getByTestId('sketch-cancel').click()
  await expect(page.locator('[data-strip="feature"]')).toBeVisible()
  // the part selection works again once the sketch is gone
  await settled(page)
  expect((await readViewport(page)).sketch).toBeNull()
})
