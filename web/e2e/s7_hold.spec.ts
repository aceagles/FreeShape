import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { sendPointer } from './support/pointer.ts'
import type { Point } from './support/pointer.ts'
import { openSketchFeature, readSketch } from './support/sketch.ts'

// Slice S7, F19: the browser takes no long press in the app. A finger holds on a sketch label and on empty sketch space: the quick
// menu opens, the finger slides to an item without lifting and lifts on it, and the item's tool is chosen. Through all of it nothing
// is selected, no `selectstart` or `contextmenu` default runs, and the root rules (user-select, touch callout, touch-action) are on.
//
// What this proves under WebKit and what it cannot: pointer events stand in for the finger (WebKit under Playwright has no touch
// input device), so the browser's own long-press (Chrome's selection handles and menu on a real touch screen) never starts here.
// What it does prove is that the app's side holds: the rules are computed on the elements a finger lands on, the events a long press
// would raise are prevented, and the pointer module keeps the slide. Only a real touch screen in Chrome shows the rest.

test.afterEach(async ({ page }) => closeAll(page.request))

const FINGER = 21
const down = (p: Point) => ({ phase: 'down' as const, kind: 'touch' as const, id: FINGER, ...p })
const move = (p: Point) => ({ phase: 'move' as const, kind: 'touch' as const, id: FINGER, ...p })
const up = (p: Point) => ({ phase: 'up' as const, kind: 'touch' as const, id: FINGER, ...p })

/** Counts the default actions a long press would run: a `selectstart` or a `contextmenu` nobody prevented. */
async function watchDefaults(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as { __defaults: string[] }
    w.__defaults = []
    for (const type of ['selectstart', 'contextmenu'])
      window.addEventListener(type, (e) => {
        if (!e.defaultPrevented) w.__defaults.push(type)
      })
  })
}
const defaultsRun = (page: Page) => page.evaluate(() => (window as unknown as { __defaults: string[] }).__defaults)
const selection = (page: Page) => page.evaluate(() => ({ text: window.getSelection()?.toString() ?? '', ranges: window.getSelection()?.rangeCount ?? 0 }))

/** What a browser does when a finger rests on `at`: ask for a selection to start and for a context menu, on the element there. */
async function offerLongPressDefaults(page: Page, at: Point): Promise<void> {
  await page.evaluate(({ x, y }) => {
    const el = document.elementFromPoint(x, y) ?? document.body
    for (const type of ['selectstart', 'contextmenu']) el.dispatchEvent(new Event(type, { bubbles: true, cancelable: true, composed: true }))
    const text = document.querySelector('.sketch-layer g[data-kind="dimension"] text')
    if (text) for (const type of ['selectstart', 'contextmenu']) text.dispatchEvent(new Event(type, { bubbles: true, cancelable: true, composed: true }))
  }, at)
}

/** A finger holds at `at` (view coordinates), slides to the item and lifts on it. */
async function holdSlideRelease(page: Page, at: Point, id: string): Promise<void> {
  await sendPointer(page, [down(at)])
  await expect(page.getByTestId('quick-line')).toBeVisible()
  const view = (await page.getByTestId('viewport').boundingBox())!
  await offerLongPressDefaults(page, { x: view.x + at.x, y: view.y + at.y })
  expect((await selection(page)).ranges).toBe(0)
  const b = (await page.getByTestId(`quick-${id}`).boundingBox())!
  const to = { x: b.x + b.width / 2 - view.x, y: b.y + b.height / 2 - view.y }
  // the finger stays down the whole way: this is the move the browser's selection drag used to take
  await sendPointer(page, [move({ x: (at.x + to.x) / 2, y: (at.y + to.y) / 2 }), move(to)])
  await expect(page.getByTestId(`quick-${id}`)).toHaveAttribute('data-aimed', '')
  expect(await selection(page)).toEqual({ text: '', ranges: 0 })
  await sendPointer(page, [up(to)])
  await expect(page.getByTestId('quick-menu')).toHaveCount(0)
  expect(await selection(page)).toEqual({ text: '', ranges: 0 })
}

const tool = (page: Page) => expect.poll(async () => (await readSketch(page)).draw.tool)

test('a hold on a sketch label opens the quick menu, slides to Circle and picks it, with nothing selected', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await watchDefaults(page)
  const label = page.locator('.sketch-layer g[data-kind="dimension"] text').first()
  await expect(label).toBeVisible()
  const lb = (await label.boundingBox())!
  const view = (await page.getByTestId('viewport').boundingBox())!
  await holdSlideRelease(page, { x: lb.x + lb.width / 2 - view.x, y: lb.y + lb.height / 2 - view.y }, 'circle')
  await tool(page).toBe('circle')
  expect(await defaultsRun(page)).toEqual([])
})

test('a hold on empty sketch space opens the quick menu, slides to Rectangle and picks it, with nothing selected', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  await watchDefaults(page)
  await holdSlideRelease(page, { x: 60, y: 60 }, 'rectangle')
  await tool(page).toBe('rectangle')
  expect(await defaultsRun(page)).toEqual([])
})

test('the root rules are computed on what a finger lands on: nothing is selectable but a text field', async ({ page }) => {
  await openApp(page)
  await openSketchFeature(page, 'Bracket profile')
  const style = (sel: string) =>
    page.locator(sel).first().evaluate((el) => {
      const c = getComputedStyle(el) as CSSStyleDeclaration & { webkitUserSelect?: string; webkitTouchCallout?: string }
      return { select: c.webkitUserSelect || c.userSelect, callout: c.webkitTouchCallout ?? 'none', action: c.touchAction }
    })
  for (const sel of ['.sketch-layer g[data-kind="dimension"] text', '.tool-strip button', '.top-bar button', '.viewport-canvas']) {
    const s = await style(sel)
    expect(s.select, sel).toBe('none')
    expect(s.callout, sel).toBe('none')
  }
  expect((await style('.viewport-canvas')).action).toBe('none')
  // a portalled piece and a text field, made the way the app makes them: outside .shell, and an input
  const made = await page.evaluate(() => {
    const out = document.createElement('div')
    out.textContent = 'portalled'
    const input = document.createElement('input')
    input.type = 'text'
    out.append(input)
    document.body.append(out)
    const get = (el: Element) => {
      const c = getComputedStyle(el) as CSSStyleDeclaration & { webkitUserSelect?: string }
      return c.webkitUserSelect || c.userSelect
    }
    const r = { outside: get(out), input: get(input) }
    out.remove()
    return r
  })
  expect(made).toEqual({ outside: 'none', input: 'text' })
})
