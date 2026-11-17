import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { idOf } from './support/docs.ts'
import { canvasCentre, fingerDrag, penStroke, twoFingers } from './support/pointer.ts'
import { settled, readViewport } from './support/viewport.ts'

// Slice S1, "A served model on the iPad", against `freeshape serve` on a Documents root holding the bracket and the
// hinge examples (scripts/web/e2e.sh); each test opens the bracket Document from the home screen first (openApp).
// Under WebKit at 1024 x 768 with touch. The edit test puts the diameter back, and openApp closes every Document
// without saving, so no test sees what another did.

const expected = JSON.parse(readFileSync(resolve(import.meta.dirname, '../fixtures/bracket.expected.json'), 'utf8')) as {
  parts: number
  faces: number
  edges: number
  triangles: number
}
const EDIT = { op: 'set_param', feature: 'F5', key: 'diameter' }
const ORIGINAL_DIAMETER = 6.5

test('the page shows the app shell, and the hook reports the fixture\'s parts and faces', async ({ page }) => {
  await openApp(page)
  for (const region of ['top-bar', 'tool-strip', 'left-column', 'viewport', 'view-cube']) {
    await expect(page.locator(`[data-region="${region}"]`), region).toBeVisible()
  }
  await expect(page.getByTestId('document-name')).toHaveText('Bracket')
  const v = await readViewport(page)
  expect({ parts: v.parts, faces: v.faces, edges: v.edges, triangles: v.triangles }).toEqual({
    parts: expected.parts,
    faces: expected.faces,
    edges: expected.edges,
    triangles: expected.triangles,
  })
  expect(v.parts).toBe(1)
  expect(v.faces).toBe(16)
})

test('one finger orbits, two fingers pinch and twist, the pen changes nothing', async ({ page }) => {
  await openApp(page)
  const c = await canvasCentre(page)

  // one finger drag: the view orientation changes, the zoom does not
  const start = await readViewport(page)
  await fingerDrag(page, { x: c.x, y: c.y }, { x: c.x + 140, y: c.y - 70 })
  const orbited = await settled(page)
  const turned = orbited.camera.back.some((x, i) => Math.abs(x - start.camera.back[i]!) > 0.05)
  expect(turned, `orientation ${start.camera.back} -> ${orbited.camera.back}`).toBe(true)
  expect(orbited.camera.scale).toBeCloseTo(start.camera.scale, 6)

  // two fingers spreading: zoom in (fewer mm per pixel)
  const a0 = { x: c.x - 40, y: c.y }
  const b0 = { x: c.x + 40, y: c.y }
  await twoFingers(page, a0, b0, { x: c.x - 110, y: c.y }, { x: c.x + 110, y: c.y })
  const pinched = await settled(page)
  expect(pinched.camera.scale, 'pinch out zooms in').toBeLessThan(orbited.camera.scale * 0.8)

  // two fingers turning about their middle: the view rolls
  const r = 80
  const angle = (deg: number) => ({ x: Math.cos((deg * Math.PI) / 180) * r, y: Math.sin((deg * Math.PI) / 180) * r })
  const at = (deg: number, sign: 1 | -1) => ({ x: c.x + sign * angle(deg).x, y: c.y + sign * angle(deg).y })
  await twoFingers(page, at(0, 1), at(0, -1), at(40, 1), at(40, -1), 12)
  const twisted = await settled(page)
  expect(Math.abs(twisted.camera.roll - pinched.camera.roll), 'twist rolls the view').toBeGreaterThan(0.2)

  // the pen: a long drag and a long hold-and-drag, none of it navigates
  const before = await readViewport(page)
  await penStroke(page, { x: c.x - 150, y: c.y + 60 }, { x: c.x + 150, y: c.y - 90 }, { moves: 12 })
  await penStroke(page, { x: c.x + 100, y: c.y + 100 }, { x: c.x - 100, y: c.y + 20 }, { id: 11, moves: 6, pressure: 0.9 })
  await page.waitForTimeout(250)
  const after = await readViewport(page)
  expect(after.camera).toEqual(before.camera)
})

test('an edit posted to the server reaches the page without a reload', async ({ page, request }) => {
  await openApp(page)
  await page.evaluate(() => {
    ;(window as unknown as { __stillHere: boolean }).__stillHere = true
  })
  const before = await readViewport(page)
  expect(before.revision).not.toBeNull()
  const doc = await idOf(request, 'Bracket')
  const tab = (await (await request.get(`/documents/${doc}/tabs`)).json()).tabs[0].id as string
  try {
    const res = await request.post(`/documents/${doc}/tabs/${tab}/edit`, { data: { ...EDIT, value: 7.0 } })
    expect(res.status(), await res.text()).toBe(200)
    await expect.poll(async () => (await readViewport(page)).revision, { timeout: 30_000 }).toBeGreaterThan(before.revision!)
    const after = await readViewport(page)
    expect(after.parts).toBe(before.parts)
    expect(after.faces).toBe(before.faces)
    await expect.poll(async () => (await readViewport(page)).frames).toBeGreaterThan(before.frames)
    expect(await page.evaluate(() => (window as unknown as { __stillHere?: boolean }).__stillHere), 'the page was not reloaded').toBe(true)
  } finally {
    await request.post(`/documents/${doc}/tabs/${tab}/edit`, { data: { ...EDIT, value: ORIGINAL_DIAMETER } })
  }
})

// The baseline for the iPad hand check: the debug panel's round trip over 200 pings. It is logged, not asserted
// against a limit (loopback has no tunnel in it); the panel has to reach 200 samples and show them.
test('the debug panel reaches 200 round trips and shows the median and worst', async ({ page }) => {
  test.setTimeout(120_000)
  await openApp(page)
  await page.getByTestId('link-status').click()
  const panel = page.getByTestId('debug-panel')
  await expect(panel).toBeVisible()
  await expect(panel.getByText('Samples')).toBeVisible()
  await expect(panel.locator('dd').nth(2)).toHaveText('200', { timeout: 90_000 })
  const median = await page.getByTestId('rtt-median').innerText()
  const worst = await page.getByTestId('rtt-worst').innerText()
  expect(median).toMatch(/^\d+(\.\d+)? ms$/)
  expect(worst).toMatch(/^\d+(\.\d+)? ms$/)
  console.log(`ROUND TRIP over 200 pings (loopback): median ${median}, worst ${worst}`)
})
