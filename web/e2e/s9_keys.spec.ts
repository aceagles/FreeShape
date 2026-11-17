import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { desktopKeymap } from '../src/input/bindings.ts'
import { openApp } from './support/app.ts'
import { closeAll, removeDocument } from './support/docs.ts'
import { chooseWithRing, place, screenFor } from './support/draw.ts'
import { desktopMode } from './support/mode.ts'
import { pickAll } from './support/pick.ts'
import type { Spot } from './support/pick.ts'
import { settledSketch } from './support/sketch.ts'
import { readViewport, settled, viewCanvas } from './support/viewport.ts'

// Slice S9, card I9.6: the desktop's global, view and workspace keys in Desktop mode (a mouse and a keyboard, no touch),
// and which of the desktop's Ctrl chords a browser lets the page have.
test.use(desktopMode)
test.afterEach(async ({ page }) => closeAll(page.request))

/** The desktop chords a browser may keep for itself, as Playwright presses them and as bindings.ts writes them. */
const CHORDS = [
  ['Control+w', 'Ctrl+W'],
  ['Control+n', 'Ctrl+N'],
  ['Control+Shift+N', 'Ctrl+Shift+N'],
  ['Control+o', 'Ctrl+O'],
  ['Control+q', 'Ctrl+Q'],
  ['Control+t', 'Ctrl+T'],
] as const

test('which of Ctrl+W, Ctrl+N, Ctrl+Shift+N, Ctrl+O, Ctrl+Q and Ctrl+T reach the page and stay stopped; none the browser keeps is bound', async ({ page, context, browserName }) => {
  const kept: string[] = []
  for (const [press, chord] of CHORDS) {
    await page.setContent('<p tabindex="0">keys</p>')
    await page.evaluate(() => {
      const w = window as unknown as { got: string[] }
      w.got = []
      window.addEventListener('keydown', (e) => {
        if (!e.ctrlKey || e.key.length !== 1) return
        e.preventDefault()
        w.got.push(e.key.toUpperCase())
      })
    })
    await page.locator('p').focus()
    const pages = context.pages().length
    await page.keyboard.press(press)
    // the browser's own action, if it took one, has had its turn once the page has run a frame
    const closed = page.isClosed()
    const got = closed ? [] : await page.evaluate(() => new Promise<string[]>((r) => requestAnimationFrame(() => r((window as unknown as { got: string[] }).got))))
    const reached = got.length === 1
    const held = !closed && context.pages().length === pages
    test.info().annotations.push({ type: `${browserName} ${chord}`, description: `${reached ? 'reaches the page' : 'does not reach the page'}, ${held ? 'stopped' : 'the browser acted'}` })
    if (!reached || !held) kept.push(chord)
  }
  console.log(`${browserName}: ${test.info().annotations.map((a) => `${a.type}: ${a.description}`).join('; ')}`)
  for (const chord of kept) expect(desktopKeymap[chord], chord).toBeUndefined()
  // and none Chromium and Firefox keep for themselves, which Playwright's keys go past (see the table in bindings.ts)
  for (const chord of ['Ctrl+W', 'Ctrl+N', 'Ctrl+Shift+N', 'Ctrl+T', 'Ctrl+Q']) expect(desktopKeymap[chord], chord).toBeUndefined()
})

const sketchStrip = (page: Page) => page.locator('[data-strip="sketch"]')
const featureStrip = (page: Page) => page.locator('[data-strip="feature"]')
const near = (a: readonly number[], b: readonly number[], tol = 1e-4) => a.every((x, i) => Math.abs(x - b[i]!) < tol)

/** Click the Top plane with the mouse where nothing is in front of it. */
async function selectTop(page: Page): Promise<void> {
  const cam = (await settled(page)).camera
  const grid: Spot[] = []
  for (let y = 100; y < cam.height - 20; y += 16) for (let x = 20; x < cam.width - 20; x += 16) grid.push({ x, y })
  const hits = await pickAll(page, grid, 'mouse')
  const i = hits.findIndex((h) => h?.kind === 'plane' && (h as { key: string }).key === 'Top')
  if (i < 0) throw new Error('the Top plane is not picked anywhere in the view')
  const box = (await viewCanvas(page).boundingBox())!
  await page.mouse.move(box.x + grid[i]!.x, box.y + grid[i]!.y)
  await page.mouse.click(box.x + grid[i]!.x, box.y + grid[i]!.y)
  await expect.poll(async () => (await readViewport(page)).selection.items.map((s) => `${s.kind}:${s.key}`)).toEqual(['plane:Top'])
}

