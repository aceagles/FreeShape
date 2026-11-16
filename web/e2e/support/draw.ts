import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { canvasCentre, sendPointer } from './pointer.ts'
import type { Point } from './pointer.ts'
import { project } from './pick.ts'
import type { Spot } from './pick.ts'
import { screenOf, uvAt } from './sketch.ts'
import { settled } from './viewport.ts'

/** Drawing in an open sketch with the pen the way the iPad does it (pointer events stand in for the Pencil). */
export const PEN = 10

/** The screen position of a sketch point (u, v in mm), read off the view so it holds in any 2D turn. */
export async function screenFor(page: Page, u: number, v: number): Promise<Point> {
  const o = await screenOf(page, 'point', 'origin')
  const a = (await uvAt(page, o.x + 100, o.y))!
  const b = (await uvAt(page, o.x, o.y + 100))!
  const m = [
    [a[0] / 100, b[0] / 100],
    [a[1] / 100, b[1] / 100],
  ]
  const det = m[0]![0]! * m[1]![1]! - m[0]![1]! * m[1]![0]!
  return { x: o.x + (m[1]![1]! * u - m[0]![1]! * v) / det, y: o.y + (-m[1]![0]! * u + m[0]![0]! * v) / det }
}

export const place = (page: Page, p: Point) =>
  sendPointer(page, [
    { phase: 'down', kind: 'pen', id: PEN, x: p.x - 3, y: p.y },
    { phase: 'move', kind: 'pen', id: PEN, ...p },
    { phase: 'up', kind: 'pen', id: PEN, ...p, pressure: 0 },
  ])

/** Choose a drawing tool with the quick menu: hold the pen mid-view, slide to the button, lift on it. */
export async function chooseWithRing(page: Page, id: string): Promise<void> {
  const c = await canvasCentre(page)
  await sendPointer(page, [{ phase: 'down', kind: 'pen', id: PEN, ...c }])
  await expect(page.getByTestId('quick-line')).toBeVisible()
  const b = (await page.getByTestId(`quick-${id}`).boundingBox())!
  const box = (await page.getByTestId('viewport').boundingBox())!
  const to = { x: b.x + b.width / 2 - box.x, y: b.y + b.height / 2 - box.y }
  await sendPointer(page, [
    { phase: 'move', kind: 'pen', id: PEN, x: (c.x + to.x) / 2, y: (c.y + to.y) / 2 },
    { phase: 'move', kind: 'pen', id: PEN, ...to },
    { phase: 'up', kind: 'pen', id: PEN, ...to, pressure: 0 },
  ])
  await expect(page.getByTestId('quick-menu')).toHaveCount(0)
}


/**
 * A spot in the view where the pen picks a sketch region (no edge or vertex of the part wins there), found over the
 * rectangle `u0`..`u1` by `v0`..`v1` (default u -28 to -12, v -8 to 8) of a sketch on the Top plane, in whatever view the page shows now.
 */
export async function regionSpot(page: Page, over: { u0: number; u1: number; v0: number; v1: number } = { u0: -28, u1: -12, v0: -8, v1: 8 }): Promise<Spot> {
  const cam = (await settled(page)).camera
  const spots: Spot[] = []
  for (let i = 0; i <= 4; i++) for (let j = 0; j <= 4; j++) spots.push(project(cam, [over.u0 + ((over.u1 - over.u0) * i) / 4, over.v0 + ((over.v1 - over.v0) * j) / 4, 0]))
  const hits = await page.evaluate((g) => g.map((s) => window.__freeshapeViewport!.pickAt(s.x, s.y, 'pen')), spots)
  const i = hits.findIndex((h) => h?.kind === 'region')
  if (i < 0) throw new Error('no spot of the view picks the sketch region')
  return spots[i]!
}
