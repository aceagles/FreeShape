import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, idOf } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { emptySpot, faceSpots, pickAll } from './support/pick.ts'
import type { Spot } from './support/pick.ts'
import { partRow } from './support/parts.ts'
import { readViewport, settled, viewCanvas } from './support/viewport.ts'

// Slice S9, card I9.8: the visibility keys as the owner uses them in Desktop mode, on a copy of the example Bracket: real mouse
// and keyboard events under WebKit. P hides the planes and shows them again, with the View menu's tick; Y hides the selected
// part (clicked, Shift-clicked or boxed), or the part under the pointer when nothing is selected; Shift+Y shows every part.
// P acts on the planes' own state (the Origin rows' eyes), not a view-wide switch: after P an eye brings one plane back.
test.use(desktopMode)
test.afterEach(async ({ page }) => closeAll(page.request))

const planes = async (page: Page) => (await readViewport(page)).planes
const hiddenParts = async (page: Page) => (await readViewport(page)).hiddenParts

/** The View menu's Show planes tick, read by opening the menu and closing it again. */
async function planesTick(page: Page): Promise<string | null> {
  await page.getByRole('button', { name: 'View', exact: true }).click()
  const checked = await page.getByRole('menuitemcheckbox', { name: /Show planes/ }).getAttribute('aria-checked')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
  return checked
}

/** Where the mouse is over the part: a spot that picks a face. */
async function overPart(page: Page): Promise<Spot> {
  const spots = await faceSpots(page, page.request, 'mouse')
  if (spots.length === 0) throw new Error('no face of the bracket is in the view')
  return spots[0]!
}

test('P hides the default planes in the 3D view and P again shows them; the View menu tick follows', async ({ page }) => {
  await openApp(page)
  expect(await page.getByTestId('shell').getAttribute('data-mode')).toBe('desktop')
  // the default planes are drawn faint in the view, and the menu's Show planes is ticked
  expect(await planes(page)).toBe(3)
  expect(await planesTick(page)).toBe('true')
  const canvas = (await viewCanvas(page).boundingBox())!
  const spot = await overPart(page)
  await page.mouse.move(canvas.x + spot.x, canvas.y + spot.y)

  await page.keyboard.press('p')
  await expect.poll(() => planes(page)).toBe(0)
  expect(await planesTick(page)).toBe('false')
  // a hidden plane is not picked either: no pick of the view lands on a plane any more
  const cam = (await settled(page)).camera
  const grid: Spot[] = []
  for (let y = 8; y < cam.height - 8; y += 14) for (let x = 8; x < cam.width - 8; x += 14) grid.push({ x, y })
  expect((await pickAll(page, grid, 'mouse')).some((h) => h?.kind === 'plane')).toBe(false)

  // every plane's eye now shows hidden; the eye of one plane shows it alone, and it is drawn
  await expect(page.locator('[data-origin-row][data-hidden]')).toHaveCount(3)
  await page.locator('[data-origin-eye="Front"]').click()
  await expect.poll(() => planes(page)).toBe(1)
  expect((await readViewport(page)).hiddenDefaultPlanes).toEqual(expect.not.arrayContaining(['Front']))
  await expect(page.locator('[data-origin-row="Front"]')).not.toHaveAttribute('data-hidden', '')
  expect(await planesTick(page)).toBe('true')
  // one plane shows, so P hides all again, and P once more shows all
  await page.mouse.move(canvas.x + spot.x, canvas.y + spot.y)
  await page.keyboard.press('p')
  await expect.poll(() => planes(page)).toBe(0)
  await expect(page.locator('[data-origin-row][data-hidden]')).toHaveCount(3)
  await page.keyboard.press('p')
  await expect.poll(() => planes(page)).toBe(3)
  await expect(page.locator('[data-origin-row][data-hidden]')).toHaveCount(0)
  expect(await planesTick(page)).toBe('true')

  // the menu item does the same as the key
  await page.getByRole('button', { name: 'View', exact: true }).click()
  await page.getByRole('menuitemcheckbox', { name: /Show planes/ }).click()
  await expect.poll(() => planes(page)).toBe(0)
  expect(await planesTick(page)).toBe('false')
  await page.keyboard.press('p')
  await expect.poll(() => planes(page)).toBe(3)

  // Shift+P: planes, axes and mate connectors together (off, then on)
  await page.keyboard.press('Shift+P')
  await expect.poll(() => planes(page)).toBe(0)
  await page.keyboard.press('Shift+P')
  await expect.poll(() => planes(page)).toBe(3)
})

