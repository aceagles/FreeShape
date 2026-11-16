import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { dropFixture, FIXTURE, makeFixture } from './support/create.ts'
import { closeAll, discardAutosaveOf, holdLockClosed, idOf, releaseLock } from './support/docs.ts'
import { longPressTab } from './support/tabs.ts'
import { dragRollbackTo, featureMenu, featureRow, longPressFeature } from './support/features.ts'
import { partEye, partMenu, partRow, longPressPart } from './support/parts.ts'
import { edgeSpot, faceSpots, planeSpot, vertexSpot } from './support/pick.ts'
import type { Spot } from './support/pick.ts'
import { canvasCentre, fingerDrag, fingerTap, penStroke, sendPointer, twoFingers } from './support/pointer.ts'
import type { PointerStep } from './support/pointer.ts'
import { chooseWithRing, place, regionSpot, screenFor } from './support/draw.ts'
import { openNewSketch, openSketchFeature, screenOf, settledSketch, uvAt } from './support/sketch.ts'
import { settled } from './support/viewport.ts'
import { gizmoOf, openAssemblyTab, readAssembly, settledAssembly } from './support/assembly.ts'

/**
 * The states `sh scripts/web/shots.sh` captures: a name and the steps that reach the state from a fresh page.
 * A card that introduces a state adds an entry here, then looks at the images (see STANDING.md, "whole screen").
 *
 *   name   the file name stem: web/shots/out/<name>-<width>x<height>.png (mock | client side by side) and
 *          <name>-<width>x<height>.client.png (the client alone)
 *   mock   the state of web/design/layout.html to put on the left: browsing, flyout, dialog, sketch, constrain,
 *          radial or collapsed. Leave it out when it is the same as `name`; null when the mock has no such state.
 *   desktop  a Desktop-mode state has no mock: the shot of the desktop program to put on the left instead
 *          (desktop-studio, desktop-menu-document, desktop-menu-view, desktop-menu-select, desktop-dialog,
 *          desktop-sketch, desktop-assembly, desktop-context, desktop-palette, desktop-shortcuts)
 *   steps  drives the page to the state. It starts with `page` at the size being captured, nothing loaded yet. A
 *          state whose steps throw is reported and skipped; the other states are still captured.
 */
export interface ShotState {
  name: string
  /** The client's mode (default tablet). A desktop state is captured at DESKTOP_SIZES; it has no mock, only `desktop`. */
  mode?: 'desktop' | 'tablet'
  /** Desktop mode: the state of the desktop program (web/shots/desktop/<desktop>-<w>x<h>.png, written by scripts/web/desktop_shots.py) to put on the left. */
  desktop?: string
  mock?: string | null
  steps: (page: Page) => Promise<void>
  /** Runs after the picture is taken: puts back what the steps changed in the served Document. */
  after?: (page: Page) => Promise<void>
}

/** Double tap the bracket's Extrude 1 and wait for its dialog with the pad on Depth. */
async function openExtrudeDialog(page: Page): Promise<void> {
  await openApp(page)
  await featureRow(page, 'Extrude 1').dblclick()
  await page.getByTestId('pad').waitFor()
}
async function padKeys(page: Page, ...names: string[]): Promise<void> {
  for (const n of names) await page.getByTestId('pad').getByRole('button', { name: n, exact: true }).click()
}

/** Double tap the bracket's Fillet 1, tap its first selection field, and wait until the view picks for it. */
async function openFilletField(page: Page): Promise<void> {
  await openApp(page)
  await featureRow(page, 'Fillet 1').dblclick()
  const field = page.locator('[data-testid^="sel-"]').first()
  await field.getByTestId('sel-chip').first().waitFor()
  await field.getByRole('button', { name: /Tap, then pick/ }).click()
  await page.locator('[data-testid^="sel-"][data-active]').first().waitFor()
  // the view draws the model before Fillet 1 (card F3): picks resolve against it
  await page.locator('[data-dialog-base]').waitFor()
  await settled(page)
}
/** Pick an edge the field does not hold yet (a pick of one it holds would take it out). */
async function penPickEdge(page: Page): Promise<void> {
  const chips = await page.locator('[data-testid^="sel-"]').first().getByTestId('sel-chip').allTextContents()
  const held = chips.flatMap((t) => /^Edge (\d+) of /.exec(t)?.slice(1).map((n) => Number(n) - 1) ?? [])
  const spot = await edgeSpot(page, page.request, 'pen', held)
  await sendPointer(page, [{ phase: 'down', kind: 'pen', id: 7, x: spot.x, y: spot.y }])
  await sendPointer(page, [{ phase: 'up', kind: 'pen', id: 7, x: spot.x, y: spot.y, pressure: 0 }])
}

/** Sizes of the screen, as the layout has them (key = the mock's size key). */
/** The `sketch.opened` answer of the page's live connection, for states that need the sketch's ids and the model's edges (I7.12). Call before the sketch is opened. */
type EditOpened = { sketch: { points: Record<string, [number, number]>; entities: Record<string, { type: string; p1?: string; p2?: string }> }; edges: { pick: unknown; points: [number, number][] }[] }
function editOpened(page: Page): () => EditOpened {
  let got: EditOpened | null = null
  page.on('websocket', (ws) =>
    ws.on('framereceived', (f) => {
      if (typeof f.payload !== 'string' || !f.payload.includes('sketch.opened')) return
      try {
        const m = JSON.parse(f.payload) as { type?: string }
        if (m.type === 'sketch.opened') got = m as unknown as EditOpened
      } catch {
        // not JSON
      }
    }),
  )
  return () => got!
}

/** Desktop mode: click the first face the mouse picks in the view, and wait until the chip says so. */
async function pickFaceWithMouse(page: Page): Promise<void> {
  const [face] = await faceSpots(page, page.request, 'mouse')
  await sendPointer(page, [{ phase: 'move', kind: 'mouse', id: 20, ...face! }])
  await sendPointer(page, [
    { phase: 'down', kind: 'mouse', id: 20, ...face!, button: 0 },
    { phase: 'up', kind: 'mouse', id: 20, ...face!, button: 0 },
  ])
  await page.getByTestId('selection-count').filter({ hasText: '1 face' }).waitFor()
}

