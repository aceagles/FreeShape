import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './app.ts'
import { openAssemblyTab, readAssembly, settledAssembly } from './assembly.ts'
import { idOf } from './docs.ts'
import { project } from './pick.ts'
import type { Spot } from './pick.ts'
import { viewCanvas } from './viewport.ts'

/** The mate dialog's dialog (card I10.6): its title is the mate's name, "Revolute 1". */
export const mateDialog = (page: Page) => page.getByRole('dialog', { name: /^(Fastened|Revolute|Slider|Cylindrical|Planar|Pin slot|Parallel|Ball) \d/ })
export const mateRows = (page: Page) => page.getByTestId('mate-list').locator('li')

/**
 * Where the circular edge of a hole is on screen: the circle of radius `r` about `centre` in the part's own coordinates (so the
 * placement moves it), at its point farthest to the right on screen, which is clear of a dialog floating at the view's left. The spots
 * around that point are asked which one picks an edge of the instance.
 */
export async function holeSpot(page: Page, instance: string, centre: readonly [number, number, number], r = 4): Promise<Spot> {
  const view = await settledAssembly(page)
  const T = view.placements[instance]!
  const cam = view.camera as Parameters<typeof project>[0]
  const onCircle = Array.from({ length: 24 }, (_, k) => {
    const local = [centre[0] + r * Math.cos((k * Math.PI) / 12), centre[1] + r * Math.sin((k * Math.PI) / 12), centre[2]]
    const world = [0, 1, 2].map((row) => T[row]![0]! * local[0]! + T[row]![1]! * local[1]! + T[row]![2]! * local[2]! + T[row]![3]!) as unknown as [number, number, number]
    return project(cam, world)
  })
  const at = onCircle.reduce((best, p) => (p.x > best.x ? p : best))
  const grid: Spot[] = []
  for (let dy = -6; dy <= 6; dy++) for (let dx = -6; dx <= 6; dx++) grid.push({ x: Math.round(at.x) + dx, y: Math.round(at.y) + dy })
  const hits = await page.evaluate((g) => g.map((s) => window.__freeshapeAssembly!.pickAt(s.x, s.y, 'mouse', ['edge'])), grid)
  let best = -1
  hits.forEach((h, i) => {
    if (h?.kind !== 'edge' || (h as { instance?: string }).instance !== instance) return
    if (best < 0 || Math.hypot(grid[i]!.x - at.x, grid[i]!.y - at.y) < Math.hypot(grid[best]!.x - at.x, grid[best]!.y - at.y)) best = i
  })
  const seen = [...new Set(hits.filter((h) => h !== null).map((h) => `${h!.kind}:${(h as { instance?: string }).instance}:${h!.index}`))]
  expect(best, `an edge of ${instance} near ${JSON.stringify(at)}; picked ${JSON.stringify(seen)}`).toBeGreaterThanOrEqual(0)
  return grid[best]!
}

/** A mouse click at a spot of the view (CSS pixels from the canvas's top left). */
export async function clickView(page: Page, spot: Spot): Promise<void> {
  const box = (await viewCanvas(page).boundingBox())!
  await page.mouse.click(box.x + spot.x, box.y + spot.y)
}

/** The Base plate's pin hole (the circle of radius 4 about (15, 20) on its top face) and the Arm's (about (6, 66) on its top face), in their own coordinates. */
export const BASE_HOLE = [15, 20, 10] as const
export const ARM_HOLE = [6, 66, 6] as const

/**
 * The hinge in its Assembly tab with its two mates deleted from the Mates list (the context menu's Delete, Desktop mode) and the Arm moved
 * clear of the Base plate: the example has the Arm lying on the plate with its hole over the plate's, so the plate's pin edge is under it.
 * The view is fitted.
 */
export async function hingeWithoutMates(page: Page, via: 'menu' | 'edit' = 'menu'): Promise<void> {
  await openApp(page, { document: 'Hinge', model: false })
  await openAssemblyTab(page, 2)
  await expect(mateRows(page)).toHaveCount(2)
  const doc = await idOf(page.request, 'Hinge')
  if (via === 'menu') {
    for (const left of [1, 0]) {
      await mateRows(page).first().click({ button: 'right' })
      await page.getByRole('menuitem', { name: 'Delete', exact: true }).click()
      await expect(mateRows(page)).toHaveCount(left)
    }
  } else {
    const ids = await mateRows(page).evaluateAll((rows) => rows.map((r) => (r as HTMLElement).dataset['mateRow']))
    expect((await page.request.post(`/documents/${doc}/tabs/T2/edit`, { data: { op: 'delete_mates', ids } })).ok()).toBe(true)
    await expect(mateRows(page)).toHaveCount(0)
  }
  await expect.poll(async () => (await readAssembly(page)).mateEnds).toBe(0)
  // a motion: 60 mm along x from where the Arm is
  const T = [[1, 0, 0, 60], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
  expect((await page.request.post(`/documents/${doc}/tabs/T2/edit`, { data: { op: 'move_instance', instance: 'I2', T } })).ok()).toBe(true)
  await expect.poll(async () => (await readAssembly(page)).placements['I2']![0]![3]).toBe(69)
  await page.locator('[data-tool="fit"]').click()
  await settledAssembly(page)
}

/** Both pin holes clicked, the First field then the Second, and the dialog's preview drawn. */
export async function pickBothHoles(page: Page): Promise<void> {
  const field = (name: string) => mateDialog(page).getByRole('group', { name })
  await expect(field('First mate connector')).toHaveAttribute('data-active', '')
  await clickView(page, await holeSpot(page, 'I1', BASE_HOLE))
  await expect(field('First mate connector').getByTestId('sel-chip')).toContainText('circular edge')
  await expect(field('Second mate connector')).toHaveAttribute('data-active', '')
  await clickView(page, await holeSpot(page, 'I2', ARM_HOLE))
  await expect(field('Second mate connector').getByTestId('sel-chip')).toContainText('circular edge')
}
