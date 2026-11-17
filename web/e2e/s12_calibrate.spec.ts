import { resolve } from 'node:path'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, removeDocument } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { sendPointer } from './support/pointer.ts'
import type { PointerStep } from './support/pointer.ts'
import { readViewport } from './support/viewport.ts'

// Slice S12, card I12.6: Calibrate with a ruler, in the real client against `freeshape serve`. A new Document with an empty Part Studio; the
// Canvas tool uploads the 64 x 32 PNG and its dialog opens; Width is set to 100 (Height follows, 50). The ruler measures two points 40 mm apart,
// the real distance is 50, and Width and Height become 125 and 62.5; Accept keeps them. Desktop mode uses the mouse and the typed box, Tablet
// mode pen events and the pad. Every check after an action waits on state (a poll or a locator); the live length is read from the state the ruler
// publishes (data-mm), never from a moment in time. Ends are placed from the canvas frame through the view's own projection
// (`canvasFrames().at`), the inverse of what the ruler computes, so a wrong frame would put the ends elsewhere.
const NAME = 'Calibrate fixture'
const PNG = resolve(import.meta.dirname, 'fixtures/canvas.png')

test.beforeEach(async ({ page }) => {
  await closeAll(page.request)
  removeDocument(NAME)
  const made = (await (await page.request.post('/documents', { data: { name: NAME } })).json()) as { id: string }
  await page.request.post(`/documents/${made.id}/open`, { data: {} })
  await page.request.post(`/documents/${made.id}/save`, { data: {} })
  await page.request.post(`/documents/${made.id}/close`, { data: {} })
})
test.afterEach(async ({ page }) => {
  await closeAll(page.request)
  removeDocument(NAME)
})

const accept = (page: Page) => page.getByRole('dialog').getByRole('button', { name: 'Accept', exact: true })
const ruler = (page: Page) => page.getByTestId('calibrate')
const phase = (page: Page) => ruler(page).getAttribute('data-phase')

/** The canvases the view draws: size, and whether it is the dialog's preview. */
// A typed number is committed by leaving its field with a blur, not Tab: Tab would move into the next field, whose text is then stale when the server's
// answer for this one changes it (Height follows Width), and leaving that one would send the stale text back.
const frames = (page: Page) => page.evaluate(() => window.__freeshapeViewport!.canvasFrames().map((c) => ({ id: c.id, preview: c.preview, width: c.width, height: c.height, xdir: c.xdir })))
/** Where a point of the canvas being edited (mm from its centre along its drawn x and y) is on screen, from the view's own projection. */
const screenOf = (page: Page, u: number, v: number) =>
  page.evaluate(([a, b]) => {
    const c = window.__freeshapeViewport!.canvasFrames().find((f) => f.preview) ?? window.__freeshapeViewport!.canvasFrames()[0]
    return c ? c.at(a!, b!) : null
  }, [u, v] as const)

/** Opens the Canvas dialog on the uploaded PNG; `start` taps the Canvas tool in the mode's own toolbar. */
async function openCanvasDialog(page: Page, start: () => Promise<void>): Promise<void> {
  await openApp(page, { document: NAME, model: false })
  await expect.poll(async () => (await readViewport(page)).frames).toBeGreaterThan(0)
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), start()])
  await chooser.setFiles(PNG)
  await expect(page.getByRole('dialog', { name: /Canvas/ })).toBeVisible()
  await expect(page.getByTestId('calibrate-start')).toBeVisible()
  await expect.poll(async () => (await frames(page)).filter((f) => f.preview).length).toBe(1)
}

/** Waits for the preview canvas to be this size. */
const previewIs = (page: Page, width: number, height: number) =>
  expect
    .poll(async () => {
      const f = (await frames(page)).find((x) => x.preview)
      return f ? [Math.round(f.width * 1e4) / 1e4, Math.round(f.height * 1e4) / 1e4] : null
    })
    .toEqual([width, height])

/** Width 100 on the dialog's first number: on the pad in Tablet mode (it opens on it), typed in Desktop mode; Height follows, 50. */
async function width100(page: Page, desktop: boolean): Promise<void> {
  if (desktop) {
    const width = page.getByRole('textbox', { name: 'Width' })
    await width.fill('100')
    await width.blur()
  } else {
    const pad = page.getByTestId('pad')
    await expect(pad).toBeVisible()
    for (const k of ['1', '0', '0']) await pad.getByRole('button', { name: k, exact: true }).click()
    await pad.getByRole('button', { name: 'Enter', exact: true }).click()
  }
  await previewIs(page, 100, 50)
}