/** Sizes a Desktop-mode state is captured at (the sizes of scripts/web/desktop_shots.py too). */
export const DESKTOP_SIZES = [
  { key: '1440', width: 1440, height: 900 },
  { key: '1920', width: 1920, height: 1080 },
] as const

export const SIZES = [
  { key: '1024', width: 1024, height: 768 },
  { key: '1366', width: 1366, height: 1024 },
] as const

/** Swings the finger `radians` about the gizmo's origin from the ring on the leaf's own Z (the pin's axis), then waits until the view shows the leaf turned; `release` lifts the finger, else it stays down. */
async function turnRing(page: Page, radians: number, release: boolean): Promise<void> {
  const before = (await readAssembly(page)).placements['I2']!
  const g = await gizmoOf(page)
  const a = g.axes[2]!
  if (!a.turn) throw new Error('the pin ring has no handle')
  const dx = a.turn.x - g.origin.x
  const dy = a.turn.y - g.origin.y
  const at = (t: number) => ({ x: g.origin.x + dx * Math.cos(t) - dy * Math.sin(t), y: g.origin.y + dx * Math.sin(t) + dy * Math.cos(t) })
  const steps: PointerStep[] = [{ phase: 'down', kind: 'touch', id: 9, ...at(0) }]
  for (let k = 1; k <= 6; k++) steps.push({ phase: 'move', kind: 'touch', id: 9, ...at((radians * k) / 6) })
  if (release) steps.push({ phase: 'up', kind: 'touch', id: 9, ...at(radians) })
  await sendPointer(page, steps)
  // each move_to solves on the server, which takes a while: wait for the answer, never a fixed time
  await page.waitForFunction((b) => {
    const now = window.__freeshapeAssembly?.placements['I2']
    return !!now && now.some((row, i) => row.some((v, j) => Math.abs(v - b[i]![j]!) > 0.05))
  }, before, { timeout: 30_000 })
  await settledAssembly(page)
}

