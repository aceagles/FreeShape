import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { openAssemblyTab, readAssembly, settledAssembly } from './support/assembly.ts'
import { closeAll } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { pickAll, project } from './support/pick.ts'
import type { Spot } from './support/pick.ts'
import { readViewport, settled, viewCanvas } from './support/viewport.ts'

// Slice S10, card I10.4: mate connectors made from the browser, on the served example Documents, under WebKit, in Desktop mode.
// On the bracket: the Mate connector tool, a click on the top face, Accept, and a triad is drawn on that face. In the hinge
// Assembly: the instance's menu adds an origin connector and a triad appears at the instance's origin. Every check polls the page:
// the server answers each pick and edit, the client then redraws.
test.use(desktopMode)
test.afterEach(async ({ page }) => closeAll(page.request))

const BASE = 6 // the bracket's flat face looking up on its base flange: z = 6 mm, centred at x = 35.34 mm (the highest +z face, at z = 45, is a thin strip a click cannot hit in the opening view)
const CENTRE: [number, number, number] = [35.3423, 0, BASE]
const dialog = (page: Page) => page.getByRole('dialog', { name: /^Mate connector/ })

test('Mate connector, a click on a flat face looking up, Accept: a triad is drawn on that face', async ({ page }) => {
  await openApp(page)
  await expect.poll(() => page.getByTestId('shell').getAttribute('data-mode')).toBe('desktop')
  expect((await readViewport(page)).connectors).toBe(0)
  await page.locator('[data-tool="mate_connector"]').click()
  await expect(dialog(page)).toBeVisible()
  // the rows: Name, the element, Owner part and Flip
  await expect(dialog(page).getByRole('textbox', { name: 'Name' })).toBeVisible()
  await expect(dialog(page).getByText('Face or edge (click it in the view)')).toBeVisible()
  await expect(dialog(page).getByText('Owner part')).toBeVisible()
  await expect(dialog(page).getByRole('checkbox', { name: /Flip/ })).toBeVisible()
  // the dialog's base arrives before a pick can be resolved
  await expect(page.getByTestId('shell-viewport')).toHaveAttribute('data-dialog-base', '')
  const canvas = (await viewCanvas(page).boundingBox())!
  const cam = (await settled(page)).camera
  // scan around where the face's centre projects for a spot that picks the face
  const centre0 = project(cam, CENTRE)
  const grid: Spot[] = []
  for (let y = Math.round(centre0.y) - 24; y <= centre0.y + 24; y += 2) for (let x = Math.round(centre0.x) - 24; x <= centre0.x + 24; x += 2) grid.push({ x, y })
  const hits = await pickAll(page, grid, 'mouse', ['face', 'edge']) // what the dialog's field lets a click take
  const i = hits.findIndex((h) => h?.kind === 'face' && Math.abs(h.point[2] - BASE) < 1e-3)
  expect(i).toBeGreaterThanOrEqual(0)
  await page.mouse.click(canvas.x + grid[i]!.x, canvas.y + grid[i]!.y)
  await expect(dialog(page).getByTestId('sel-chip')).toHaveCount(1)
  await expect(dialog(page).getByTestId('sel-chip')).toContainText('Face')
  await dialog(page).getByRole('textbox', { name: 'Name' }).fill('Top pin')
  await dialog(page).getByRole('textbox', { name: 'Name' }).press('Tab')
  await expect(page.getByTestId('preview-error')).toHaveCount(0)
  await page.locator('.dialog-header').getByRole('button', { name: 'Accept', exact: true }).click()
  await expect(dialog(page)).toHaveCount(0)
  await expect.poll(async () => (await readViewport(page)).connectors).toBe(1)
  // the triad stands on the face, at its centre: the view projects that point to where the triad is drawn
  const at = await page.evaluate(() => window.__freeshapeViewport!.connectorScreens()[0]!)
  const after = (await settled(page)).camera
  const centre = project(after, CENTRE)
  expect(Math.hypot(at.x - centre.x, at.y - centre.y)).toBeLessThan(2)
  expect(at.id).toMatch(/^F\d+$/)
})

test('in the hinge Assembly the instance menu adds an origin connector and a triad appears at its origin', async ({ page }) => {
  await openApp(page, { document: 'Hinge', model: false })
  await openAssemblyTab(page, 2)
  const before = (await readAssembly(page)).connectors
  const row = page.locator('[data-instance="I1"]')
  await row.click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Add mate connector to instance origin' }).click()
  await expect.poll(async () => (await readAssembly(page)).connectors).toBe(before + 1)
  const view = await settledAssembly(page)
  const origin = view.placements['I1']!
  const spot = project(view.camera as Parameters<typeof project>[0], [origin[0]![3]!, origin[1]![3]!, origin[2]![3]!])
  const triads = await page.evaluate(() => window.__freeshapeAssembly!.connectorScreens().filter((c) => c.instance === 'I1'))
  expect(triads.length).toBe(1)
  expect(Math.hypot(triads[0]!.x - spot.x, triads[0]!.y - spot.y)).toBeLessThan(2)
  // a second one for the same instance adds nothing
  await row.click({ button: 'right' })
  await page.getByRole('menuitem', { name: 'Add mate connector to instance origin' }).click()
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect.poll(async () => (await readAssembly(page)).connectors).toBe(before + 1)
})
