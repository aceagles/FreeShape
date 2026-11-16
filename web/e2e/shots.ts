import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, discardAutosaveOf, holdLockClosed, idOf, releaseLock } from './support/docs.ts'
import { longPressTab } from './support/tabs.ts'
import { dragRollbackTo, featureMenu, featureRow, longPressFeature } from './support/features.ts'
import { partEye, partMenu, partRow, longPressPart } from './support/parts.ts'
import { canvasCentre, fingerDrag, twoFingers } from './support/pointer.ts'
import { settled } from './support/viewport.ts'

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
  /** Runs after the picture is taken: puts back what the steps changed in the served Document. */
  after?: (page: Page) => Promise<void>
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
  {
    name: 'view-cube-menu',
    mock: null, // the layout has the cube closed only
    steps: async (page) => {
      await openApp(page)
      await page.getByRole('button', { name: /View cube/ }).click()
      await page.getByRole('menu', { name: 'Standard views' }).waitFor()
    },
  },
  {
    name: 'debug-panel',
    mock: null, // the layout has no debug panel
    steps: async (page) => {
      await openApp(page)
      await page.getByTestId('link-status').click()
      await page.getByTestId('debug-panel').waitFor()
      // a few round trips, so the numbers are not dashes
      await page.getByTestId('rtt-median').filter({ hasText: 'ms' }).waitFor({ timeout: 10_000 })
      await page.waitForTimeout(3000)
    },
  },
  {
    name: 'dialog-open',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page)
      await page.evaluate(() => window.__freeshape?.shell.openDialog('Extrude 1'))
      await page.locator('[data-region="dialog"]').waitFor()
    },
  },
  {
    name: 'orbited',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      const c = await canvasCentre(page)
      await fingerDrag(page, { x: c.x, y: c.y }, { x: c.x + 70, y: c.y - 30 })
      await settled(page)
    },
  },
  {
    name: 'pinched-and-twisted',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      const c = await canvasCentre(page)
      const spoke = (deg: number, r: number, sign: 1 | -1) => ({
        x: c.x + sign * Math.cos((deg * Math.PI) / 180) * r,
        y: c.y + sign * Math.sin((deg * Math.PI) / 180) * r,
      })
      await twoFingers(page, spoke(0, 90, 1), spoke(0, 90, -1), spoke(30, 60, 1), spoke(30, 60, -1), 12)
      await settled(page)
    },
  },
  {
    name: 'tab-menu',
    mock: 'browsing', // the layout has no tab menu; the top bar and panel are the same
    steps: async (page) => {
      await openApp(page)
      await longPressTab(page)
    },
  },
  {
    name: 'add-tab-choice',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await page.getByRole('button', { name: 'Add tab' }).click()
      await page.getByRole('menu', { name: 'Add tab' }).waitFor()
    },
  },
  {
    name: 'rename-tab',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await longPressTab(page)
      await page.getByRole('menuitem', { name: 'Rename' }).click()
      await page.getByRole('textbox', { name: 'Name' }).waitFor()
    },
  },
  {
    name: 'delete-tab-confirm',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await longPressTab(page)
      await page.getByRole('menuitem', { name: 'Delete' }).click()
      await page.getByRole('dialog', { name: /Delete/ }).waitFor()
    },
  },
  {
    name: 'save-dirty',
    mock: 'browsing', // the layout draws the save button with its dot
    steps: async (page) => {
      await openApp(page)
      await renameFirstTab(page)
      await page.getByTestId('dirty-dot').waitFor()
    },
    after: restoreName,
  },
  {
    name: 'save-clean',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await renameFirstTab(page)
      await page.getByTestId('dirty-dot').waitFor()
      await page.getByRole('button', { name: 'Save, unsaved changes' }).click()
      await page.getByRole('button', { name: 'Save', exact: true }).waitFor()
    },
    after: restoreName,
  },
  {
    name: 'feature-panel',
    mock: 'browsing', // the bracket's own features; the layout's panel has Origin, Sketch 1, Extrude 1, Fillet 1 and a failed Hole
    steps: async (page) => {
      await openApp(page)
      await featureRow(page, 'Extrude 1').waitFor()
    },
  },
  {
    name: 'feature-selected',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await featureRow(page, 'Extrude 1').click()
      await page.locator('.panel-row[aria-current="true"]').waitFor()
    },
  },
  {
    name: 'feature-menu',
    mock: 'browsing', // the layout draws no row menu
    steps: async (page) => {
      await openApp(page)
      await longPressFeature(page, 'Fillet 1')
    },
  },
  {
    name: 'feature-suppressed',
    mock: 'browsing', // the layout draws no suppressed feature
    steps: async (page) => {
      await openApp(page)
      await featureMenu(page, 'Fillet 1', 'Suppress')
      await page.locator('.panel-row[data-state="suppressed"]').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'rollback-moved',
    mock: 'browsing', // the bar above Slot: Slot, Slot cut and Chamfer 1 greyed
    steps: async (page) => {
      await openApp(page)
      await dragRollbackTo(page, /^Slot$/)
      await page.locator('.panel-row[data-state="rolled-back"]').first().waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'feature-failed',
    mock: 'browsing', // Extrude 1 suppressed, so Fillet 1 has nothing to round: it fails with the engine's message
    steps: async (page) => {
      await openApp(page, { model: false })
      await featureMenu(page, 'Extrude 1', 'Suppress')
      await page.locator('.panel-row[data-state="failed"]').first().waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'feature-failed-selected',
    mock: 'browsing', // the failed row selected: the whole message, whatever its length
    steps: async (page) => {
      await openApp(page, { model: false })
      await featureMenu(page, 'Extrude 1', 'Suppress')
      await page.locator('.panel-row[data-state="failed"]').first().click()
    },
    after: closeAllDiscard,
  },
  {
    name: 'rename-feature',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await featureMenu(page, 'Slot cut', 'Rename')
      await page.getByRole('textbox', { name: 'Name' }).waitFor()
    },
  },
  {
    name: 'delete-feature-confirm',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await featureMenu(page, 'Chamfer 1', 'Delete')
      await page.getByRole('dialog', { name: /Delete "Chamfer 1"/ }).waitFor()
    },
  },
  {
    name: 'rail-features',
    mock: 'collapsed', // one icon per feature; the layout's rail has four and a failed Hole
    steps: async (page) => {
      await openApp(page)
      await page.getByRole('button', { name: 'Collapse feature panel' }).click()
      await featureRow(page, 'Extrude 1').waitFor({ state: 'detached' })
      await page.locator('.rail-feature').first().waitFor()
    },
  },
  {
    name: 'rail-failed-selected',
    mock: 'collapsed',
    steps: async (page) => {
      await openApp(page, { model: false })
      await featureMenu(page, 'Extrude 1', 'Suppress')
      await page.locator('.panel-row[data-state="failed"]').first().waitFor()
      await page.getByRole('button', { name: 'Collapse feature panel' }).click()
      await page.getByRole('button', { name: 'Chamfer 1' }).click()
      await page.locator('.rail-feature[data-state="suppressed"]').first().waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'undo-redo-enabled',
    mock: 'browsing', // the layout draws redo disabled
    steps: async (page) => {
      await openApp(page)
      await featureMenu(page, 'Fillet 1', 'Suppress')
      await page.locator('.panel-row[data-state="suppressed"]').waitFor()
      await featureMenu(page, 'Chamfer 1', 'Suppress')
      await page.getByRole('button', { name: 'Undo' }).click()
      await page.getByRole('button', { name: 'Redo' }).and(page.locator(':enabled')).waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'parts-list',
    mock: 'browsing', // the bracket's one part under the rollback bar, as in the layout; the eye and the colour swatch are new
    steps: async (page) => {
      await openApp(page)
      await partRow(page, 'Bracket').waitFor()
    },
  },
  {
    name: 'parts-several',
    mock: 'browsing', // the Hinge's Part Studio: several parts (the layout draws one)
    steps: async (page) => {
      await openApp(page, { document: 'Hinge' })
      await partRow(page, new RegExp('.')).first().waitFor()
      await waitParts(page, 2)
    },
  },
  {
    name: 'part-hidden',
    mock: 'browsing', // the layout draws no hidden part: one Hinge part hidden, its row greyed with a struck eye
    steps: async (page) => {
      await openApp(page, { document: 'Hinge' })
      const first = (await partRow(page, new RegExp('.')).first().locator('.row-name').textContent()) ?? ''
      await partEye(page, first).click()
      await page.locator('[data-part-row][data-hidden]').waitFor()
      await page.waitForFunction(() => (window.__freeshapeViewport?.hiddenParts ?? 0) === 1)
    },
    after: closeAllDiscard,
  },
  {
    name: 'part-selected',
    mock: 'browsing', // the part tapped in the list: its row selected and the part tinted amber in the view
    steps: async (page) => {
      await openApp(page, { document: 'Hinge' })
      await partRow(page, new RegExp('.')).first().click()
      await page.waitForFunction(() => window.__freeshapeViewport?.highlightedPart != null)
    },
  },
  {
    name: 'part-selected-bracket',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await partRow(page, 'Bracket').click()
      await page.waitForFunction(() => window.__freeshapeViewport?.highlightedPart != null)
    },
  },
  {
    name: 'part-menu',
    mock: 'browsing', // the layout draws no part menu
    steps: async (page) => {
      await openApp(page)
      await longPressPart(page, 'Bracket')
    },
  },
  {
    name: 'part-colour-choice',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await longPressPart(page, 'Bracket')
      await page.getByRole('menuitem', { name: 'Colour', exact: true }).click()
      await page.getByRole('group', { name: 'Colours' }).waitFor()
    },
  },
  {
    name: 'part-rename',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await partMenu(page, 'Bracket', 'Rename')
      await page.getByRole('textbox', { name: 'Name' }).waitFor()
    },
  },
  {
    name: 'part-coloured',
    mock: 'browsing', // a part given Dusty rose from the palette: the swatch and the model follow
    steps: async (page) => {
      await openApp(page)
      await partMenu(page, 'Bracket', 'Colour')
      await page.getByRole('button', { name: 'Dusty rose' }).click()
      await page.waitForFunction(() => window.__freeshapeViewport?.partColors['F2/1']?.[0] === 0.84)
    },
    after: closeAllDiscard,
  },
  {
    name: 'planes-all-features-failed',
    mock: 'browsing', // every part gone after the base extrude is suppressed: the default planes keep the model's size
    steps: async (page) => {
      await openApp(page)
      await featureMenu(page, 'Extrude 1', 'Suppress')
      await page.waitForFunction(() => window.__freeshapeViewport?.visibleParts === 0)
    },
    after: closeAllDiscard,
  },
  {
    name: 'planes-all-hidden',
    mock: 'browsing', // the only part hidden by its eye: the planes keep their size here too
    steps: async (page) => {
      await openApp(page)
      await partEye(page, 'Bracket').click()
      await page.waitForFunction(() => window.__freeshapeViewport?.visibleParts === 0)
    },
    after: closeAllDiscard,
  },
  {
    name: 'read-only-banner',
    mock: 'browsing', // no banner in the layout; the real server, with the lock written for the desktop before opening
    steps: async (page) => {
      await holdLockClosed(page.request, 'bracket', 'desktop')
      await openApp(page)
      await page.getByTestId('read-only-banner').waitFor()
    },
    after: async () => releaseLock('bracket'),
  },
  {
    name: 'home',
    mock: null, // the layout has no home screen
    steps: async (page) => {
      await openHome(page)
    },
  },
  {
    name: 'home-held-elsewhere',
    mock: null,
    steps: async (page) => {
      await holdLockClosed(page.request, 'bracket', 'desktop')
      await openHome(page)
      await page.getByText('opens read-only here').waitFor()
    },
    after: async () => releaseLock('bracket'),
  },
  {
    name: 'new-document-dialog',
    mock: null,
    steps: async (page) => {
      await openHome(page)
      await page.getByRole('button', { name: 'New Document' }).click()
      await page.getByRole('textbox', { name: 'Name' }).fill('Hinge bracket')
    },
  },
  {
    name: 'home-recoverable',
    mock: null,
    steps: async (page) => {
      await leaveAutosave(page)
      await openHome(page)
      await page.getByTestId('recover-mark').waitFor()
    },
    after: async (page) => discardAutosaveOf(page.request, 'Bracket'),
  },
  {
    name: 'recover-dialog',
    mock: null,
    steps: async (page) => {
      await leaveAutosave(page)
      await openHome(page)
      await page.getByTestId('recover-mark').waitFor()
      await page.locator('.doc-row', { has: page.locator('.doc-name', { hasText: /^Bracket$/ }) }).click() // the dialog comes, not the shell
      await page.getByRole('dialog', { name: /Recover unsaved changes/ }).waitFor()
    },
    after: async (page) => discardAutosaveOf(page.request, 'Bracket'),
  },
]