export const shots: ShotState[] = [
  {
    // I9.2: the Desktop-mode chrome (menus, bottom tab strip, resizable left column, floating dialog); 1440 x 900 and 1920 x 1080, beside the desktop program (I9.13)
    name: 'desktop-studio',
    mode: 'desktop',
    desktop: 'desktop-studio',
    steps: async (page) => {
      await openApp(page)
    },
  },
  ...(['document', 'view', 'select'] as const).map(
    (menu): ShotState => ({
      name: `desktop-menu-${menu}`,
      mode: 'desktop',
      desktop: `desktop-menu-${menu}`,
      steps: async (page) => {
        await openApp(page)
        await page.locator('[data-region="top-bar"]').getByRole('button', { name: menu[0]!.toUpperCase() + menu.slice(1), exact: true }).click()
        await page.getByRole('menu').waitFor()
      },
    }),
  ),
  {
    name: 'desktop-dialog',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await openApp(page)
      await featureRow(page, 'Extrude 1').dblclick()
      await page.getByRole('dialog').waitFor()
      await page.getByTestId('pad').waitFor({ state: 'attached' }).catch(() => {})
    },
  },
  {
    // I9.3: the Desktop feature toolbar with a face selected (tools that need another pick are dimmed), beside the desktop's toolbar
    name: 'desktop-toolbar-face',
    mode: 'desktop',
    desktop: 'desktop-studio',
    steps: async (page) => {
      await openApp(page)
      await pickFaceWithMouse(page)
    },
  },
  {
    // I9.3: Extrude started from the toolbar with a face selected: its dialog floats over the viewport with the face in its field
    name: 'desktop-toolbar-extrude',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await openApp(page)
      await pickFaceWithMouse(page)
      await page.locator('.desktop-toolbar').getByRole('button', { name: 'Extrude', exact: true }).click()
      await page.getByRole('dialog').waitFor()
      await page.locator('[data-testid^="sel-"]').first().getByTestId('sel-chip').first().waitFor()
    },
  },
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
    name: 'pad-open',
    mock: 'dialog', // the number pad docked at the foot of the dialog, a number being edited
    steps: async (page) => {
      await openApp(page)
      await page.evaluate(() => window.__freeshape?.openPadDemo({ value: '25 mm' }))
      await page.getByTestId('pad').waitFor()
    },
  },
  {
    name: 'pad-variables',
    mock: 'dialog', // the # key's list of variables in scope, above the keys
    steps: async (page) => {
      await openApp(page)
      await page.evaluate(() => window.__freeshape?.openPadDemo({ value: '25 mm', variables: ['depth', 'width', 'wall_thickness', 'hole_diameter', 'pitch'] }))
      await page.getByRole('button', { name: 'Variables' }).click()
      await page.getByTestId('pad-variables').waitFor()
    },
  },
  {
    name: 'pad-expression',
    mock: 'dialog', // an expression with a variable and a unit typed on the pad
    steps: async (page) => {
      await openApp(page)
      await page.evaluate(() => window.__freeshape?.openPadDemo({ value: '25 mm', variables: ['depth', 'width'] }))
      const key = (n: string) => page.getByTestId('pad').getByRole('button', { name: n, exact: true }).click()
      await key('Variables')
      await page.getByRole('option', { name: '#width' }).click()
      for (const n of ['Multiply', '2', 'Plus', 'Open bracket', '1', 'Decimal point', '5', 'Close bracket', 'mm']) await key(n)
    },
  },
  {
    name: 'feature-dialog',
    mock: 'dialog', // the bracket's Extrude 1 from a double tap: Depth on the pad, Cancel and Accept in the header
    steps: async (page) => {
      await openExtrudeDialog(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'feature-dialog-arrow',
    mock: 'dialog', // Extrude 1 open and the pad away: the depth arrow with its round Drag handle and its value label
    steps: async (page) => {
      await openExtrudeDialog(page)
      await padKeys(page, 'Enter')
      await page.locator('.viewport-manipulators [data-part="label"]').waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'feature-dialog-drag',
    mock: 'dialog', // the pen holds the Drag handle part-way along the arrow: the label and the preview have followed
    steps: async (page) => {
      await openExtrudeDialog(page)
      const at = async () => {
        const h = page.locator('.viewport-manipulators [data-part="handle"]')
        return { x: Number(await h.getAttribute('cx')), y: Number(await h.getAttribute('cy')) }
      }
      const from = await at()
      const [bx, by] = (await page.locator('.viewport-manipulators path').first().getAttribute('d'))!.match(/-?[\d.]+/g)!.map(Number)
      const len = Math.hypot(from.x - bx!, from.y - by!)
      const along = (px: number) => ({ x: from.x + ((from.x - bx!) / len) * px, y: from.y + ((from.y - by!) / len) * px })
      await sendPointer(page, [
        { phase: 'down', kind: 'pen', id: 10, ...from },
        ...[1, 2, 3, 4, 5, 6].map((i) => ({ phase: 'move' as const, kind: 'pen' as const, id: 10, ...along(8 * i) })),
      ])
      await page.waitForFunction(() => (window.__freeshapeViewport?.preview ?? 0) >= 2)
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'feature-dialog-preview',
    mock: 'dialog', // Depth 50 entered: the pad has closed, the view draws the preview with the new body tinted
    steps: async (page) => {
      await openExtrudeDialog(page)
      await padKeys(page, '5', '0', 'Enter')
      await page.waitForFunction(() => (window.__freeshapeViewport?.preview ?? 0) >= 1)
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'fillet-dialog-field',
    mock: 'dialog', // the bracket's Fillet 1 with its edge field active: chips with a remove button each, Multi on and no Clear in the view's chip, the field's edges highlighted
    steps: async (page) => {
      await openFilletField(page)
      const before = (await page.evaluate(() => window.__freeshapeViewport?.preview ?? 0)) as number
      await penPickEdge(page)
      await page.waitForFunction((n) => (window.__freeshapeViewport?.preview ?? 0) > n, before)
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'fillet-dialog-scrolled',
    mock: 'dialog', // Fillet 1 with its edge field active, the form scrolled to its end: Type and Radius reachable above the docked pad
    steps: async (page) => {
      await openFilletField(page)
      await penPickEdge(page)
      await page.evaluate(() => {
        const form = document.querySelector('.dlg-form') as HTMLElement
        form.scrollTop = form.scrollHeight
      })
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'fillet-dialog-refused',
    mock: 'dialog', // a pick the server refuses (here the refs route is made to answer with its own wording): the reason under the field, nothing added
    steps: async (page) => {
      await page.route('**/refs', (route) =>
        route.fulfill({ json: { document: 'x', tab: 'T1', revision: 1, items: [{ ok: false, code: 'wrong_kind', reason: 'A vertex cannot be used here; an edge is needed' }] } }),
      )
      await openFilletField(page)
      await penPickEdge(page)
      await page.locator('.dlg-message').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'feature-dialog-end-list',
    mock: 'dialog', // the End type list open (more options than fit as segments), the pad closed
    steps: async (page) => {
      await openExtrudeDialog(page)
      await padKeys(page, 'Cancel')
      await page.getByRole('button', { name: /^End type/ }).click()
      await page.getByRole('radiogroup', { name: 'End type' }).waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'feature-dialog-error',
    mock: 'dialog', // a depth of 0 refused: the message under the field, the last good value kept
    steps: async (page) => {
      await openExtrudeDialog(page)
      await padKeys(page, 'Backspace', '0', 'Enter')
      await page.locator('.dlg-message').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'feature-dialog-desktop-only',
    mock: 'dialog', // a Mate connector has a row only the desktop can edit: a message and no Accept
    steps: async (page) => {
      await openApp(page)
      const id = await idOf(page.request, 'Bracket')
      const r = await page.request.post(`/documents/${id}/tabs/T1/edit`, { data: { op: 'add_feature', feature: { id: 'F20', type: 'mate_connector', name: 'Mate connector 1', params: {} } } })
      if (!r.ok()) throw new Error(`could not add a mate connector: ${await r.text()}`)
      await featureRow(page, 'Mate connector 1').waitFor()
      await featureRow(page, 'Mate connector 1').dblclick()
      await page.getByTestId('desktop-only').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'feature-dialog-read-only',
    mock: 'dialog', // the desktop holds the Document: Accept is refused in plain words and the dialog keeps its values
    steps: async (page) => {
      await holdLockClosed(page.request, 'bracket', 'desktop')
      await openApp(page)
      await page.getByTestId('read-only-banner').waitFor()
      await featureRow(page, 'Extrude 1').dblclick()
      await page.getByTestId('pad').waitFor()
      await padKeys(page, '5', '0', 'Enter')
      await page.locator('.dialog-header').getByRole('button', { name: 'Accept', exact: true }).click()
      await page.getByTestId('dialog-problem').waitFor()
    },
    after: async () => releaseLock('bracket'),
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
    name: 'feature-sketch-menu',
    mock: 'browsing', // the layout draws no row menu: a sketch row's menu with Hide, and the eyes at the row ends
    steps: async (page) => {
      await openApp(page)
      const name = (await page.locator('.panel-row[data-type="sketch"] .row-name').first().innerText()).trim()
      await longPressFeature(page, new RegExp(`^${name}`))
    },
  },
  {
    name: 'feature-sketch-shown',
    mock: 'browsing', // the layout draws no shown sketch: the Bracket's sketches are hidden (Extrude uses them); one shown from its eye
    steps: async (page) => {
      await openApp(page)
      const eye = page.locator('[data-feature-eye]').first()
      if ((await eye.getAttribute('aria-label')) === 'Show sketch') await eye.click()
      await page.locator('li.feature-eyed:not([data-hidden])').first().waitFor()
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketchRegions ?? 0) > 0)
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
    name: 'assembly-tab',
    mock: 'browsing', // the Hinge's Assembly tab: no Part Studio, so what the panel and the view show for another kind of tab
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await page.locator('.tab-button[data-kind="assembly"]').click()
      await page.locator('.tab-button[data-kind="assembly"][aria-current="page"]').waitFor()
    },
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
    name: 'selection-none',
    mock: 'browsing', // the layout's chip reads "1 face"; this is its resting state, nothing selected
    steps: async (page) => {
      await openApp(page)
    },
  },
  {
    name: 'selection-face-aimed',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      const [a] = await faceSpots(page, page.request, 'pen')
      await penDown(page, a!) // the pen is down and has not lifted: the face is only Aimed
      await settled(page)
    },
  },
  {
    name: 'selection-face',
    mock: 'browsing', // the chip reads "1 face" in the layout too
    steps: async (page) => {
      await openApp(page)
      const [a] = await faceSpots(page, page.request, 'pen')
      await penPick(page, a!)
      await settled(page)
    },
  },
  {
    name: 'selection-edge-aimed',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await penDown(page, await edgeSpot(page, page.request, 'pen'))
      await settled(page)
    },
  },
  {
    name: 'selection-edge',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await penPick(page, await edgeSpot(page, page.request, 'pen'))
      await settled(page)
    },
  },
  {
    name: 'selection-vertex-aimed',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await penDown(page, await vertexSpot(page, page.request, 'pen'))
      await settled(page)
    },
  },
  {
    name: 'selection-vertex',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await penPick(page, await vertexSpot(page, page.request, 'pen'))
      await settled(page)
    },
  },
  {
    name: 'selection-plane',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await penPick(page, await planeSpot(page, 'pen'))
      await settled(page)
    },
  },
  {
    name: 'selection-multi',
    mock: 'browsing', // Multi on, as the layout's dialog state draws it, with a face, an edge and a vertex
    steps: async (page) => {
      await openApp(page)
      const [a, b] = await faceSpots(page, page.request, 'pen')
      const e = await edgeSpot(page, page.request, 'pen')
      await page.getByTestId('selection-multi').click()
      await penPick(page, a!)
      await penPick(page, b!)
      await penPick(page, e)
      await settled(page)
    },
  },
  {
    name: 'selection-read-only',
    mock: 'browsing', // the chip beside the read-only banner: selecting is allowed
    steps: async (page) => {
      await holdLockClosed(page.request, 'bracket', 'desktop')
      await openApp(page)
      await page.getByTestId('read-only-banner').waitFor()
      const [a] = await faceSpots(page, page.request, 'pen')
      await penPick(page, a!)
      await settled(page)
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
  {
    name: 'assembly-hinge',
    mock: 'browsing', // the layout has no Assembly state: its panel, rows, eye and view cube are the browsing state's
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await settledAssembly(page)
    },
  },
  {
    name: 'assembly-instance-selected',
    mock: 'browsing', // an instance selected in the list: its row tinted, Delete under it, the instance tinted in the view
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.locator('[data-instance="I2"]').click()
      await page.locator('.instance-delete').waitFor()
      await settledAssembly(page)
    },
  },
  {
    name: 'assembly-instance-hidden',
    mock: 'browsing', // one instance hidden: its row greyed with a struck eye, and it is gone from the view
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.locator('[data-instance-eye="I2"]').click()
      await page.waitForFunction(() => window.__freeshapeAssembly?.drawn === 2)
      await settledAssembly(page)
    },
  },
  {
    name: 'assembly-instance-fixed',
    mock: 'browsing', // one instance fixed: the lock toggle pressed on its row
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.locator('.instance-row:not([data-fixed]) .instance-fixed').first().click()
      await page.locator('.instance-row[data-fixed]').first().waitFor()
      await settledAssembly(page)
    },
  },
  {
    name: 'assembly-face-picked',
    mock: 'browsing', // a pen pick of a face of an instance: the face tinted, the chip says 1 face
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      const at = await page.evaluate(() => {
        const v = window.__freeshapeAssembly!
        const c = v.screenOf('I2')!
        for (const [dx, dy] of [[0, 0], [6, 0], [-6, 0], [0, 6], [0, -6], [14, 10], [-14, -10]] as const) {
          const hit = v.pickAt(c.x + dx, c.y + dy, 'mouse')
          if (hit?.instance === 'I2' && hit.kind === 'face') return { x: c.x + dx, y: c.y + dy }
        }
        return null
      })
      if (at === null) throw new Error('no face of I2 under its middle')
      const box = (await page.locator('[data-region="viewport"] canvas').first().boundingBox())!
      await page.mouse.click(box.x + at.x, box.y + at.y)
      await page.waitForFunction(() => document.querySelector('[data-testid="chip-slot"]')?.textContent?.includes('1 face'))
      await settledAssembly(page)
    },
  },
  {
    name: 'assembly-gizmo',
    mock: 'dialog', // the move gizmo on a selected free instance; its Drag handles follow the mock's look
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.locator('[data-instance="I2"]').click()
      await page.getByTestId('gizmo').waitFor()
      await settledAssembly(page)
    },
  },
  {
    name: 'assembly-gizmo-drag',
    mock: 'dialog', // a drag in progress on a rotation ring: the ring highlighted, the angle beside it, the finger still down
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.locator('[data-instance="I2"]').click()
      await page.getByTestId('gizmo').waitFor()
      await turnRing(page, 0.5, false)
    },
  },
  {
    name: 'assembly-gizmo-pad',
    mock: 'dialog', // the pad open on the angle label of the last drag, docked in the dialog area
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.locator('[data-instance="I2"]').click()
      await page.getByTestId('gizmo').waitFor()
      await turnRing(page, 0.5, true)
      await page.getByTestId('gizmo-label').click()
      await page.getByTestId('pad').waitFor()
      await settledAssembly(page)
    },
  },
  {
    name: 'assembly-gizmo-fixed',
    mock: 'browsing', // a fixed instance selected: tinted, and no gizmo over it
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.locator('.instance-row[data-fixed] [data-instance]').first().click()
      await page.locator('.instance-delete').waitFor()
      await settledAssembly(page)
    },
  },
  {
    name: 'assembly-panel-collapsed',
    mock: 'collapsed',
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.getByRole('button', { name: 'Collapse feature panel' }).click()
      await settledAssembly(page)
    },
  },
  {
    // Fillet and Chamfer pinned; the strip with groups and pins, no list open
    name: 'tools-pinned',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      await pinTools(page, [['Modify', 'Fillet'], ['Modify', 'Chamfer']])
    },
  },
  {
    // a press held on Modify with the pen over Chamfer: the row is lit, Fillet is pinned (the layout's flyout state)
    name: 'tools-group-aim',
    mock: 'flyout',
    steps: async (page) => {
      await openApp(page)
      await pinTools(page, [['Modify', 'Fillet']])
      const g = (await page.locator('.tool-strip').getByRole('button', { name: /^Modify/ }).boundingBox())!
      await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2)
      await page.mouse.down()
      const row = page.getByRole('menu', { name: 'Modify' }).getByRole('menuitem', { name: /^Chamfer/ })
      const c = (await row.boundingBox())!
      await page.mouse.move(c.x + c.width / 2, c.y + c.height / 2, { steps: 4 })
      await page.locator('.group-row[data-tool="chamfer"][data-aim]').waitFor()
    },
  },
  {
    // pins at the cap: the other pin buttons are dimmed and the list says the strip is full
    name: 'tools-pins-full',
    mock: 'flyout',
    steps: async (page) => {
      await openApp(page)
      const large = (page.viewportSize()?.width ?? 1024) >= 1366
      const all: Array<[string, string]> = [['Create', 'Extrude'], ['Create', 'Revolve'], ['Modify', 'Fillet'], ['Modify', 'Chamfer'], ['Modify', 'Shell'], ['Modify', 'Hole'], ['Create', 'Loft'], ['Modify', 'Draft'], ['Copy', 'Mirror'], ['Copy', 'Linear']]
      await pinTools(page, all.slice(0, large ? 10 : 6))
      await page.locator('.tool-strip').getByRole('button', { name: /^Modify/ }).click()
      await page.getByTestId('strip-full').waitFor()
    },
  },
  {
    // a tool whose dialog needs a field the client cannot draw is dimmed, with its note (Hole and Fill here)
    name: 'tools-desktop-only',
    mock: 'flyout',
    steps: async (page) => {
      await openApp(page)
      await page.locator('.tool-strip').getByRole('button', { name: /^Modify/ }).click()
      await page.locator('.group-row[data-state="desktop"] .group-note').first().waitFor()
    },
  },
  {
    // a face selected: tools it cannot start are dimmed (the Copy list is open)
    name: 'tools-selection',
    mock: 'flyout',
    steps: async (page) => {
      await openApp(page)
      const [spot] = await faceSpots(page, page.request, 'touch')
      await fingerTap(page, spot!)
      await page.getByTestId('selection-count').getByText('1 face').waitFor()
      await page.locator('.tool-strip').getByRole('button', { name: /^Copy/ }).click()
      await page.locator('.group-row[data-tool="mirror"]').waitFor()
    },
  },
  {
    name: 'sketch',
    mock: 'sketch', // the bracket's Bracket profile open: the view square to its plane, the solid see-through, the sketch drawn, the sketch strip
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-selected',
    mock: 'sketch', // a point selected: the chip says "1 point"
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await fingerTap(page, await screenOf(page, 'point', 'origin'))
      await page.getByTestId('selection-count').filter({ hasText: '1 point' }).waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-constrain-list',
    mock: 'constrain', // the Constrain group open, two columns, every tool on (F15: any tool can be chosen first), none marked
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await page.locator('.tool-strip').getByRole('button', { name: /^Constrain/ }).click()
      await page.getByRole('menu', { name: 'Constrain' }).waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-constrain-fits',
    mock: 'constrain', // a line selected: the Constrain group open, every tool on, a dot on the ones the server says fit
    steps: async (page) => {
      const data = sketchData(page)
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await tapSketchLine(page, data, data.line(0))
      await page.locator('.tool-strip').getByRole('button', { name: /^Constrain/ }).click()
      await page.getByRole('menu', { name: 'Constrain' }).waitFor()
      await page.locator('.group-row[data-fit]').first().waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-constrain-pick',
    mock: 'sketch', // a line selected, then Parallel (needs two lines): the tool stays lit and the hint names what to pick next
    steps: async (page) => {
      const data = sketchData(page)
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await tapSketchLine(page, data, data.line(0))
      await page.locator('.tool-strip').getByRole('button', { name: /^Constrain/ }).click()
      await page.locator('.group-row[data-tool="parallel"] .group-tool').click()
      await page.getByTestId('constraint-hint').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-constrain-conflict',
    mock: 'sketch', // Vertical on a line held horizontal: refused, the message beside the status, the sketch as it was
    steps: async (page) => {
      const data = sketchData(page)
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await tapSketchLine(page, data, data.lineWith('horizontal'))
      await page.locator('.tool-strip').getByRole('button', { name: /^Constrain/ }).click()
      await page.locator('.group-row[data-tool="vertical"][data-fit]').waitFor()
      await page.locator('.group-row[data-tool="vertical"] .group-tool').click()
      await page.getByTestId('constraint-note').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-twisted',
    mock: 'sketch', // 2D after a two-finger twist of 30 degrees: the sketch turned on screen, the camera still square to the plane
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      const c = await canvasCentre(page)
      const r = 100
      const fingers = (a: number) => ({ a: { x: c.x - r * Math.cos(a), y: c.y - r * Math.sin(a) }, b: { x: c.x + r * Math.cos(a), y: c.y + r * Math.sin(a) } })
      await twoFingers(page, fingers(0).a, fingers(0).b, fingers(Math.PI / 6).a, fingers(Math.PI / 6).b, 15)
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-3d',
    mock: 'sketch', // the 3D toggle on and the view orbited: the sketch seen against the see-through model
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await page.getByTestId('nav-3d').click()
      const c = await canvasCentre(page)
      await fingerDrag(page, c, { x: c.x + 150, y: c.y - 110 })
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-dimension-placing',
    mock: 'sketch', // Dimension on, two points of the Slot picked: the chip says "2 points" and the hint asks where the dimension goes
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, /Slot(?! cut)/)
      await page.locator('[data-strip="sketch"] [data-tool="dimension"]').click()
      await settledSketch(page)
      await fingerTap(page, await screenOf(page, 'point', 'p1'))
      await fingerTap(page, await screenOf(page, 'point', 'p3'))
      await page.getByTestId('selection-count').filter({ hasText: '2 points' }).waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-dimension-pad',
    mock: 'dialog', // a line of the Slot picked and a label placed: the dialog area holds the length on the pad, as a feature's number does
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, /Slot(?! cut)/)
      await page.locator('[data-strip="sketch"] [data-tool="dimension"]').click()
      await settledSketch(page)
      const a = await screenOf(page, 'point', 'p1')
      const b = await screenOf(page, 'point', 'p2')
      await fingerTap(page, { x: a.x + (b.x - a.x) / 4, y: a.y + (b.y - a.y) / 4 })
      await fingerTap(page, { x: (a.x + b.x) / 2, y: a.y + 70 })
      await page.getByTestId('pad').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-dimension-refused',
    mock: 'dialog', // a double tap on the 60 mm label, 0 on the pad and Enter: the server's refusal above the pad, the label unchanged
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await settledSketch(page)
      const at = await screenOf(page, 'dimension', 'k19')
      await sendPointer(page, [
        { phase: 'down', kind: 'touch', id: 1, ...at },
        { phase: 'up', kind: 'touch', id: 1, ...at },
        { phase: 'down', kind: 'touch', id: 1, ...at },
        { phase: 'up', kind: 'touch', id: 1, ...at },
      ])
      await page.getByTestId('pad').waitFor()
      await page.keyboard.type('0')
      await page.keyboard.press('Enter')
      await page.getByTestId('dimension-refusal').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-handle',
    mock: 'sketch', // a point that can move selected: its Drag handle up and right of it, the dashed line between (the mock draws the same on a corner)
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await fingerTap(page, await screenOf(page, 'point', 'p3'))
      await page.getByTestId('selection-count').filter({ hasText: '1 point' }).waitFor()
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-dragging',
    mock: 'sketch', // the 60 mm dimension is deleted first (the profile is fully defined, so no point could move): the 3 free items say so, and nothing is selected. The pen is down on the bottom edge's right end and has moved 60 px left: the end is at the pen, the vertical constraint carries its partner, the 6 mm dimension follows, and the pale box beyond is the extruded solid, which does not move until Done
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      // the profile is fully defined; the 60 mm dimension goes so that the end of the edge can move
      await fingerTap(page, await screenOf(page, 'dimension', 'k19'))
      await page.locator('[data-tool="delete"]').click()
      await page.getByTestId('selection-count').filter({ hasText: 'Nothing selected' }).waitFor()
      const p = await screenOf(page, 'point', 'p2')
      await sendPointer(page, [
        { phase: 'down', kind: 'pen', id: 10, ...p },
        { phase: 'move', kind: 'pen', id: 10, x: p.x - 30, y: p.y },
        { phase: 'move', kind: 'pen', id: 10, x: p.x - 60, y: p.y + 12 },
      ])
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-construction',
    mock: 'sketch', // the bottom edge turned to construction with Construct: dashed grey, still selected, its handle beside it
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      const a = await screenOf(page, 'point', 'origin')
      const b = await screenOf(page, 'point', 'p2')
      await fingerTap(page, { x: a.x + (b.x - a.x) / 4, y: a.y + (b.y - a.y) / 4 })
      await page.getByTestId('selection-count').filter({ hasText: '1 item' }).waitFor()
      await page.locator('[data-tool="construct"]').click()
      await page.locator('.sketch-layer path[data-id="l7"][data-dashed]').waitFor({ state: 'attached' })
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-new',
    mock: 'sketch', // a new sketch on the Top plane: nothing drawn yet but the axes, "Empty"
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-quickmenu',
    mock: 'radial', // a pen held 0.4 s at the mock's place (60% across, 56% down) and slid towards Line: the ring round the press, Line lit
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      const box = (await page.getByTestId('viewport').boundingBox())!
      const at = { x: Math.round(box.width * 0.6), y: Math.round(box.height * 0.56) }
      await sendPointer(page, [{ phase: 'down', kind: 'pen', id: 10, ...at }])
      await page.getByTestId('quick-line').waitFor()
      await sendPointer(page, [{ phase: 'move', kind: 'pen', id: 10, x: at.x, y: at.y - 70 }])
      await page.locator('.quick-menu button[data-aimed]').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-quickmenu-corner',
    mock: 'radial', // a hold 20 px from the top left corner of the view: the ring moves inward so every button is on screen
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await sendPointer(page, [{ phase: 'down', kind: 'pen', id: 10, x: 20, y: 20 }])
      await page.getByTestId('quick-line').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-draw-aim',
    mock: 'sketch', // the Line tool chosen from the Draw group, a first point placed, and the pen down over the bracket's origin: the mark, the snap label and the rubber band
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await page.locator('.tool-strip').getByRole('button', { name: /^Draw/ }).click()
      await page.locator('.group-row[data-tool="line"]').click()
      await page.getByTestId('aim-hint').waitFor()
      const o = await screenOf(page, 'point', 'origin')
      const start = { x: o.x + 150, y: o.y - 110 }
      await sendPointer(page, [
        { phase: 'down', kind: 'pen', id: 10, x: start.x - 4, y: start.y },
        { phase: 'move', kind: 'pen', id: 10, ...start },
        { phase: 'up', kind: 'pen', id: 10, ...start, pressure: 0 },
        { phase: 'down', kind: 'pen', id: 10, x: o.x + 40, y: o.y - 40 },
        { phase: 'move', kind: 'pen', id: 10, x: o.x + 4, y: o.y - 3 },
      ])
      await page.locator('[data-kind="aim"][data-snap="point"]').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-trim-aim',
    mock: 'sketch', // Trim chosen from the Draw group and the pen down on a line of the bracket profile: the piece a Commit would remove is marked red and dashed, with the tool's hint at the top left
    steps: async (page) => {
      const opened = editOpened(page)
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await page.locator('.tool-strip').getByRole('button', { name: /^Draw/ }).click()
      await page.locator('.group-row[data-tool="trim"]').click()
      await page.getByTestId('edit-hint').waitFor()
      await settledSketch(page)
      const sk = opened().sketch
      const id = Object.keys(sk.entities).find((k) => sk.entities[k]!.type === 'line')!
      const a = await screenOf(page, 'point', sk.entities[id]!.p1!)
      const b = await screenOf(page, 'point', sk.entities[id]!.p2!)
      const at = { x: a.x + (b.x - a.x) * 0.3, y: a.y + (b.y - a.y) * 0.3 }
      await sendPointer(page, [
        { phase: 'down', kind: 'pen', id: 10, x: at.x - 4, y: at.y },
        { phase: 'move', kind: 'pen', id: 10, ...at },
      ])
      await page.locator('.sketch-layer g[data-preview="trim"]').waitFor({ state: 'attached' })
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-fillet-pad',
    mock: 'dialog', // Corner fillet on a corner of the bracket profile: the dialog area holds the radius on the pad, the corner is selected
    steps: async (page) => {
      const opened = editOpened(page)
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await page.locator('.tool-strip').getByRole('button', { name: /^Draw/ }).click()
      await page.locator('.group-row[data-tool="corner_fillet"]').click()
      await settledSketch(page)
      const sk = opened().sketch
      const users = (pid: string) => Object.values(sk.entities).filter((e) => e.p1 === pid || e.p2 === pid)
      const corner = Object.keys(sk.points).find((p) => p !== 'origin' && users(p).length === 2 && users(p).every((e) => e.type === 'line'))!
      await fingerTap(page, await screenOf(page, 'point', corner))
      await page.getByTestId('pad').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-use-edge-aim',
    mock: 'sketch', // Use edge on a new sketch on the Top plane, the pen down on a model edge: the edge is marked blue through the see-through solid
    steps: async (page) => {
      const opened = editOpened(page)
      await openApp(page)
      await openNewSketch(page, 'Top')
      await page.locator('.tool-strip').getByRole('button', { name: /^Draw/ }).click()
      await page.locator('.group-row[data-tool="use_edge"]').click()
      await page.getByTestId('edit-hint').waitFor()
      await settledSketch(page)
      const e = opened().edges.find((x) => x.points.length === 2) ?? opened().edges[0]!
      const mid = { u: (e.points[0]![0] + e.points[1]![0]) / 2, v: (e.points[0]![1] + e.points[1]![1]) / 2 }
      const o = await screenOf(page, 'point', 'origin')
      const a = (await uvAt(page, o.x + 100, o.y))!
      const b = (await uvAt(page, o.x, o.y + 100))!
      const m = [
        [a[0] / 100, b[0] / 100],
        [a[1] / 100, b[1] / 100],
      ]
      const det = m[0]![0]! * m[1]![1]! - m[0]![1]! * m[1]![0]!
      const at = { x: o.x + (m[1]![1]! * mid.u - m[0]![1]! * mid.v) / det, y: o.y + (-m[1]![0]! * mid.u + m[0]![0]! * mid.v) / det }
      await sendPointer(page, [
        { phase: 'down', kind: 'pen', id: 10, x: at.x - 3, y: at.y },
        { phase: 'move', kind: 'pen', id: 10, ...at },
      ])
      await page.locator('.sketch-layer g[data-preview="edge"]').waitFor({ state: 'attached' })
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-drawn',
    mock: 'sketch', // a new sketch with a rectangle, a circle and a line drawn with the pen, the Circle tool still on
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      const o = await screenOf(page, 'point', 'origin')
      const penAt = (p: { x: number; y: number }) =>
        sendPointer(page, [
          { phase: 'down', kind: 'pen', id: 10, x: p.x - 3, y: p.y },
          { phase: 'move', kind: 'pen', id: 10, ...p },
          { phase: 'up', kind: 'pen', id: 10, ...p, pressure: 0 },
        ])
      const tool = async (group: string, id: string) => {
        await page.locator('.tool-strip').getByRole('button', { name: new RegExp(`^${group}`) }).click()
        await page.locator(`.group-row[data-tool="${id}"]`).click()
        await page.getByTestId('aim-hint').filter({ hasText: id === 'rectangle' ? 'Rectangle' : id === 'line' ? 'Line' : 'Circle' }).waitFor({ timeout: 5000 })
      }
      await tool('Draw', 'rectangle')
      await penAt({ x: o.x - 200, y: o.y - 120 })
      await penAt({ x: o.x - 40, y: o.y + 60 })
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.stats.lines ?? 0) >= 4)
      await tool('Draw', 'line')
      await penAt({ x: o.x + 40, y: o.y + 120 })
      await penAt({ x: o.x + 200, y: o.y + 40 })
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.stats.lines ?? 0) >= 5)
      await tool('Draw', 'circle')
      await penAt({ x: o.x + 110, y: o.y - 90 })
      await penAt({ x: o.x + 170, y: o.y - 90 })
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.stats.circles ?? 0) >= 1)
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    // a face picked with the pen, Extrude tapped on the strip: the create dialog holds the face, Depth is on the pad
    name: 'create-extrude',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page)
      const [spot] = await faceSpots(page, page.request, 'pen')
      await penPick(page, spot!)
      await startFromStrip(page, 'Create', /^Extrude/)
      await page.getByTestId('pad').waitFor()
      await page.locator('[data-dialog-base]').waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // the same with the pad away: the new feature's preview carries the depth arrow with its Drag handle and label
    name: 'create-extrude-arrow',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page)
      const [spot] = await faceSpots(page, page.request, 'pen')
      await penPick(page, spot!)
      await startFromStrip(page, 'Create', /^Extrude/)
      await padKeys(page, 'Enter')
      await page.locator('.viewport-manipulators [data-part="label"]').waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // an edge picked, Fillet tapped: the dialog holds the edge chip, the Type and Radius rows sit under the pad (at 1024)
    name: 'create-fillet',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page)
      const spot = await edgeSpot(page, page.request, 'pen')
      await penPick(page, spot)
      await startFromStrip(page, 'Modify', /^Fillet/)
      await page.getByTestId('pad').waitFor()
      await page.locator('[data-dialog-base]').waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // Extrude tapped with nothing selected: the profile field asks for a pick
    name: 'create-extrude-empty',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page)
      await startFromStrip(page, 'Create', /^Extrude/)
      await page.getByTestId('pad').waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-plane-ask',
    mock: 'browsing', // the Sketch tool with nothing selected: the small dialog asks for a plane, the three default planes as buttons
    steps: async (page) => {
      await openApp(page)
      await page.locator('[data-tool="sketch"]').click()
      await page.getByRole('dialog', { name: 'Sketch on which plane?' }).waitFor()
    },
  },
  {
    name: 'sketch-discard',
    mock: 'sketch', // Cancel on a sketch with a change: asks before the change is thrown away
    steps: async (page) => {
      await openApp(page)
      await featureMenu(page, 'Bracket profile', 'Edit')
      await page.locator('[data-strip="sketch"]').waitFor()
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.paints ?? 0) > 0)
      await chooseWithRing(page, 'point')
      await place(page, await screenFor(page, 20, 30))
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.stats.points ?? 0) > 0)
      await page.getByTestId('sketch-cancel').click()
      await page.getByRole('dialog', { name: 'Discard this sketch?' }).waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'region-selected',
    mock: 'browsing', // back in the Part Studio after Done: the new sketch's region is drawn, the pen has picked it ("1 region")
    steps: async (page) => {
      await commitRectangleAndPickRegion(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'region-extrude',
    mock: 'dialog', // Extrude started from the picked region: its profile field reads "Region 1 of Sketch ..."
    steps: async (page) => {
      await commitRectangleAndPickRegion(page)
      await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
      await page.getByRole('menuitem', { name: /^Extrude/ }).click()
      await page.getByTestId('pad').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-3d-line',
    mock: 'sketch', // the 3D toggle on and the view tipped: a line drawn with the pen lies in the sketch plane, the grid and axes tipped with it
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      await page.getByTestId('nav-3d').click()
      const c = await canvasCentre(page)
      await fingerDrag(page, c, { x: c.x + 80, y: c.y - 40 })
      await chooseWithRing(page, 'line')
      await place(page, { x: c.x - 120, y: c.y + 60 })
      await place(page, { x: c.x + 20, y: c.y + 110 })
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.stats.lines ?? 0) >= 1)
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    name: 'part-from-nothing',
    mock: 'browsing', // a Document made from nothing: its sketch and the extrusion of its region in the panel, the solid in the view
    steps: async (page) => {
      await makeFixture(page.request, { solid: true })
      await openApp(page, { document: FIXTURE })
    },
    after: async (page) => dropFixture(page.request),
  },
]

