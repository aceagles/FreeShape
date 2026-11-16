import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { screenFor } from './support/draw.ts'
import { desktopMode } from './support/mode.ts'
import { sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { openNewSketch, settledSketch } from './support/sketch.ts'
import { settled, viewCanvas } from './support/viewport.ts'

// Slice S9, card I9.7: the desktop's sketch keys. Desktop mode, 1440 x 900, a mouse and a keyboard.
test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })
test.afterEach(async ({ page }) => closeAll(page.request))

const bar = (page: Page) => page.locator('[data-strip="sketch"]')
const button = (page: Page, name: string) => bar(page).getByRole('button', { name, exact: true })
const pressed = (page: Page, name: string) => expect(button(page, name)).toHaveAttribute('aria-pressed', 'true')
const MOUSE = 20

/** A mouse click on the view at a point: move there, press, release. */
const click = (page: Page, p: Point) =>
  sendPointer(page, [
    { phase: 'move', kind: 'mouse', id: MOUSE, ...p },
    { phase: 'down', kind: 'mouse', id: MOUSE, ...p, button: 0 },
    { phase: 'up', kind: 'mouse', id: MOUSE, ...p, button: 0 },
  ])
const labels = (page: Page) => page.locator('.sketch-layer g[data-kind="dimension"] text').evaluateAll((els) => els.map((e) => e.textContent ?? ''))

test('in a sketch on Top, G and two clicks draw a rectangle; D, a click on a side and one for the label open the dimension box; 30 and Enter set it; Esc ends', async ({ page }) => {
  await openApp(page)
  await openNewSketch(page, 'Top')
  const keyboard = page.keyboard

  await keyboard.press('g')
  await pressed(page, 'Corner rectangle')
  await click(page, await screenFor(page, -30, -10))
  await click(page, await screenFor(page, 20, 15))
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(4)

  // D, then a click on the bottom side (a quarter along: its middle is where its glyph sits) and one below it for the label
  await keyboard.press('d')
  await pressed(page, 'Dimension')
  await click(page, await screenFor(page, -17.5, -10))
  await expect(page.getByTestId('selection-count')).toHaveText('1 item')
  await click(page, await screenFor(page, -17.5, -25))
  const box = page.locator('[data-testid="dimension-inline"] input')
  await expect(box).toBeFocused()
  // letters belong to the box while it has the keyboard: 30 and Enter set the side
  await keyboard.type('30')
  await keyboard.press('Enter')
  await expect(page.getByTestId('dimension-inline')).toHaveCount(0)
  await expect.poll(() => labels(page)).toEqual(['30 mm'])

  // Esc leaves the Dimension tool for Select; the sketch has a 30 mm side
  await keyboard.press('Escape')
  await pressed(page, 'Select')
  expect((await settledSketch(page)).stats.labels).toBe(1)
  // the bottom side is 30 mm now, not the 50 it was drawn with: the distances between the sketch's points tell
  const dots = await page.locator('.sketch-layer circle[data-kind="point"]').evaluateAll((els) => els.map((e) => ({ x: e.getBoundingClientRect().x + e.getBoundingClientRect().width / 2, y: e.getBoundingClientRect().y + e.getBoundingClientRect().height / 2 })))
  const box0 = (await viewCanvas(page).boundingBox())!
  const uvs = await Promise.all(dots.map((d) => page.evaluate(([x, y]) => window.__freeshapeViewport?.sketch?.uvAt(x!, y!) ?? null, [d.x - box0.x, d.y - box0.y])))
  const gaps = uvs.flatMap((a, i) => uvs.slice(i + 1).map((b) => (a && b ? Math.hypot(a[0] - b[0], a[1] - b[1]) : 0)))
  expect(gaps.some((g) => Math.abs(g - 30) < 0.05)).toBe(true)
  expect(gaps.some((g) => Math.abs(g - 50) < 0.05)).toBe(false)
})

test('a key pressed right after clicking a toolbar button, the 3D view or a closed dialog, and after the column collapses, still reaches the sketch', async ({ page }) => {
  await openApp(page)
  await openNewSketch(page, 'Top')
  const keyboard = page.keyboard

  // the toolbar button keeps the focus after a mouse click (Chromium; WebKit gives a button none): a letter still reaches the sketch
  await button(page, 'Line').click()
  await pressed(page, 'Line')
  await keyboard.press('c')
  await pressed(page, 'Circle')
  await button(page, 'Zoom to fit').click()
  await keyboard.press('l')
  await pressed(page, 'Line')

  // a real mouse click in the 3D view, with Select on
  await keyboard.press('Escape')
  await pressed(page, 'Select')
  await viewCanvas(page).click({ position: { x: 300, y: 300 } })
  await keyboard.press('g')
  await pressed(page, 'Corner rectangle')

  // a closed dialog: Cancel sketch is not asked about an empty sketch, so draw first
  await click(page, await screenFor(page, -30, -10))
  await click(page, await screenFor(page, 20, 15))
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(4)
  // Esc chooses Select; Ctrl+A selects every entity; Space after a mouse click on a button clears it rather than pressing the button
  await keyboard.press('Escape')
  await pressed(page, 'Select')
  await keyboard.press('Control+a')
  await expect(page.getByTestId('selection-count')).toHaveText('4 items')
  await button(page, 'Select').click()
  await keyboard.press('Space')
  await expect(page.getByTestId('selection-count')).toHaveText('Nothing selected')
  await pressed(page, 'Select')
  // nothing is left to step back: Esc leaves the sketch, asking first
  await keyboard.press('Escape')
  const ask = page.getByRole('dialog', { name: 'Discard this sketch?' })
  await expect(ask).toBeVisible()
  await ask.getByRole('button', { name: 'Keep sketching' }).click()
  await expect(ask).toHaveCount(0)
  await keyboard.press('r')
  await pressed(page, 'Center rectangle')

  // the column collapses and expands: letters still reach the sketch both times
  await page.getByRole('button', { name: 'Collapse feature panel' }).click()
  await expect(page.getByTestId('panel-rail')).toBeVisible()
  await keyboard.press('a')
  await pressed(page, '3-point arc')
  await page.getByRole('button', { name: 'Expand feature panel' }).click()
  await expect(page.getByTestId('feature-panel')).toBeVisible()
  await keyboard.press('p')
  await pressed(page, 'Point')

  // O has no tool yet: the hint, no change of tool
  await keyboard.press('o')
  await expect(page.getByTestId('sketch-notice')).toHaveText('Desktop only for now')
  await pressed(page, 'Point')
})

test('in a Part Studio, F and Shift+7 reach the view right after a click on a toolbar button and in the 3D view', async ({ page }) => {
  await openApp(page)
  const keyboard = page.keyboard
  const iso = async () => (await settled(page)).camera.back
  // a click on a toolbar button, then Shift+7: the isometric view
  await page.getByRole('button', { name: 'Zoom to fit' }).first().click()
  await keyboard.press('Shift+7')
  const s = Math.sqrt(3)
  await expect.poll(async () => (await iso()).every((v, i) => Math.abs(v - [1 / s, -1 / s, 1 / s][i]!) < 1e-3)).toBe(true)
  // a click in the 3D view, then Shift+5 and F
  await viewCanvas(page).click({ position: { x: 20, y: 120 } })
  await keyboard.press('Shift+5')
  await expect.poll(async () => (await iso()).every((v, i) => Math.abs(v - [0, 0, 1][i]!) < 1e-3)).toBe(true)
})
