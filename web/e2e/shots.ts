import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'

/**
 * The states `sh scripts/web/shots.sh` captures: a name and the steps that reach the state from a fresh page.
 * A card that introduces a state adds an entry here, then looks at the images (see STANDING.md, "whole screen").
 *
 *   name   the file name stem: web/shots/out/<name>-<width>x<height>.png (mock | client side by side) and
 *          <name>-<width>x<height>.client.png (the client alone)
 *   mock   the state of web/design/layout.html to put on the left: browsing, flyout, dialog, sketch, constrain,
 *          radial or collapsed. Leave it out when it is the same as `name`; null when the mock has no such state.
 *   steps  drives the page to the state. It starts with `page` at the size being captured, nothing loaded yet. A
 *          state whose steps throw is reported and skipped; the other states are still captured.
 */
export interface ShotState {
  name: string
  mock?: string | null
  steps: (page: Page) => Promise<void>
}

/** Sizes of the screen, as the layout has them (key = the mock's size key). */
export const SIZES = [
  { key: '1024', width: 1024, height: 768 },
  { key: '1366', width: 1366, height: 1024 },
] as const

export const shots: ShotState[] = [
  {
    name: 'browsing',
    steps: async (page) => {
      await openApp(page)
    },
  },
  {
    name: 'panel-collapsed',
    mock: 'collapsed',
    steps: async (page) => {
      await openApp(page)
      await page.getByRole('button', { name: 'Collapse feature panel' }).click()
    },
  },
]