/** Listen for the open sketch's data (call before it opens); `lineWith(type)` is the first line a constraint of that type holds. */
function sketchData(page: Page) {
  let opened: { entities: Record<string, { p1?: string; p2?: string }>; constraints: { type: string; refs: string[] }[] } | null = null
  page.on('websocket', (ws) =>
    ws.on('framereceived', (f) => {
      if (typeof f.payload !== 'string' || !f.payload.includes('"sketch.opened"')) return
      opened = (JSON.parse(f.payload) as { sketch: NonNullable<typeof opened> }).sketch
    }),
  )
  return { lineWith: (type: string): string | undefined => opened?.constraints.find((c) => c.type === type)?.refs[0], line: (i: number): string | undefined => Object.keys(opened?.entities ?? {})[i], ends: (id: string) => [opened!.entities[id]!.p1!, opened!.entities[id]!.p2!] as const }
}

/** Tap the sketch's line `id` a quarter along it (the middle is where a constraint's glyph sits) and wait for it to be selected. */
async function tapSketchLine(page: Page, data: ReturnType<typeof sketchData>, id: string | undefined): Promise<void> {
  if (id === undefined) throw new Error('no such line in the sketch')
  const [p1, p2] = data.ends(id)
  const a = await screenOf(page, 'point', p1)
  const b = await screenOf(page, 'point', p2)
  await fingerTap(page, { x: a.x + (b.x - a.x) * 0.25, y: a.y + (b.y - a.y) * 0.25 })
  await page.getByTestId('selection-count').filter({ hasText: '1 item' }).waitFor()
}