/** Both ends of a 40 mm line along the canvas's x, at 40 % of its width round the centre, are clear of the dialog. */
async function ends(page: Page, du = 40): Promise<{ a: { x: number; y: number }; b: { x: number; y: number } }> {
  const a = await screenOf(page, -du / 2, 0)
  const b = await screenOf(page, du / 2, 0)
  expect(a).not.toBeNull()
  expect(b).not.toBeNull()
  return { a: a!, b: b! }
}

test.describe('Tablet mode', () => {
  test('a pen dragged from one end to the other, 50 on the pad: 100 x 50 becomes 125 x 62.5, and Accept keeps it', async ({ page }) => {
    await openCanvasDialog(page, async () => {
      await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
      await page.getByRole('menuitem', { name: /^Canvas/ }).click()
    })
    await width100(page, false)
    await page.getByTestId('calibrate-start').click()
    await expect.poll(() => phase(page)).toBe('first')
    // the pad of the first number is gone: the ruler has the dock
    await expect(page.getByTestId('pad')).toHaveCount(0)
    const { a, b } = await ends(page)
    await sendPointer(page, [
      { phase: 'down', kind: 'pen', id: 7, ...a },
      { phase: 'move', kind: 'pen', id: 7, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
      { phase: 'move', kind: 'pen', id: 7, ...b },
    ])
    // while the pen is down the line follows it with its length, and the magnifier is up
    await expect(page.getByTestId('ruler-loupe')).toBeVisible()
    await expect.poll(async () => Number(await page.getByTestId('ruler-length').getAttribute('data-mm'))).toBeCloseTo(40, 3)
    expect(await phase(page)).toBe('first')
    await sendPointer(page, [{ phase: 'up', kind: 'pen', id: 7, ...b, pressure: 0 }])
    await expect.poll(() => phase(page)).toBe('set')
    await expect(page.getByTestId('ruler-loupe')).toHaveCount(0)
    // the pad asks for the distance, prefilled with the measured length; no native input is on the page while it is open
    await expect(page.getByTestId('ruler-pad')).toBeVisible()
    await expect(page.getByTestId('pad-text')).toHaveText('40')
    expect(await page.locator('input, textarea, select, [contenteditable]').count()).toBe(0)
    for (const k of ['5', '0']) await page.getByTestId('pad').getByRole('button', { name: k, exact: true }).click()
    await page.getByTestId('pad').getByRole('button', { name: 'Enter', exact: true }).click()
    await previewIs(page, 125, 62.5)
    await expect(page.getByRole('button', { name: /^Width, 125/ })).toBeVisible()
    await expect.poll(() => phase(page)).toBe('off')
    await accept(page).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect.poll(async () => (await frames(page)).map((f) => [f.preview, f.width, f.height])).toEqual([[false, 125, 62.5]])
  })

  test('tap, tap with the pen sets the same ends, and the pad\'s Cancel ends the ruler with the size unchanged', async ({ page }) => {
    await openCanvasDialog(page, async () => {
      await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
      await page.getByRole('menuitem', { name: /^Canvas/ }).click()
    })
    await width100(page, false)
    await page.getByTestId('calibrate-start').click()
    const { a, b } = await ends(page)
    await sendPointer(page, [{ phase: 'down', kind: 'pen', id: 7, ...a }])
    await sendPointer(page, [{ phase: 'up', kind: 'pen', id: 7, ...a, pressure: 0 }])
    await expect.poll(() => phase(page)).toBe('second')
    // a pen tap off the picture does nothing
    await sendPointer(page, [{ phase: 'down', kind: 'pen', id: 7, x: 3, y: 3 }])
    await sendPointer(page, [{ phase: 'up', kind: 'pen', id: 7, x: 3, y: 3, pressure: 0 }])
    await expect.poll(() => phase(page)).toBe('second')
    await expect(page.getByTestId('calibrate-note')).toBeVisible()
    await sendPointer(page, [{ phase: 'down', kind: 'pen', id: 7, ...b }])
    await sendPointer(page, [{ phase: 'up', kind: 'pen', id: 7, ...b, pressure: 0 }])
    await expect.poll(() => phase(page)).toBe('set')
    await expect.poll(async () => Number(await page.getByTestId('ruler-length').getAttribute('data-mm'))).toBeCloseTo(40, 3)
    await page.getByTestId('pad').getByRole('button', { name: 'Cancel', exact: true }).click()
    await expect.poll(() => phase(page)).toBe('off')
    await previewIs(page, 100, 50)
  })

  test('a finger tap sets an end; two fingers move the view and set none', async ({ page }) => {
    await openCanvasDialog(page, async () => {
      await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
      await page.getByRole('menuitem', { name: /^Canvas/ }).click()
    })
    await width100(page, false)
    await page.getByTestId('calibrate-start').click()
    const { a } = await ends(page)
    const before = (await readViewport(page)).camera.target
    const steps: PointerStep[] = []
    for (let i = 0; i <= 6; i++) {
      steps.push({ phase: i === 0 ? ('down' as const) : ('move' as const), kind: 'touch' as const, id: 1, x: a.x + i * 8, y: a.y + 60 + i * 4 })
      steps.push({ phase: i === 0 ? ('down' as const) : ('move' as const), kind: 'touch' as const, id: 2, x: a.x + 60 + i * 8, y: a.y + 60 + i * 4 })
    }
    steps.push({ phase: 'up', kind: 'touch', id: 1, x: a.x + 48, y: a.y + 84 }, { phase: 'up', kind: 'touch', id: 2, x: a.x + 108, y: a.y + 84 })
    await sendPointer(page, steps)
    await expect.poll(async () => (await readViewport(page)).camera.target).not.toEqual(before)
    expect(await phase(page)).toBe('first')
    // one finger tap: the first end (the canvas has moved with the view: ask again)
    const at = (await screenOf(page, -10, 0))!
    await sendPointer(page, [
      { phase: 'down', kind: 'touch', id: 3, ...at },
      { phase: 'up', kind: 'touch', id: 3, ...at },
    ])
    await expect.poll(() => phase(page)).toBe('second')
  })
})

test.describe('Desktop mode', () => {
  test.use({ ...desktopMode, viewport: { width: 1440, height: 900 } })

  const start = (page: Page) => () => page.locator('.desktop-toolbar [data-tool="canvas"]').click()

  test('the mouse: click the first end, move, click the second, 50 typed, Enter: 100 x 50 becomes 125 x 62.5, and Accept keeps it', async ({ page }) => {
    await openCanvasDialog(page, start(page))
    await width100(page, true)
    await page.getByTestId('calibrate-start').click()
    await expect.poll(() => phase(page)).toBe('first')
    const { a, b } = await ends(page)
    await sendPointer(page, [{ phase: 'move', kind: 'mouse', id: 1, ...a }])
    await sendPointer(page, [{ phase: 'down', kind: 'mouse', id: 1, ...a }])
    await sendPointer(page, [{ phase: 'up', kind: 'mouse', id: 1, ...a, pressure: 0 }])
    await expect.poll(() => phase(page)).toBe('second')
    // the line and its live length follow the mouse; Shift is not held: the length is the distance to where the mouse is
    await sendPointer(page, [{ phase: 'move', kind: 'mouse', id: 1, ...(await screenOf(page, 0, 0))! }])
    await expect.poll(async () => Number(await page.getByTestId('ruler-length').getAttribute('data-mm'))).toBeCloseTo(20, 1)
    await sendPointer(page, [{ phase: 'move', kind: 'mouse', id: 1, ...b }])
    await expect.poll(async () => Number(await page.getByTestId('ruler-length').getAttribute('data-mm'))).toBeCloseTo(40, 3)
    await sendPointer(page, [{ phase: 'down', kind: 'mouse', id: 1, ...b }])
    await sendPointer(page, [{ phase: 'up', kind: 'mouse', id: 1, ...b, pressure: 0 }])
    await expect.poll(() => phase(page)).toBe('set')
    const box = page.getByRole('textbox', { name: 'Distance' })
    await expect(box).toBeFocused()
    await expect(box).toHaveValue('40')
    await box.fill('50')
    await box.press('Enter')
    await previewIs(page, 125, 62.5)
    await expect.poll(() => phase(page)).toBe('off')
    await expect(page.getByRole('textbox', { name: 'Width' })).toHaveValue('125')
    // Enter went to the distance box: the dialog is still open
    await expect(page.getByRole('dialog', { name: /Canvas/ })).toBeVisible()
    await accept(page).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect.poll(async () => (await frames(page)).map((f) => [f.preview, f.width, f.height])).toEqual([[false, 125, 62.5]])
  })

  test('two clicks at nearly the same spot are the ruler\'s, not a double click: the second end is refused as the same point', async ({ page }) => {
    await openCanvasDialog(page, start(page))
    await width100(page, true)
    await page.getByTestId('calibrate-start').click()
    const { a } = await ends(page)
    await sendPointer(page, [{ phase: 'move', kind: 'mouse', id: 1, ...a }])
    await sendPointer(page, [{ phase: 'down', kind: 'mouse', id: 1, ...a }])
    await sendPointer(page, [{ phase: 'up', kind: 'mouse', id: 1, ...a, pressure: 0 }])
    await expect.poll(() => phase(page)).toBe('second')
    // 2 px away, at once (well inside the double click's 400 ms and 6 px): it is a click like any other
    const near = { x: a.x + 2, y: a.y }
    await sendPointer(page, [{ phase: 'down', kind: 'mouse', id: 1, ...near }])
    await sendPointer(page, [{ phase: 'up', kind: 'mouse', id: 1, ...near, pressure: 0 }])
    await expect(page.getByTestId('calibrate-note')).toContainText('same point')
    expect(await phase(page)).toBe('second')
  })

  test('Esc ends the ruler and sends nothing; a second Esc cancels the dialog', async ({ page }) => {
    await openCanvasDialog(page, start(page))
    await width100(page, true)
    await page.getByTestId('calibrate-start').click()
    const { a } = await ends(page)
    await sendPointer(page, [{ phase: 'move', kind: 'mouse', id: 1, ...a }])
    await sendPointer(page, [{ phase: 'down', kind: 'mouse', id: 1, ...a }])
    await sendPointer(page, [{ phase: 'up', kind: 'mouse', id: 1, ...a, pressure: 0 }])
    await expect.poll(() => phase(page)).toBe('second')
    const calls: string[] = []
    page.on('request', (r) => r.url().includes('/calibrate') && calls.push(r.url()))
    await page.keyboard.press('Escape')
    await expect.poll(() => phase(page)).toBe('off')
    await expect(page.getByRole('dialog', { name: /Canvas/ })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    expect(calls).toEqual([])
  })

  test('a rotated and flipped canvas: the line is read in the picture\'s own frame (Snap rotation to line agrees with the server)', async ({ page }) => {
    await openCanvasDialog(page, start(page))
    await width100(page, true)
    const rotation = page.getByRole('textbox', { name: 'Rotation' })
    await rotation.fill('30')
    await rotation.blur()
    await page.getByLabel('Flip horizontally').check()
    await expect(rotation).toHaveValue('30')
    await previewIs(page, 100, 50)
    // the preview is the rotated, flipped one before the ends are placed: its drawn x is (-cos 30, -sin 30)
    await expect
      .poll(async () => {
        const f = (await frames(page)).find((x) => x.preview)
        return f ? f.xdir.map((n) => Math.round(n * 1e3) / 1e3) : null
      })
      .toEqual([-0.866, -0.5, 0])
    await page.getByLabel('Snap rotation to line').check()
    await page.getByTestId('calibrate-start').click()
    // a line from (-20, -4) to (20, 4) in the picture's frame: 44.7 mm along the picture, 26.6 degrees from its x
    const a = (await screenOf(page, -20, -4))!
    const b = (await screenOf(page, 20, 4))!
    await sendPointer(page, [{ phase: 'move', kind: 'mouse', id: 1, ...a }])
    await sendPointer(page, [{ phase: 'down', kind: 'mouse', id: 1, ...a }])
    await sendPointer(page, [{ phase: 'up', kind: 'mouse', id: 1, ...a, pressure: 0 }])
    await expect.poll(() => phase(page)).toBe('second')
    await sendPointer(page, [{ phase: 'move', kind: 'mouse', id: 1, ...b }])
    await sendPointer(page, [{ phase: 'down', kind: 'mouse', id: 1, ...b }])
    await sendPointer(page, [{ phase: 'up', kind: 'mouse', id: 1, ...b, pressure: 0 }])
    await expect.poll(() => phase(page)).toBe('set')
    const [pa, pb] = [(await ruler(page).getAttribute('data-a'))!.split(',').map(Number), (await ruler(page).getAttribute('data-b'))!.split(',').map(Number)]
    expect(pa[0]).toBeCloseTo(-20, 3)
    expect(pa[1]).toBeCloseTo(-4, 3)
    expect(pb[0]).toBeCloseTo(20, 3)
    expect(pb[1]).toBeCloseTo(4, 3)
    const box = page.getByRole('textbox', { name: 'Distance' })
    // twice the line's length: the canvas doubles
    await box.fill(String(2 * Math.hypot(40, 8)))
    await box.press('Enter')
    // freeshape/canvas_calibrate.py: the line in the plane is 40 x_drawn + 8 y_drawn, with x_drawn = (-cos 30, -sin 30) for the flip and y_drawn = (-sin 30, cos 30)
    const c = Math.cos(Math.PI / 6)
    const s = Math.sin(Math.PI / 6)
    const dx = 40 * -c + 8 * -s
    const dy = 40 * -s + 8 * c
    const angle = (Math.atan2(dy, dx) * 180) / Math.PI
    const expected = 30 + Math.round(angle / 45) * 45 - angle
    await previewIs(page, 200, 100)
    await expect.poll(async () => Number(await rotation.inputValue())).toBeCloseTo(expected, 2)
  })
})
