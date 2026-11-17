import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, idOf } from './support/docs.ts'
import { faceSpots } from './support/pick.ts'
import { fingerTap } from './support/pointer.ts'
import { readViewport } from './support/viewport.ts'

// Slice S6, the feature toolbar (card I6.2) in the real client against `freeshape serve`: select something in the view,
// tap a tool, see the create dialog with the selection in its profile field, accept, and see the new feature in the list.
// This test selects a face; the Extrude dialog's profile field takes faces and regions alike, and a region of a sketch the
// eye shows can be picked in the view too (card I7.13, s7_start.spec.ts).

test.afterEach(async ({ page }) => closeAll(page.request))

const features = (page: Page) => page.locator('.panel-row[data-feature]')
const group = (page: Page, name: string) => page.locator('.tool-strip').getByRole('button', { name: new RegExp(`^${name}`) })
const strip = (page: Page) => page.locator('.tool-strip')

test('select a face, tap Extrude: the create dialog has it in its profile field; Accept adds a feature', async ({ page, request }) => {
  await openApp(page)
  await expect.poll(() => features(page).count()).toBeGreaterThan(0)
  const before = await features(page).count()
  const [spot] = await faceSpots(page, request, 'touch')
  await fingerTap(page, spot!)
  await expect(page.getByTestId('selection-count')).toHaveText('1 face')
  await group(page, 'Create').click()
  await expect(page.getByRole('menu', { name: 'Create' })).toBeVisible()
  await page.getByRole('menuitem', { name: /^Extrude/ }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  // the profile field shows the pick as a readable chip (I5.7), not the raw Element name
  await expect(dialog.getByTestId('dialog-form')).toContainText(/Face \d+ of Bracket/)
  await expect(dialog.getByTestId('dialog-form')).toContainText('1 item')
  // the tool took the selection
  await expect(page.getByTestId('selection-count')).toHaveText('Nothing selected')
  expect((await readViewport(page)).selection.count).toBe(0)
  const rev = (await readViewport(page)).revision!
  await dialog.getByRole('button', { name: 'Accept', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r, rev)
  await expect(features(page)).toHaveCount(before + 1)
})

test('press on a group, move onto a row and release starts that tool (a real press-move-release)', async ({ page }) => {
  await openApp(page)
  const g = (await group(page, 'Modify').boundingBox())!
  await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2)
  await page.mouse.down()
  const list = page.getByRole('menu', { name: 'Modify' })
  await expect(list).toBeVisible()
  const chamfer = list.getByRole('menuitem', { name: /^Chamfer/ })
  const c = (await chamfer.boundingBox())!
  await page.mouse.move(c.x + c.width / 2, c.y + c.height / 2, { steps: 5 })
  await expect(list.locator('.group-row[data-tool="chamfer"]')).toHaveAttribute('data-aim', '')
  await page.mouse.up()
  // Chamfer starts: its dialog opens (with nothing selected the dialog still opens)
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('menu')).toHaveCount(0)
  await page.locator('.dialog-header').getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('a tool with a dialog the client cannot draw is disabled with the desktop-only note, from the types route', async ({ page }) => {
  await openApp(page)
  const id = await idOf(page.request, 'Bracket')
  const tabs = (await (await page.request.get(`/documents/${id}/tabs`)).json()).tabs as Array<{ id: string }>
  const types = ((await (await page.request.get(`/documents/${id}/tabs/${tabs[0]!.id}/features/types`)).json()).types as Array<{ type: string; standard: boolean }>)
  const custom = new Set(types.filter((t) => !t.standard).map((t) => t.type))
  const known = types.map((t) => t.type)
  for (const name of ['Create', 'Modify', 'Copy']) {
    await group(page, name).click()
    const list = page.getByRole('menu', { name })
    await expect(list).toBeVisible()
    // a row reads 'off' until the types route has answered; hold the check until it has for every type the server lists
    await expect.poll(() => list.locator('.group-row').evaluateAll((els, k) => els.filter((e) => k.includes((e as HTMLElement).dataset.tool!) && (e as HTMLElement).dataset.state === 'off').length, known)).toBe(0)
    const rows = await list.locator('.group-row').evaluateAll((els) => els.map((e) => ({ tool: (e as HTMLElement).dataset.tool!, state: (e as HTMLElement).dataset.state!, note: e.textContent?.includes('Desktop only for now') ?? false })))
    for (const r of rows) {
      if (custom.has(r.tool)) expect(r).toMatchObject({ state: 'desktop', note: true })
      else expect(r.note).toBe(false)
    }
    await page.getByTestId('strip-scrim').click({ position: { x: 900, y: 700 } })
    await expect(list).toHaveCount(0)
  }
})

test('pins: six fit at 1024 wide with nothing clipped, the strip is full at the cap, and a pin survives a reload', async ({ page }) => {
  await openApp(page)
  const pin = async (name: { group: string; tool: string }) => {
    await group(page, name.group).click()
    await page.getByRole('menu', { name: name.group }).getByRole('button', { name: `Pin ${name.tool}`, exact: true }).click()
    await page.getByTestId('strip-scrim').click({ position: { x: 900, y: 700 } })
  }
  const six = [{ group: 'Modify', tool: 'Fillet' }, { group: 'Modify', tool: 'Chamfer' }, { group: 'Modify', tool: 'Shell' }, { group: 'Modify', tool: 'Hole' }, { group: 'Create', tool: 'Extrude' }, { group: 'Create', tool: 'Revolve' }]
  for (const p of six) await pin(p)
  await expect(strip(page).locator('.strip-button')).toHaveCount(5 + 6)
  const box = (await strip(page).boundingBox())!
  const overflow = await strip(page).evaluate((el) => el.scrollWidth - el.clientWidth)
  expect(overflow).toBe(0)
  for (const b of await strip(page).locator('.strip-button').all()) {
    const r = (await b.boundingBox())!
    expect(r.x).toBeGreaterThanOrEqual(box.x)
    expect(r.x + r.width).toBeLessThanOrEqual(box.x + box.width)
    expect([r.width, r.height]).toEqual([72, 56])
  }
  await group(page, 'Modify').click()
  const full = page.getByTestId('strip-full')
  await expect(full).toHaveText('The strip is full. Unpin a tool to pin another.')
  await page.getByRole('menu', { name: 'Modify' }).getByRole('button', { name: 'Pin Draft', exact: true }).click()
  await expect(strip(page).locator('.strip-button')).toHaveCount(5 + 6)
  await page.getByTestId('strip-scrim').click({ position: { x: 900, y: 700 } })
  await page.reload()
  await expect(page.getByTestId('shell')).toBeVisible({ timeout: 30_000 })
  await expect(strip(page).locator('.strip-button')).toHaveCount(5 + 6)
  await expect(strip(page).locator('[data-pinned]')).toHaveAttribute('data-tool', 'extrude')
  // clean up the user state of this browser context
  await page.evaluate(() => window.localStorage.removeItem('freeshape.toolPins'))
})