/** Pin tools through their rows' pin buttons: [group name, tool label] each; the strip is left with no list open. */
async function pinTools(page: Page, tools: Array<[string, string]>): Promise<void> {
  for (const [group, tool] of tools) {
    await page.locator('.tool-strip').getByRole('button', { name: new RegExp(`^${group}`) }).click()
    await page.getByRole('menu', { name: group }).getByRole('button', { name: `Pin ${tool}`, exact: true }).click()
    await page.getByTestId('strip-scrim').click({ position: { x: 5, y: 700 } })
  }
}

/** Open a group on the tool strip and tap a tool of its list. */
async function startFromStrip(page: Page, group: string, tool: RegExp): Promise<void> {
  await page.locator('.tool-strip').getByRole('button', { name: new RegExp(`^${group}`) }).click()
  await page.getByRole('menu', { name: group }).getByRole('menuitem', { name: tool }).click()
  await page.locator('[data-region="dialog"]').waitFor()
}

const penDown = (page: Page, at: Spot) => sendPointer(page, [{ phase: 'down', kind: 'pen', id: 10, ...at }])
const penPick = async (page: Page, at: Spot) => {
  await penDown(page, at)
  await sendPointer(page, [{ phase: 'up', kind: 'pen', id: 10, ...at, pressure: 0 }])
}

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

/** A rectangle on the Top plane clear of the Bracket, Done, and the pen on its region: the Part Studio with the region selected. */
async function commitRectangleAndPickRegion(page: Page): Promise<void> {
  await openApp(page)
  await openNewSketch(page, 'Top')
  await chooseWithRing(page, 'rectangle')
  await place(page, await screenFor(page, -30, -10))
  await place(page, await screenFor(page, -10, 10))
  await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.stats.lines ?? 0) >= 4)
  const rev = await page.evaluate(() => window.__freeshapeViewport?.revision ?? 0)
  await page.getByTestId('sketch-done').click()
  await page.locator('[data-strip="feature"]').waitFor()
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r, rev)
  const at = await regionSpot(page)
  await penStroke(page, at, at)
  await page.getByTestId('selection-count').getByText('1 region').waitFor()
  await settled(page)
}
