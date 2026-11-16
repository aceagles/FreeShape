import type { Locator, Page } from '@playwright/test'
import type { ViewportReport } from '../../src/viewport/controller.ts'

/** The viewport's canvas: the one element every pointer sequence goes to. */
export const viewCanvas = (page: Page): Locator => page.locator('[data-region="viewport"] canvas').first()

/** Reads `window.__freeshapeViewport` (parts, faces, edges, triangles, planes, camera with roll, frames drawn). */
export async function readViewport(page: Page): Promise<ViewportReport> {
  const report = await page.evaluate(() => window.__freeshapeViewport ?? null)
  if (report === null) throw new Error('window.__freeshapeViewport is not there: the viewport has not mounted (no WebGL?)')
  return report
}

/** Waits until a model is on screen: parts in the scene and at least one frame drawn after them. */
export async function waitForModel(page: Page, timeout = 30_000): Promise<ViewportReport> {
  await page.waitForFunction(
    () => {
      const v = window.__freeshapeViewport
      return v !== undefined && v.parts > 0 && v.frames > 0
    },
    undefined,
    { timeout },
  )
  return readViewport(page)
}

/** Waits for the next frame to be drawn (after a camera change), then reads the hook. */
export async function nextFrame(page: Page, timeout = 10_000): Promise<ViewportReport> {
  const before = (await readViewport(page)).frames
  await page.waitForFunction((n) => (window.__freeshapeViewport?.frames ?? 0) > n, before, { timeout })
  return readViewport(page)
}

/** Lets two frames pass (so a camera change from the last gesture has been drawn), then reads the hook. The view draws on demand, so this never waits for a frame that nothing asks for. */
export async function settled(page: Page): Promise<ViewportReport> {
  await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))))
  return readViewport(page)
}