/** Close every Document and throw away what the state edited, so the next state starts from the served files. */
async function closeAllDiscard(page: Page): Promise<void> {
  await closeAll(page.request)
}

/** The home screen with every Document closed, the rows loaded. */
async function openHome(page: Page): Promise<void> {
  await closeAll(page.request)
  await page.goto('/')
  await page.getByTestId('home').waitFor()
  await page.locator('.doc-name', { hasText: /^Hinge$/ }).waitFor()
}

/** Leave an autosave of the bracket behind, the way a real session does: open, edit, close without saving. */
async function leaveAutosave(page: Page): Promise<void> {
  await closeAll(page.request)
  const id = await idOf(page.request, 'Bracket')
  await page.request.post(`/documents/${id}/open`, { data: {} })
  const res = await page.request.post(`/documents/${id}/tabs/T1/edit`, { data: { op: 'set_param', feature: 'F5', key: 'diameter', value: 7.0 } })
  if (!res.ok()) throw new Error(`the edit that leaves the autosave failed: ${res.status()}`)
  await page.request.post(`/documents/${id}/close`, { data: {} })
}

const NEW_NAME = 'Base plate'
let original = ''

/** Put the first tab's name back and save, so the next state starts from the Document as it was. */
async function restoreName(page: Page): Promise<void> {
  const doc = await idOf(page.request, 'Bracket')
  const tabs = ((await page.evaluate((d) => fetch(`/documents/${d}/tabs`).then((r) => r.json()), doc)) as { tabs: Array<{ id: string; name: string }> }).tabs
  const first = tabs[0]!
  if (first.name !== NEW_NAME) return
  await page.evaluate(
    async ([d, t, n]) => {
      const post = (u: string, b: object) => fetch(u, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) })
      await post(`/documents/${d}/tabs/${t}/rename`, { name: n })
      await post(`/documents/${d}/save`, {})
    },
    [doc, first.id, original] as const,
  )
}

async function renameFirstTab(page: Page): Promise<void> {
  original = (await page.locator('.tab-button').first().textContent()) ?? ''
  await longPressTab(page)
  await page.getByRole('menuitem', { name: 'Rename' }).click()
  const field = page.getByRole('textbox', { name: 'Name' })
  await field.fill(NEW_NAME)
  await field.press('Enter')
}

/** Waits until the viewport has drawn at least this many parts. */
async function waitParts(page: Page, n: number): Promise<void> {
  await page.waitForFunction((k) => (window.__freeshapeViewport?.visibleParts ?? 0) >= k, n)
}
