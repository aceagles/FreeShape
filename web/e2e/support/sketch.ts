import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import type { SketchReport } from '../../src/sketch/mode.ts'
import type { SketchHit } from '../../src/sketch/pick.ts'
import type { Uv } from '../../src/sketch/wire.ts'
import { readViewport } from './viewport.ts'
import { featureRow } from './features.ts'

/** What the hook reports of sketch mode that survives a trip out of the page (its three functions are the helpers below). */
export type SketchState = Omit<SketchReport, 'screenOf' | 'pickAt' | 'uvAt' | 'handleOf'>

/** The sketch mode of the view, as the hook reports it; throws when no sketch is open in the view. */
export async function readSketch(page: Page): Promise<SketchState> {
  const s = (await readViewport(page)).sketch
  if (s === null) throw new Error('no sketch is open in the view')
  return s
}

/** Where a sketch item is on screen now (CSS pixels from the canvas's top left). */
export async function screenOf(page: Page, kind: SketchHit['kind'], id: string): Promise<{ x: number; y: number }> {
  const at = await page.evaluate(([k, i]) => window.__freeshapeViewport?.sketch?.screenOf(k as SketchHit['kind'], i) ?? null, [kind, id] as const)
  if (at === null) throw new Error(`no ${kind} ${id} in the sketch`)
  // only the position: the layout's own item (with its id) spread into a pointer step would overwrite the step's pointer id
  return { x: at.x, y: at.y }
}

/** Where the Drag handle of a selected item is on screen, or null when it shows none. */
export const handleOf = (page: Page, kind: 'point' | 'entity' | 'dimension', id: string): Promise<{ x: number; y: number } | null> =>
  page.evaluate(([k, i]) => window.__freeshapeViewport?.sketch?.handleOf(k as 'point' | 'entity' | 'dimension', i) ?? null, [kind, id] as const)

/** What a pointer at a view position picks in the sketch. */
export const pickSketchAt = (page: Page, x: number, y: number, pointer: 'pen' | 'touch' | 'mouse'): Promise<SketchHit | null> =>
  page.evaluate(([px, py, p]) => window.__freeshapeViewport?.sketch?.pickAt(px, py, p) ?? null, [x, y, pointer] as const)

/** The sketch point under a view position: where the ray of the view meets the sketch plane. */
export const uvAt = (page: Page, x: number, y: number): Promise<Uv | null> => page.evaluate(([px, py]) => window.__freeshapeViewport?.sketch?.uvAt(px, py) ?? null, [x, y] as const)

/** Opens the Part Studio's sketch feature `name` through the test hook (the way in from the screen is card I7.13) and waits for the sketch strip and the drawn sketch. */
export async function openSketchFeature(page: Page, name: string | RegExp): Promise<void> {
  const id = await featureRow(page, name).getAttribute('data-feature')
  if (id === null) throw new Error(`no feature row ${name}`)
  await page.evaluate((feature) => window.__freeshape!.openSketch({ feature }), id)
  await waitForSketch(page)
}

/** Opens a new sketch on a standard plane through the test hook. */
export async function openNewSketch(page: Page, plane: 'Top' | 'Front' | 'Right'): Promise<void> {
  await page.evaluate((name) => window.__freeshape!.openSketch({ plane: { kind: 'std', name } }), plane)
  await waitForSketch(page)
}

async function waitForSketch(page: Page): Promise<void> {
  await expect(page.locator('[data-strip="sketch"]')).toBeVisible({ timeout: 20_000 })
  await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.paints ?? 0) > 0)
}

/** Lets the view draw once more after a gesture, then reads the sketch. */
export async function settledSketch(page: Page): Promise<SketchState> {
  await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))))
  return readSketch(page)
}