test('Y hides the clicked part, the part under the pointer when nothing is selected, and the boxed part; Shift+Y shows it again', async ({ page }) => {
  await openApp(page)
  const canvas = (await viewCanvas(page).boundingBox())!
  const at = (s: Spot) => ({ x: canvas.x + s.x, y: canvas.y + s.y })
  const spot = await overPart(page)
  const name = (await partRow(page, /.*/).first().innerText()).trim().split('\n')[0]!
  expect(await hiddenParts(page)).toBe(0)
  const id = await idOf(page.request, 'Bracket')
  const dirty = async () => ((await (await page.request.get(`/documents/${id}/tabs`)).json()).tabs as { dirty: boolean }[]).some((t) => t.dirty)
  expect(await dirty()).toBe(false)

  // 1. a click selects the part's face; Y hides the part
  await page.mouse.click(at(spot).x, at(spot).y)
  await expect.poll(async () => (await readViewport(page)).selection.count).toBe(1)
  await page.keyboard.press('y')
  await expect.poll(() => hiddenParts(page)).toBe(1)
  await expect(partRow(page, name)).toHaveCount(1)
  await expect(page.locator('.part-row[data-hidden]')).toHaveCount(1)
  // the eye in the Parts list agrees
  await expect(page.getByRole('button', { name: `Show ${name}` })).toBeVisible()
  // what Y changed on the server: the part's eye (set_part_override) as an edit of the tab, so the Document is marked unsaved,
  // as it is after a click on the eye in the Parts list; the desktop does not mark it
  expect(await dirty()).toBe(true)

  // 2. Shift+Y shows every part again
  await page.keyboard.press('Shift+Y')
  await expect.poll(() => hiddenParts(page)).toBe(0)
  await expect(page.locator('.part-row[data-hidden]')).toHaveCount(0)
  await expect(page.getByRole('button', { name: `Hide ${name}` })).toBeVisible()

  // 3. nothing selected: Y hides the part under the pointer
  const empty = await emptySpot(page, 'mouse')
  await page.mouse.click(at(empty).x, at(empty).y)
  await expect.poll(async () => (await readViewport(page)).selection.count).toBe(0)
  await page.mouse.move(at(spot).x, at(spot).y)
  await expect.poll(async () => (await readViewport(page)).selection.aimed?.part ?? null).not.toBeNull()
  await page.keyboard.press('y')
  await expect.poll(() => hiddenParts(page)).toBe(1)
  await page.keyboard.press('Shift+Y')
  await expect.poll(() => hiddenParts(page)).toBe(0)

  // 4. a box around the model selects its edges and faces; Y hides the part they belong to
  const cam = (await settled(page)).camera
  const box = { from: { x: 4, y: 4 }, to: { x: cam.width - 4, y: cam.height - 4 } }
  await page.mouse.move(at(box.from).x, at(box.from).y)
  await page.mouse.down()
  await page.mouse.move(at(box.to).x, at(box.to).y, { steps: 8 })
  await page.mouse.up()
  await expect.poll(async () => (await readViewport(page)).selection.count).toBeGreaterThan(1)
  await page.keyboard.press('y')
  await expect.poll(() => hiddenParts(page)).toBe(1)

  // a hidden part cannot be picked: nothing is under the pointer now, and Y with nothing to hide changes nothing
  await page.keyboard.press('Shift+Y')
  await expect.poll(() => hiddenParts(page)).toBe(0)
})

test('Y over the part with Shift-click selection hides it, and Shift+I and Shift+T act on it', async ({ page }) => {
  await openApp(page)
  const canvas = (await viewCanvas(page).boundingBox())!
  const spot = await overPart(page)
  await page.mouse.move(canvas.x + spot.x, canvas.y + spot.y)
  await expect.poll(async () => (await readViewport(page)).selection.aimed?.part ?? null).not.toBeNull()
  // Shift+T: see-through, again solid
  await page.keyboard.press('Shift+T')
  await expect.poll(async () => (await readViewport(page)).transparentParts).toBe(1)
  await page.keyboard.press('Shift+T')
  await expect.poll(async () => (await readViewport(page)).transparentParts).toBe(0)
  // Shift+I: only the part (the bracket has one, so the count stays), again all
  await page.keyboard.press('Shift+I')
  await page.keyboard.press('Shift+I')
  // Shift-click adds to the selection, then Y
  await page.keyboard.down('Shift')
  await page.mouse.click(canvas.x + spot.x, canvas.y + spot.y)
  await page.keyboard.up('Shift')
  await expect.poll(async () => (await readViewport(page)).selection.count).toBe(1)
  await page.keyboard.press('y')
  await expect.poll(() => hiddenParts(page)).toBe(1)
  await page.keyboard.press('Shift+Y')
  await expect.poll(() => hiddenParts(page)).toBe(0)
})