test('Shift+S sketches on the selected Top plane, Esc leaves it (asking once something is drawn), Shift+7 then F gives the fitted isometric view', async ({ page }) => {
  await openApp(page)
  await expect.poll(() => page.getByTestId('shell').getAttribute('data-mode')).toBe('desktop')
  await expect.poll(() => page.locator('.panel-row[data-feature]').count()).toBeGreaterThan(0)
  const features = await page.locator('.panel-row[data-feature]').count()

  await selectTop(page)
  await page.keyboard.press('Shift+S')
  await expect(sketchStrip(page)).toBeVisible({ timeout: 20_000 })
  // the sketch faces its plane: the Top plane's normal points at the eye
  await expect.poll(async () => near((await settled(page)).camera.back, [0, 0, 1], 1e-3)).toBe(true)
  // nothing drawn: Esc leaves at once, with no new feature
  await page.keyboard.press('Escape')
  await expect(featureStrip(page)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.locator('.panel-row[data-feature]')).toHaveCount(features)

  // something drawn: Esc steps back out of the tool, then asks before dropping the drawing
  await selectTop(page)
  await page.keyboard.press('Shift+S')
  await expect(sketchStrip(page)).toBeVisible({ timeout: 20_000 })
  await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.paints ?? 0) > 0)
  await chooseWithRing(page, 'rectangle')
  await place(page, await screenFor(page, -30, -10))
  await place(page, await screenFor(page, -10, 10))
  await expect.poll(async () => (await settledSketch(page)).stats.lines).toBe(4)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.keyboard.press('Escape')
  const ask = page.getByRole('dialog', { name: 'Discard this sketch?' })
  await expect(ask).toBeVisible()
  await ask.getByRole('button', { name: 'Discard' }).click()
  await expect(featureStrip(page)).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.panel-row[data-feature]')).toHaveCount(features)

  await page.keyboard.press('Shift+7')
  const s = Math.sqrt(3)
  await expect.poll(async () => near((await settled(page)).camera.back, [1 / s, -1 / s, 1 / s])).toBe(true)
  const iso = (await settled(page)).camera
  // move the view off, then F fits it again
  await page.keyboard.press('Control+Shift+ArrowRight')
  await page.keyboard.press('Control+Shift+ArrowUp')
  await expect.poll(async () => near((await settled(page)).camera.target, iso.target)).toBe(false)
  await page.keyboard.press('f')
  await expect.poll(async () => near((await settled(page)).camera.target, iso.target)).toBe(true)
  const fitted = (await settled(page)).camera
  expect(near(fitted.back, iso.back)).toBe(true)
  expect(fitted.scale).toBeCloseTo(iso.scale, 9)
})

test('Ctrl+S saves: the Document is no longer marked unsaved', async ({ page }) => {
  const name = 'Keys spec'
  try {
    await closeAll(page.request)
    await page.goto('/')
    await expect(page.getByTestId('home')).toBeVisible()
    await page.getByRole('button', { name: 'New Document' }).click()
    await page.getByRole('textbox', { name: 'Name' }).fill(name)
    await page.getByRole('button', { name: 'Create' }).click()
    await expect(page.getByTestId('document-name')).toHaveText(name)
    await expect(page.getByTestId('link-status')).toHaveText('Connected', { timeout: 30_000 })
    // Alt+N (New Part Studio, the replacement for the browser's Ctrl+N) leaves the Document unsaved
    await page.keyboard.press('Alt+n')
    await page.getByTestId('dirty-dot').waitFor()
    await page.keyboard.press('Control+s')
    await expect(page.getByTestId('dirty-dot')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeVisible()
  } finally {
    await closeAll(page.request)
    removeDocument(name)
  }
})
