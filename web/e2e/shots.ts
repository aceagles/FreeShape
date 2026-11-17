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
      await sendPointer(page, [{ phase: 'move', kind: 'pen', id: 10, x: at.x + 50, y: at.y - 50 }]) // up and right: Line (Select is the top item since F19)
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
  {
    // I9.15: the Assembly panel at the desktop's density, beside the desktop program (I9.13)
    name: 'desktop-assembly',
    mode: 'desktop',
    desktop: 'desktop-assembly',
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await settledAssembly(page)
    },
  },
  {
    // I9.15: a feature row and a part row under the mouse in the Part Studio (hover tint, the eye of the row)
    name: 'desktop-studio-hover',
    mode: 'desktop',
    desktop: 'desktop-studio',
    steps: async (page) => {
      await openApp(page)
      await featureRow(page, 'Extrude 1').hover()
    },
  },
  {
    // I9.4: the Desktop sketch toolbar on a new sketch on Top, Select on, beside the desktop's sketch state
    name: 'desktop-sketch-toolbar',
    mode: 'desktop',
    desktop: 'desktop-sketch',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
    },
    after: closeAllDiscard,
  },
  {
    // I9.4: Line on (pressed) with a chain half drawn by mouse clicks
    name: 'desktop-sketch-line',
    mode: 'desktop',
    desktop: 'desktop-sketch',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      await page.locator('[data-strip="sketch"]').getByRole('button', { name: 'Line', exact: true }).click()
      const a = await screenFor(page, -30, -10)
      const b = await screenFor(page, 20, 15)
      for (const p of [a, b]) {
        await sendPointer(page, [
          { phase: 'move', kind: 'mouse', id: 20, ...p },
          { phase: 'down', kind: 'mouse', id: 20, ...p, button: 0 },
          { phase: 'up', kind: 'mouse', id: 20, ...p, button: 0 },
        ])
      }
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.stats.lines ?? 0) >= 1)
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    // I9.16: the Extrude's depth typed in its row (Desktop mode), beside the desktop's dialog
    name: 'desktop-dialog-typing',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await openApp(page)
      await featureRow(page, 'Extrude 1').dblclick()
      const depth = page.getByRole('dialog').getByRole('textbox', { name: 'Depth' })
      await depth.click()
      await page.keyboard.press('Control+A')
      await page.keyboard.type('25/2')
    },
  },
  {
    // I9.16: a text the field cannot take, in the desktop's invalid style
    name: 'desktop-dialog-invalid',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await openApp(page)
      await featureRow(page, 'Extrude 1').dblclick()
      const depth = page.getByRole('dialog').getByRole('textbox', { name: 'Depth' })
      await depth.click()
      await page.keyboard.press('Control+A')
      await page.keyboard.type('abc')
    },
  },
  {
    // I9.16: a choice row's dropdown open (I9.2's Menu), as the desktop's QComboBox
    name: 'desktop-dialog-dropdown',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await openApp(page)
      await featureRow(page, 'Extrude 1').dblclick()
      await page.getByRole('dialog').locator('.dlg-combo-button').first().click()
      await page.getByRole('menu').waitFor()
    },
  },
  {
    // I9.16: a sketch in Desktop mode, beside the desktop's sketch
    name: 'desktop-sketch',
    mode: 'desktop',
    desktop: 'desktop-sketch',
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    // I9.16: a dimension's value typed in the inline box at its label (a mouse double click)
    name: 'desktop-sketch-dimension',
    mode: 'desktop',
    desktop: 'desktop-sketch',
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await settledSketch(page)
      const at = await screenOf(page, 'dimension', 'k19')
      const canvas = (await page.locator('[data-region="viewport"] canvas').first().boundingBox())!
      await page.mouse.dblclick(canvas.x + at.x, canvas.y + at.y)
      await page.getByTestId('dimension-inline').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    // I9.9: a selection box being dragged left to right over the left of the bracket (the button still held)
    name: 'desktop-box-select',
    mode: 'desktop',
    desktop: 'desktop-studio',
    steps: async (page) => {
      await openApp(page)
      await mouseBox(page, false)
    },
  },
  {
    // I9.9: several items selected by a box, the count beside the cursor
    name: 'desktop-multi-select',
    mode: 'desktop',
    desktop: 'desktop-studio',
    steps: async (page) => {
      await openApp(page)
      await mouseBox(page, true)
      await page.getByTestId('select-count-badge').waitFor()
      await settled(page)
    },
  },
  {
    // F18: a sketch open with the left column collapsed to its rail (Desktop mode); the view takes the width
    name: 'desktop-sketch-collapsed',
    mode: 'desktop',
    desktop: 'desktop-sketch',
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await page.getByRole('button', { name: 'Collapse feature panel' }).click()
      await page.getByTestId('panel-rail').waitFor()
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    // F18: the same in Tablet mode, beside the mock's collapsed panel
    name: 'sketch-collapsed',
    mock: 'collapsed',
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await page.getByRole('button', { name: 'Collapse feature panel' }).click()
      await page.getByTestId('panel-rail').waitFor()
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    // F20: Construct pressed with nothing selected (the mode), one construction line drawn dashed grey, and a second one being drawn with its band dashed grey
    name: 'sketch-construct-mode',
    mock: 'sketch',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      await page.locator('[data-strip="sketch"] [data-group="draw"]').click()
      await page.locator('.group-row[data-tool="line"] .group-tool').click()
      await page.locator('[data-strip="sketch"] [data-tool="construct"]').click()
      const a = await screenFor(page, -30, -10)
      const b = await screenFor(page, 20, 15)
      await place(page, a)
      await place(page, b)
      await place(page, b)
      await page.locator('.sketch-layer path[data-dashed]').first().waitFor({ state: 'attached' })
      const c = await screenFor(page, -30, 20)
      const d = await screenFor(page, 15, 30)
      await place(page, c)
      await sendPointer(page, [
        { phase: 'down', kind: 'pen', id: 10, x: d.x - 3, y: d.y },
        { phase: 'move', kind: 'pen', id: 10, ...d },
      ])
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    // I9.8: P has hidden every plane, then the Origin row's eye brought Front back alone; the View menu is open with Show planes ticked
    name: 'desktop-planes-one',
    mode: 'desktop',
    desktop: 'desktop-studio',
    steps: async (page) => {
      await openApp(page)
      await page.keyboard.press('p')
      await page.waitForFunction(() => window.__freeshapeViewport?.planes === 0)
      await page.locator('[data-origin-eye="Front"]').click()
      await page.waitForFunction(() => window.__freeshapeViewport?.planes === 1)
      await page.getByRole('button', { name: 'View', exact: true }).click()
      await settled(page)
    },
  },
  {
    // I9.8: Y with the pointer over the bracket and nothing selected hid it: the Parts list shows it hidden, the view is empty
    name: 'desktop-part-hidden',
    mode: 'desktop',
    desktop: 'desktop-studio',
    steps: async (page) => {
      await openApp(page)
      const [spot] = await faceSpots(page, page.request, 'mouse')
      const canvas = (await page.locator('.viewport-canvas').boundingBox())!
      await page.mouse.move(canvas.x + spot!.x, canvas.y + spot!.y)
      await page.waitForFunction(() => window.__freeshapeViewport?.selection.aimed != null)
      await page.keyboard.press('y')
      await page.waitForFunction(() => (window.__freeshapeViewport?.hiddenParts ?? 0) === 1)
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I9.5: the Assembly toolbar in Tablet mode, an instance selected (Fix and Move are on, Insert and the mates wait for S10)
    name: 'assembly-toolbar',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.locator('[data-instance="I2"]').click()
      await page.getByTestId('gizmo').waitFor()
      await settledAssembly(page)
    },
  },
  {
    // I9.5: the Tablet Assembly strip's Mate group open: the eight types, each with the S10 note
    name: 'assembly-toolbar-mate-group',
    mock: 'flyout',
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.locator('[data-group="mate"]').click({ position: { x: 20, y: 20 } })
      await page.getByRole('menu', { name: 'Mate' }).waitFor()
      await settledAssembly(page)
    },
  },
  {
    // I9.5: the desktop's assembly toolbar in Desktop mode, an instance selected, beside the desktop program's Assembly
    name: 'desktop-assembly-toolbar',
    mode: 'desktop',
    desktop: 'desktop-assembly',
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.locator('[data-instance="I2"]').click()
      await page.getByTestId('gizmo').waitFor()
      await settledAssembly(page)
    },
  },
  {
    name: 'sketch-hold-finger',
    mock: 'radial', // F19: a finger held at the mock's place and slid, still down, to the top item (Select): the smaller ring, Select lit, nothing selected
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      const box = (await page.getByTestId('viewport').boundingBox())!
      const at = { x: Math.round(box.width * 0.6), y: Math.round(box.height * 0.56) }
      await sendPointer(page, [{ phase: 'down', kind: 'touch', id: 10, ...at }])
      await page.getByTestId('quick-line').waitFor()
      await sendPointer(page, [{ phase: 'move', kind: 'touch', id: 10, x: at.x, y: at.y - 70 }])
      await page.locator('.quick-menu button[data-aimed]').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    name: 'sketch-hold-finger-corner',
    mock: 'radial', // F19: a finger held 20 px from the top left corner: the smaller ring moved inward, every item on screen
    steps: async (page) => {
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await sendPointer(page, [{ phase: 'down', kind: 'touch', id: 10, x: 20, y: 20 }])
      await page.getByTestId('quick-line').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    // F22: Dimension in Desktop mode, a side picked by a press and the dimension following the mouse before it is placed
    name: 'desktop-sketch-dimension-preview',
    mode: 'desktop',
    desktop: 'desktop-sketch',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      const mouse = (phase: 'move' | 'down' | 'up', p: { x: number; y: number }): PointerStep => ({ phase, kind: 'mouse', id: 20, ...p, ...(phase === 'move' ? {} : { button: 0 }) })
      await page.keyboard.press('g')
      for (const p of [await screenFor(page, -30, -10), await screenFor(page, 20, 15)]) await sendPointer(page, [mouse('move', p), mouse('down', p), mouse('up', p)])
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.stats.lines ?? 0) >= 4)
      await page.keyboard.press('d')
      const side = await screenFor(page, -17.5, -10)
      const away = await screenFor(page, -17.5, -24)
      await sendPointer(page, [mouse('move', side), mouse('down', side), mouse('move', away)])
      await page.locator('.sketch-layer g[data-preview="1"]').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    // I11.9: the Export dialog in Tablet mode, the bracket's part selected, STL with its resolution and encoding rows
    name: 'export-dialog',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page)
      await partRow(page, /./).first().click()
      await page.getByRole('button', { name: 'Export', exact: true }).click()
      await page.getByRole('menuitem', { name: 'Export STEP / STL…' }).click()
      await page.getByRole('radio', { name: 'STL' }).click()
      await settled(page)
    },
  },
  {
    // I11.9: the same with Custom on and the pad open on the deviation
    name: 'export-dialog-custom',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page)
      await page.getByRole('button', { name: 'Export', exact: true }).click()
      await page.getByRole('menuitem', { name: 'Export STEP / STL…' }).click()
      await page.getByRole('radio', { name: 'STL' }).click()
      await page.getByRole('radio', { name: 'Custom' }).click()
      await page.getByRole('button', { name: /^Deviation, / }).click()
      await page.getByTestId('pad').waitFor()
      await settled(page)
    },
  },
  {
    // I11.9: the Export dialog floating over the Desktop-mode viewport, STL, beside the desktop's dialog
    name: 'desktop-export-dialog',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await openApp(page)
      await partRow(page, /./).first().click()
      await page.locator('[data-region="top-bar"]').getByRole('button', { name: 'Document', exact: true }).click()
      await page.getByRole('menuitem', { name: 'Export STL…' }).click()
      await page.getByRole('dialog', { name: 'Export' }).waitFor()
      await settled(page)
    },
  },
  {
    // I11.9: Custom resolution in Desktop mode: the deviation and angle typed in their rows
    name: 'desktop-export-custom',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await openApp(page)
      await page.locator('[data-region="top-bar"]').getByRole('button', { name: 'Document', exact: true }).click()
      await page.getByRole('menuitem', { name: 'Export STL…' }).click()
      const dialog = page.getByRole('dialog', { name: 'Export' })
      await dialog.getByRole('button', { name: /^Resolution/ }).click()
      await page.getByRole('menuitemcheckbox', { name: 'Custom' }).click()
      await dialog.getByRole('textbox', { name: 'Deviation' }).fill('50')
      await dialog.getByRole('textbox', { name: 'Angle' }).click()
      await settled(page)
    },
  },
  {
    // I11.9: an Assembly's Export from its toolbar in Desktop mode: no Parts row
    name: 'desktop-export-assembly',
    mode: 'desktop',
    desktop: 'desktop-assembly',
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.locator('[data-strip="assembly"] [data-tool="export"]').click()
      await page.getByRole('dialog', { name: 'Export' }).waitFor()
      await settledAssembly(page)
    },
  },
  {
    // I11.14: the Export dialog set to save into a server folder (Tablet mode): the Destination row and the File name row
    name: 'export-save',
    mock: 'dialog',
    steps: async (page) => {
      await mockExportRoot(page, false)
      await openApp(page)
      await partRow(page, /./).first().click()
      await page.getByRole('button', { name: 'Export', exact: true }).click()
      await page.getByRole('menuitem', { name: 'Export STEP / STL…' }).click()
      await page.getByRole('radio', { name: 'STL' }).click()
      await page.getByRole('radio', { name: 'Save to shared' }).click()
      await settled(page)
    },
  },
  {
    // I11.14: the folder already holds that name: the Replace question over the dialog (Tablet mode)
    name: 'export-replace',
    mock: 'dialog',
    steps: async (page) => {
      await mockExportRoot(page, true)
      await openApp(page)
      await partRow(page, /./).first().click()
      await page.getByRole('button', { name: 'Export', exact: true }).click()
      await page.getByRole('menuitem', { name: 'Export STEP / STL…' }).click()
      await page.getByRole('radio', { name: 'STL' }).click()
      await page.getByRole('radio', { name: 'Save to shared' }).click()
      await page.getByRole('button', { name: 'Save to shared', exact: true }).click()
      await page.getByRole('dialog', { name: 'Replace file?' }).waitFor()
      await settled(page)
    },
  },
  {
    // I11.14: the same in Desktop mode, the name typed inline with its extension after it
    name: 'desktop-export-save',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await mockExportRoot(page, false)
      await openApp(page)
      await partRow(page, /./).first().click()
      await openDesktopSave(page)
      await settled(page)
    },
  },
  {
    // I11.14: the Replace question in Desktop mode
    name: 'desktop-export-replace',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await mockExportRoot(page, true)
      await openApp(page)
      await partRow(page, /./).first().click()
      await openDesktopSave(page)
      await page.getByRole('dialog', { name: 'Export' }).getByRole('button', { name: 'Save to shared', exact: true }).click()
      await page.getByRole('dialog', { name: 'Replace file?' }).waitFor()
      await settled(page)
    },
  },
  {
    // I14.2: the Draw group open on a new sketch: Trim, Corner fillet and Use edge, then Mirror and the two patterns, which stay dimmed until I14.3 (the strip has no Copy group of its own: it would not fit 1024)
    name: 'sketch-copy-group',
    mock: 'flyout',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      await page.locator('.tool-strip').getByRole('button', { name: /^Draw/ }).click()
      await page.locator('.group-row[data-tool="sk_mirror"]').waitFor()
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    // I14.2: a line of the bracket profile selected, Mirror chosen and the pen down on the opposite line: the hint at the top left and the ghost of the copy
    name: 'sketch-mirror-aim',
    mock: 'sketch',
    steps: async (page) => {
      const opened = editOpened(page)
      await openApp(page)
      await openSketchFeature(page, 'Bracket profile')
      await settledSketch(page)
      const sk = opened().sketch
      const lines = Object.keys(sk.entities).filter((k) => sk.entities[k]!.type === 'line')
      const mid = async (id: string) => {
        const a = await screenOf(page, 'point', sk.entities[id]!.p1!)
        const b = await screenOf(page, 'point', sk.entities[id]!.p2!)
        return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
      }
      await place(page, await mid(lines[0]!))
      await page.locator('.tool-strip').getByRole('button', { name: /^Draw/ }).click()
      await page.locator('.group-row[data-tool="sk_mirror"]').click()
      await page.getByTestId('copy-hint').waitFor()
      const at = await mid(lines[2]!)
      // the press starts well off the line, so that sliding onto it is a move and not a hold: the quick menu stays shut
      await sendPointer(page, [
        { phase: 'down', kind: 'pen', id: 10, x: at.x - 40, y: at.y },
        { phase: 'move', kind: 'pen', id: 10, ...at },
      ])
      await page.locator('.sketch-layer g[data-preview="copies"]').waitFor({ state: 'attached' })
    },
    after: closeAllDiscard,
  },
  {
    // I14.2: the Desktop sketch row with Mirror pressed, a line selected, and the hint over the view
    name: 'desktop-sketch-mirror',
    mode: 'desktop',
    desktop: 'desktop-sketch',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      const bar = page.locator('[data-strip="sketch"]')
      await bar.getByRole('button', { name: 'Line', exact: true }).click()
      const a = await screenFor(page, -30, -10)
      const b = await screenFor(page, 20, 15)
      for (const p of [a, b, b]) {
        await sendPointer(page, [
          { phase: 'move', kind: 'mouse', id: 20, ...p },
          { phase: 'down', kind: 'mouse', id: 20, ...p, button: 0 },
          { phase: 'up', kind: 'mouse', id: 20, ...p, button: 0 },
        ])
      }
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.stats.lines ?? 0) >= 1)
      await bar.getByRole('button', { name: 'Select', exact: true }).click()
      const m = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
      await sendPointer(page, [
        { phase: 'move', kind: 'mouse', id: 20, ...m },
        { phase: 'down', kind: 'mouse', id: 20, ...m, button: 0 },
        { phase: 'up', kind: 'mouse', id: 20, ...m, button: 0 },
      ])
      await bar.getByRole('button', { name: 'Mirror', exact: true }).click()
      await page.getByTestId('copy-hint').waitFor()
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    // F24: the Line tool aiming at the middle of a line drawn before it: the mark and the "Midpoint" label
    name: 'sketch-draw-midpoint',
    mock: 'sketch',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      await chooseWithRing(page, 'line')
      const a = await screenFor(page, -30, 10)
      const b = await screenFor(page, 30, 30)
      await place(page, a)
      await place(page, b)
      await place(page, b)
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.stats.lines ?? 0) >= 1)
      const mid = await screenFor(page, 0, 20)
      await sendPointer(page, [
        { phase: 'down', kind: 'pen', id: 10, x: mid.x + 24, y: mid.y },
        { phase: 'move', kind: 'pen', id: 10, x: mid.x + 4, y: mid.y - 3 },
      ])
      await page.locator('[data-kind="aim"][data-snap="mid"]').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    // I14.3: a circle selected, Linear pattern, the pen on a line right of it: the panel in the dialog area with the pad up on Instances (4 typed), and the ghost of the copies
    name: 'sketch-pattern-linear',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      const draw = async (id: string) => {
        await page.locator('.tool-strip').getByRole('button', { name: /^Draw/ }).click()
        await page.locator(`.group-row[data-tool="${id}"]`).click()
      }
      await draw('line')
      const b = await screenFor(page, 45, 12)
      for (const p of [await screenFor(page, -45, 12), b, b]) await place(page, p)
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.stats.lines ?? 0) >= 1)
      await draw('circle')
      await place(page, await screenFor(page, 0, 0))
      await place(page, await screenFor(page, 5, 0))
      await page.locator('.tool-strip').getByRole('button', { name: 'Select', exact: true }).click()
      await place(page, await screenFor(page, 0, -5))
      await draw('sk_lpattern')
      await place(page, await screenFor(page, 30, 12))
      await page.getByTestId('copy-panel').waitFor()
      await page.getByTestId('pad').getByRole('button', { name: '4', exact: true }).click()
      await page.locator('.sketch-layer g[data-preview="copies"]').waitFor({ state: 'attached' })
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    // I14.3: a circle at (20, 0) selected, Circular pattern, the pen on the origin: Instances 6 and Total angle 360 with the pad up, and the ghost of the six
    name: 'sketch-pattern-circular',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      const draw = async (id: string) => {
        await page.locator('.tool-strip').getByRole('button', { name: /^Draw/ }).click()
        await page.locator(`.group-row[data-tool="${id}"]`).click()
      }
      await draw('circle')
      await place(page, await screenFor(page, 20, 0))
      await place(page, await screenFor(page, 25, 0))
      await page.locator('.tool-strip').getByRole('button', { name: 'Select', exact: true }).click()
      await place(page, await screenFor(page, 20, -5))
      await draw('sk_cpattern')
      await place(page, await screenOf(page, 'point', 'origin'))
      await page.getByTestId('copy-panel').waitFor()
      await page.locator('.sketch-layer g[data-preview="copies"]').waitFor({ state: 'attached' })
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    // I14.3: Desktop mode, Linear pattern: the panel floating over the view with Instances and Spacing as typed fields, 4 typed, and the ghost
    name: 'desktop-sketch-pattern-linear',
    mode: 'desktop',
    desktop: 'desktop-sketch',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      const bar = page.locator('[data-strip="sketch"]')
      const click = (p: { x: number; y: number }) =>
        sendPointer(page, [
          { phase: 'move', kind: 'mouse', id: 20, ...p },
          { phase: 'down', kind: 'mouse', id: 20, ...p, button: 0 },
          { phase: 'up', kind: 'mouse', id: 20, ...p, button: 0 },
        ])
      await bar.getByRole('button', { name: 'Line', exact: true }).click()
      const b = await screenFor(page, 45, 12)
      for (const p of [await screenFor(page, -45, 12), b, b]) await click(p)
      await page.waitForFunction(() => (window.__freeshapeViewport?.sketch?.stats.lines ?? 0) >= 1)
      await bar.getByRole('button', { name: 'Circle', exact: true }).click()
      await click(await screenFor(page, 0, 0))
      await click(await screenFor(page, 5, 0))
      await bar.getByRole('button', { name: 'Select', exact: true }).click()
      await click(await screenFor(page, 0, -5))
      await bar.getByRole('button', { name: 'Linear pattern', exact: true }).click()
      await click(await screenFor(page, 30, 12))
      await page.getByTestId('copy-panel').waitFor()
      await page.keyboard.type('4')
      await page.keyboard.press('Tab')
      await page.locator('.sketch-layer g[data-preview="copies"]').waitFor({ state: 'attached' })
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    // I14.3: Desktop mode, Circular pattern about the origin: Instances 6 and Total angle 360 as typed fields, and the ghost of the six
    name: 'desktop-sketch-pattern-circular',
    mode: 'desktop',
    desktop: 'desktop-sketch',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      const bar = page.locator('[data-strip="sketch"]')
      const click = (p: { x: number; y: number }) =>
        sendPointer(page, [
          { phase: 'move', kind: 'mouse', id: 20, ...p },
          { phase: 'down', kind: 'mouse', id: 20, ...p, button: 0 },
          { phase: 'up', kind: 'mouse', id: 20, ...p, button: 0 },
        ])
      await bar.getByRole('button', { name: 'Circle', exact: true }).click()
      await click(await screenFor(page, 20, 0))
      await click(await screenFor(page, 25, 0))
      await bar.getByRole('button', { name: 'Select', exact: true }).click()
      await click(await screenFor(page, 20, -5))
      await bar.getByRole('button', { name: 'Circular pattern', exact: true }).click()
      await click(await screenOf(page, 'point', 'origin'))
      await page.getByTestId('copy-panel').waitFor()
      await page.locator('.sketch-layer g[data-preview="copies"]').waitFor({ state: 'attached' })
      await settledSketch(page)
    },
    after: closeAllDiscard,
  },
  {
    // I9.10: Desktop mode, a right click on a face of the bracket: the viewport menu at the pointer, beside the desktop's own
    name: 'desktop-context-face',
    mode: 'desktop',
    desktop: 'desktop-context',
    steps: async (page) => {
      await openApp(page)
      const [face] = await faceSpots(page, page.request, 'mouse')
      const canvas = (await page.locator('[data-region="viewport"] canvas').first().boundingBox())!
      await page.mouse.click(canvas.x + face!.x, canvas.y + face!.y, { button: 'right' })
      await page.getByTestId('context-menu').waitFor()
    },
  },
  {
    // I9.10: Desktop mode, a right click on the Extrude row: the feature menu with the items the client lacks dimmed
    name: 'desktop-context-feature',
    mode: 'desktop',
    desktop: 'desktop-studio',
    steps: async (page) => {
      await openApp(page)
      await featureRow(page, 'Extrude 1').click({ button: 'right' })
      await page.getByTestId('context-menu').waitFor()
    },
  },
  {
    // I9.10: Desktop mode, a right click on a part row (Export stays dimmed, decision d-s11-part-menu)
    name: 'desktop-context-part',
    mode: 'desktop',
    desktop: 'desktop-studio',
    steps: async (page) => {
      await openApp(page)
      await partRow(page, /./).first().click({ button: 'right' })
      await page.getByTestId('context-menu').waitFor()
    },
  },
  {
    // I9.10: Desktop mode, a right click on an Assembly instance in the list
    name: 'desktop-context-instance',
    mode: 'desktop',
    desktop: 'desktop-assembly',
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await settledAssembly(page)
      await page.locator('[data-instance="I2"]').click({ button: 'right' })
      await page.getByTestId('context-menu').waitFor()
    },
  },
  {
    // I9.10: Desktop mode, a right click in an open sketch: Normal to sketch, Finish sketch, Cancel sketch
    name: 'desktop-context-sketch',
    mode: 'desktop',
    desktop: 'desktop-sketch',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      const canvas = (await page.locator('[data-region="viewport"] canvas').first().boundingBox())!
      await page.mouse.click(canvas.x + canvas.width * 0.7, canvas.y + canvas.height * 0.6, { button: 'right' })
      await page.getByTestId('context-menu').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    // I9.11: Desktop mode, Ctrl+Space, ext typed: the Search tools palette over the Part Studio, Extrude first
    name: 'desktop-palette',
    mode: 'desktop',
    desktop: 'desktop-palette',
    steps: async (page) => {
      await openApp(page)
      await page.keyboard.press('Control+Space')
      await page.getByTestId('palette').waitFor()
      await page.keyboard.type('ext')
      await page.getByTestId('palette').getByRole('option').first().filter({ hasText: 'Extrude' }).waitFor()
    },
  },
  {
    // I9.11: Desktop mode, the palette with a tool that cannot run chosen: dimmed, its reason on the right
    name: 'desktop-palette-disabled',
    mode: 'desktop',
    desktop: 'desktop-palette',
    steps: async (page) => {
      await openApp(page)
      await page.keyboard.press('Control+Space')
      await page.getByTestId('palette').waitFor()
      await page.keyboard.type('variable studio')
      await page.getByTestId('palette').getByRole('option', { name: /^Variable Studio reference/ }).waitFor()
    },
  },
  {
    // I9.11: Desktop mode, the palette in an open sketch: the sketch tools
    name: 'desktop-palette-sketch',
    mode: 'desktop',
    desktop: 'desktop-palette',
    steps: async (page) => {
      await openApp(page)
      await openNewSketch(page, 'Top')
      await page.keyboard.press('Alt+KeyC')
      await page.getByTestId('palette').waitFor()
    },
    after: closeAllDiscard,
  },
  {
    // I9.11: Desktop mode, the ? button: every key of the binding data, grouped by workspace
    name: 'desktop-shortcuts',
    mode: 'desktop',
    desktop: 'desktop-shortcuts',
    steps: async (page) => {
      await openApp(page)
      await page.locator('[data-region="top-bar"]').getByRole('button', { name: 'Keyboard shortcuts' }).click()
      await page.getByTestId('shortcuts').waitFor()
    },
  },
  {
    // I10.1: the Insert panel over the left column in Tablet mode, on the hinge Assembly: Arm tapped and inserted, the panel still open
    name: 'insert-panel',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.locator('[data-strip="assembly"] [data-tool="insert"]').click()
      await page.locator('[data-picker-part]').nth(2).waitFor()
      await page.locator('[data-picker-part]', { hasText: /^Arm$/ }).click()
      await page.locator('[data-insert-go]').click()
      await page.getByTestId('insert-said').filter({ hasText: 'Inserted Arm' }).waitFor()
      await settledAssembly(page)
    },
    after: closeAllDiscard,
  },
  {
    // I10.1: the Insert panel floating over the view in Desktop mode, beside the desktop program's Assembly: Pin double-clicked, so inserted
    name: 'desktop-insert-panel',
    mode: 'desktop',
    desktop: 'desktop-assembly',
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      await openAssemblyTab(page, 3)
      await page.keyboard.press('i')
      await page.locator('[data-picker-part]').nth(2).waitFor()
      await page.locator('[data-picker-part]', { hasText: /^Pin$/ }).dblclick()
      await page.getByTestId('insert-said').filter({ hasText: 'Inserted Pin' }).waitFor()
      await settledAssembly(page)
    },
    after: closeAllDiscard,
  },
  {
    // I11.7: Tablet mode, the strip's Import: the file explorer over the left column, a folder open and an entry chosen
    name: 'explorer-add',
    mock: 'dialog',
    steps: async (page) => {
      await (await import('./support/library.ts')).mockLibrary(page)
      await openApp(page)
      await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
      await page.getByRole('menuitem', { name: /^Import/ }).click()
      const explorer = page.getByRole('dialog', { name: 'Files' })
      await explorer.getByRole('button', { name: /^bearings/ }).click()
      await explorer.getByRole('button', { name: /skate-608/ }).click()
      await explorer.getByTestId('ex-detail').waitFor()
      await settled(page)
    },
  },
  {
    // I11.7: Tablet mode, the part added: the status line says Imported <name>
    name: 'explorer-imported',
    mock: 'dialog',
    steps: async (page) => {
      await (await import('./support/library.ts')).mockLibrary(page)
      await openApp(page)
      await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
      await page.getByRole('menuitem', { name: /^Import/ }).click()
      const explorer = page.getByRole('dialog', { name: 'Files' })
      await explorer.getByRole('button', { name: /^fasteners/ }).click()
      await explorer.getByRole('button', { name: /m3-nut-block/ }).click()
      await explorer.getByRole('button', { name: 'Add to this Part Studio' }).click()
      await explorer.getByTestId('ex-status').filter({ hasText: 'Imported m3-nut-block' }).waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I11.7: Tablet mode, a search typed in the prompt that Tablet mode uses for text, as a tab's name is
    name: 'explorer-search',
    mock: 'dialog',
    steps: async (page) => {
      await (await import('./support/library.ts')).mockLibrary(page)
      await openApp(page)
      await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
      await page.getByRole('menuitem', { name: /^Import/ }).click()
      await page.getByRole('dialog', { name: 'Files' }).getByRole('button', { name: 'Search', exact: true }).click()
      await page.getByRole('dialog', { name: 'Search' }).getByRole('textbox').fill('skate')
      await page.getByRole('dialog', { name: 'Search' }).waitFor()
    },
  },
  {
    // I11.7: Tablet mode, the home screen's Files button: the explorer in browse mode over the Documents
    name: 'explorer-home',
    mock: null,
    steps: async (page) => {
      await (await import('./support/library.ts')).mockLibrary(page)
      await closeAllDiscard(page)
      await page.goto('/')
      await page.getByRole('button', { name: 'Files', exact: true }).click()
      const explorer = page.getByRole('dialog', { name: 'Files' })
      await explorer.getByRole('button', { name: /^fasteners/ }).click()
      await explorer.getByRole('button', { name: /m3-nut-block/ }).click()
      await explorer.getByTestId('ex-detail').waitFor()
    },
  },
  {
    // I11.7: Desktop mode, Ctrl+Shift+L: the explorer floating over the view, a search typed in the box, an entry chosen
    name: 'desktop-explorer',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await (await import('./support/library.ts')).mockLibrary(page)
      await openApp(page)
      await page.keyboard.press('Control+Shift+L')
      const explorer = page.getByRole('dialog', { name: 'Files' })
      await explorer.getByRole('textbox', { name: 'Search' }).fill('skate')
      await explorer.getByRole('button', { name: /skate-608/ }).click()
      await explorer.getByTestId('ex-detail').waitFor()
      await settled(page)
    },
  },
  {
    // I11.7: Desktop mode, the explorer on its Documents root
    name: 'desktop-explorer-documents',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await (await import('./support/library.ts')).mockLibrary(page)
      await openApp(page)
      await page.keyboard.press('Control+Shift+L')
      const explorer = page.getByRole('dialog', { name: 'Files' })
      await explorer.getByRole('navigation', { name: 'Roots' }).getByRole('button', { name: /^Documents/ }).click()
      await explorer.getByRole('button', { name: /^Hinge/ }).waitFor()
      await settled(page)
    },
  },
  {
    // I11.8: Tablet mode, From this device… under the list, and the status after a picked STEP went in
    name: 'device-imported',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page)
      await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
      await page.getByRole('menuitem', { name: /^Import/ }).click()
      const explorer = page.getByRole('dialog', { name: 'Files' })
      await explorer.getByTestId('ex-device-input').setInputFiles(new URL('./fixtures/box.step', import.meta.url).pathname)
      await explorer.getByTestId('ex-device-status').filter({ hasText: 'Imported box.step' }).waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I11.8: Tablet mode, an upload in flight (the server never answers): the progress bar and Cancel in the explorer
    name: 'device-progress',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page)
      await page.route(/\/tabs\/[^/]+\/import/, () => {})
      await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
      await page.getByRole('menuitem', { name: /^Import/ }).click()
      await page.getByRole('dialog', { name: 'Files' }).getByTestId('ex-device-input').setInputFiles(new URL('./fixtures/box.step', import.meta.url).pathname)
      await page.getByRole('dialog', { name: 'Files' }).getByTestId('upload-progress').waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I11.8: Desktop mode, From this device… in the floating explorer after a picked STEP went in
    name: 'desktop-device-imported',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await openApp(page)
      await page.keyboard.press('Control+Shift+L')
      const explorer = page.getByRole('dialog', { name: 'Files' })
      await explorer.getByTestId('ex-device-input').setInputFiles(new URL('./fixtures/box.step', import.meta.url).pathname)
      await explorer.getByTestId('ex-device-status').filter({ hasText: 'Imported box.step' }).waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I11.8: Desktop mode, an upload in flight in the explorer
    name: 'desktop-device-progress',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await openApp(page)
      await page.route(/\/tabs\/[^/]+\/import/, () => {})
      await page.keyboard.press('Control+Shift+L')
      await page.getByRole('dialog', { name: 'Files' }).getByTestId('ex-device-input').setInputFiles(new URL('./fixtures/box.step', import.meta.url).pathname)
      await page.getByRole('dialog', { name: 'Files' }).getByTestId('upload-progress').waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I11.8: Desktop mode, a STEP dropped on the view while the server is still taking it: the progress note over the view
    name: 'desktop-drop-progress',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await openApp(page)
      await page.route(/\/tabs\/[^/]+\/import/, () => {})
      await page.evaluate(() => {
        const dt = new DataTransfer()
        dt.items.add(new File([new Uint8Array(2000)], 'bracket-clip.step'))
        document.querySelector('[data-testid="shell-viewport"]')!.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }))
      })
      await page.getByTestId('drop-upload').waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I11.8: Desktop mode, a .png dropped on the view: the message that says why nothing happened
    name: 'desktop-drop-refused',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await openApp(page)
      await page.evaluate(() => {
        const dt = new DataTransfer()
        dt.items.add(new File([new Uint8Array(10)], 'photo.png'))
        document.querySelector('[data-testid="shell-viewport"]')!.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }))
      })
      await page.getByRole('dialog', { name: 'Import' }).waitFor()
      await settled(page)
    },
  },
  {
    // I11.10: the home screen's row menu on the Bracket: Download (.fsdoc); the header has Open file... beside New Document
    name: 'home-row-menu',
    mock: null, // the layout has no home screen
    steps: async (page) => {
      await openHome(page)
      await page.locator('li.doc-item', { has: page.locator('.doc-name', { hasText: /^Bracket$/ }) }).getByRole('button', { name: 'Document menu' }).click()
      await page.getByRole('menu', { name: 'Bracket menu' }).waitFor()
    },
  },
  {
    // I11.10: Tablet mode's Export button is a menu: Export STEP or STL... and Download Document
    name: 'export-menu',
    mock: 'browsing', // the layout draws no Export menu; the top bar and panel are the browsing state's
    steps: async (page) => {
      await openApp(page)
      await page.getByRole('button', { name: 'Export', exact: true }).click()
      await page.getByRole('menu', { name: 'Export menu' }).waitFor()
      await settled(page)
    },
  },
  {
    // I11.10: Download Document with a tab renamed and not saved: the server answers 409 and the client asks Save and download?
    name: 'download-save-ask',
    mock: 'browsing', // the layout draws no dialog of this kind
    steps: async (page) => {
      await openApp(page)
      await renameFirstTab(page)
      await page.getByTestId('dirty-dot').waitFor()
      await page.getByRole('button', { name: 'Export', exact: true }).click()
      await page.getByRole('menuitem', { name: 'Download Document' }).click()
      await page.getByRole('dialog', { name: 'Save and download?' }).waitFor()
      await settled(page)
    },
    after: restoreName,
  },
  {
    // I11.10: Desktop mode's Document menu with Open Document archive... after Open Document folder... and Save Document archive... after Save as...
    name: 'desktop-document-menu-archive',
    mode: 'desktop',
    desktop: 'desktop-studio',
    steps: async (page) => {
      await openApp(page)
      await page.locator('[data-region="top-bar"]').getByRole('button', { name: 'Document', exact: true }).click()
      await page.getByRole('menuitem', { name: 'Open Document archive…' }).waitFor()
      await page.locator('[role="menuitem"][data-command="document.openArchive"]:not([disabled])').waitFor()
      await settled(page)
    },
  },
  {
    // I11.10: Desktop mode's home screen with the row menu open on the Bracket
    name: 'desktop-home-row-menu',
    mode: 'desktop',
    desktop: 'desktop-studio', // the desktop's own home screen is not among the references: its main window is
    steps: async (page) => {
      await openHome(page)
      await page.locator('li.doc-item', { has: page.locator('.doc-name', { hasText: /^Bracket$/ }) }).getByRole('button', { name: 'Document menu' }).click()
      await page.getByRole('menu', { name: 'Bracket menu' }).waitFor()
    },
  },
  {
    // I10.2: the Derived dialog in Tablet mode, in a new Part Studio of the bracket: the bracket Part Studio chosen, its part ticked
    name: 'derived-dialog',
    mock: 'dialog',
    steps: async (page) => {
      await openApp(page)
      await page.getByRole('button', { name: 'Add tab' }).click()
      await page.getByRole('menuitem', { name: /^Part Studio$/ }).click()
      await page.locator('.tab-button[aria-current="page"]', { hasNotText: /bracket/ }).waitFor()
      await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
      await page.getByRole('menuitem', { name: /^Derived/ }).click()
      await page.locator('[data-picker-studio="T1"]').click()
      await page.getByRole('checkbox', { name: 'Bracket' }).click()
      await page.getByRole('tab', { name: 'Parts (1)' }).waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I10.2: the Derived dialog in Desktop mode beside the desktop program's feature dialog: the same choices, checkboxes in the tabs
    name: 'desktop-derived-dialog',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      await openApp(page)
      await page.getByRole('button', { name: 'Add tab' }).click()
      await page.getByRole('menuitem', { name: /^Create Part Studio/ }).click()
      await page.locator('.tab-button[aria-current="page"]', { hasNotText: /bracket/ }).waitFor()
      await page.locator('.desktop-toolbar').getByRole('button', { name: 'Derived', exact: true }).click()
      await page.locator('[data-picker-studio="T1"]').click()
      await page.getByRole('checkbox', { name: 'Bracket' }).click()
      await page.getByRole('tab', { name: 'Parts (1)' }).waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I10.3: Tablet mode, a Part Studio with a mate connector on a face of the bracket: its triad at a constant size over the model
    name: 'connectors-studio',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      const doc = await idOf(page.request, 'Bracket')
      const part = (await page.evaluate(() => window.__freeshapeViewport?.visiblePartIds[0])) ?? ''
      await (await import('./support/connectors.ts')).connectorOnFace(page, doc, part, 2, 'F90', 'Pin')
      await page.waitForFunction(() => (window.__freeshapeViewport?.connectors ?? 0) >= 1)
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I10.3: Desktop mode, the same connector beside the desktop program's Part Studio
    name: 'desktop-connectors-studio',
    mode: 'desktop',
    desktop: 'desktop-studio',
    steps: async (page) => {
      await openApp(page)
      const doc = await idOf(page.request, 'Bracket')
      const part = (await page.evaluate(() => window.__freeshapeViewport?.visiblePartIds[0])) ?? ''
      await (await import('./support/connectors.ts')).connectorOnFace(page, doc, part, 2, 'F90', 'Pin')
      await page.waitForFunction(() => (window.__freeshapeViewport?.connectors ?? 0) >= 1)
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I10.3: Tablet mode, the hinge Assembly: a connector on each of two parts (teal triads) and the two ends of each mate
    name: 'connectors-assembly',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      const doc = await idOf(page.request, 'Hinge')
      const { connectorOnFace } = await import('./support/connectors.ts')
      await connectorOnFace(page, doc, 'F2/1', 2, 'F90', 'Top')
      await connectorOnFace(page, doc, 'F4/1', 2, 'F91', 'Hub')
      await openAssemblyTab(page, 2)
      await page.waitForFunction(() => (window.__freeshapeAssembly?.connectors ?? 0) >= 2)
      await settledAssembly(page)
    },
    after: closeAllDiscard,
  },
  {
    // I10.3: Desktop mode, the same Assembly beside the desktop program's Assembly
    name: 'desktop-connectors-assembly',
    mode: 'desktop',
    desktop: 'desktop-assembly',
    steps: async (page) => {
      await openApp(page, { document: 'Hinge', model: false })
      const doc = await idOf(page.request, 'Hinge')
      const { connectorOnFace } = await import('./support/connectors.ts')
      await connectorOnFace(page, doc, 'F2/1', 2, 'F90', 'Top')
      await connectorOnFace(page, doc, 'F4/1', 2, 'F91', 'Hub')
      await openAssemblyTab(page, 2)
      await page.waitForFunction(() => (window.__freeshapeAssembly?.connectors ?? 0) >= 2)
      await settledAssembly(page)
    },
    after: closeAllDiscard,
  },
  {
    // I12.5: Tablet mode, a canvas on Top under and beside the Bracket (40 mm wide, left of the Bracket, 50 % opaque), drawn after the parts: the part hides what is behind it
    name: 'canvas-placed',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      const { canvasOnTop, waitPictured } = await import('./support/canvas.ts')
      await canvasOnTop(page, await idOf(page.request, 'Bracket'), { width: 40, x: -20 })
      await waitPictured(page)
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I12.5: Tablet mode, the Canvas tool's dialog open on an uploaded PNG: its rows, the plane field, and the picture drawn as the preview
    name: 'canvas-dialog',
    mock: 'dialog',
    steps: async (page) => {
      const { CANVAS_PNG, emptyDocument } = await import('./support/canvas.ts')
      await emptyDocument(page, 'Canvas shot')
      await openApp(page, { document: 'Canvas shot', model: false })
      const [chooser] = await Promise.all([page.waitForEvent('filechooser'), (async () => {
        await page.locator('.tool-strip').getByRole('button', { name: /^Create/ }).click()
        await page.getByRole('menuitem', { name: /^Canvas/ }).click()
      })()])
      await chooser.setFiles(CANVAS_PNG)
      await page.getByRole('dialog', { name: /Canvas/ }).waitFor()
      await page.waitForFunction(() => (window.__freeshapeViewport?.canvases ?? 0) >= 1 && (window.__freeshapeViewport?.canvasScreens() ?? []).every((c) => c.pictured))
      await settled(page)
    },
    after: async (page) => {
      await closeAll(page.request)
      ;(await import('./support/docs.ts')).removeDocument('Canvas shot')
    },
  },
  {
    // I12.5: Tablet mode, the placed canvas picked with the pen: its row selected in the list, the chip saying 1 canvas, the canvas tinted
    name: 'canvas-selected',
    mock: 'browsing',
    steps: async (page) => {
      await openApp(page)
      const { canvasOnTop, waitPictured } = await import('./support/canvas.ts')
      await canvasOnTop(page, await idOf(page.request, 'Bracket'), { width: 40, x: -20 })
      await waitPictured(page)
      const c = (await page.evaluate(() => window.__freeshapeViewport!.canvasScreens()))[0]!
      // the visible left part of the picture: the centre is behind the Bracket's wall from this view
      const at = { x: c.x - 45, y: c.y - 8 }
      await penStroke(page, at, at)
      await page.getByTestId('selection-chip').getByText('1 canvas').waitFor()
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I12.5: Desktop mode, the same placed canvas beside the desktop program's Part Studio
    name: 'desktop-canvas-placed',
    mode: 'desktop',
    desktop: 'desktop-studio',
    steps: async (page) => {
      await openApp(page)
      const { canvasOnTop, waitPictured } = await import('./support/canvas.ts')
      await canvasOnTop(page, await idOf(page.request, 'Bracket'), { width: 40, x: -20 })
      await waitPictured(page)
      await settled(page)
    },
    after: closeAllDiscard,
  },
  {
    // I12.5: Desktop mode, the Canvas dialog open on an uploaded PNG, floating over the view, beside the desktop program's dialog
    name: 'desktop-canvas-dialog',
    mode: 'desktop',
    desktop: 'desktop-dialog',
    steps: async (page) => {
      const { CANVAS_PNG, emptyDocument } = await import('./support/canvas.ts')
      await emptyDocument(page, 'Canvas shot')
      await openApp(page, { document: 'Canvas shot', model: false })
      const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.locator('.desktop-toolbar [data-tool="canvas"]').click()])
      await chooser.setFiles(CANVAS_PNG)
      await page.getByRole('dialog', { name: /Canvas/ }).waitFor()
      await page.waitForFunction(() => (window.__freeshapeViewport?.canvases ?? 0) >= 1 && (window.__freeshapeViewport?.canvasScreens() ?? []).every((c) => c.pictured))
      await settled(page)
    },
    after: async (page) => {
      await closeAll(page.request)
      ;(await import('./support/docs.ts')).removeDocument('Canvas shot')
    },
  },
]

/** I11.14: the server lists an export root "shared" (the shots server has none); a save answers 409 exists when `taken`, else 201. */
async function mockExportRoot(page: Page, taken: boolean): Promise<void> {
  await page.route('**/files', (route) => route.fulfill({ json: { roots: [], export_roots: [{ name: 'shared', max_bytes: 268435456 }] } }))
  await page.route('**/tabs/*/export', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill(taken ? { status: 409, json: { error: 'exists', name: 'Bracket.stl' } } : { status: 201, json: { root: 'shared', name: 'Bracket.stl', bytes: 1, replaced: false, skipped: [] } })
      : route.fallback(),
  )
}

/** I11.14: Document > Export STL, Destination set to Save to shared. */
async function openDesktopSave(page: Page): Promise<void> {
  await page.locator('[data-region="top-bar"]').getByRole('button', { name: 'Document', exact: true }).click()
  await page.getByRole('menuitem', { name: 'Export STL…' }).click()
  const dialog = page.getByRole('dialog', { name: 'Export' })
  await dialog.getByRole('button', { name: /^Destination/ }).click()
  await page.getByRole('menuitemcheckbox', { name: 'Save to shared' }).click()
  await dialog.getByRole('textbox', { name: 'File name' }).waitFor()
}

/** I9.9: drag a selection box with the mouse from above left of the bracket to below its middle; `release` lets the button go. */
async function mouseBox(page: Page, release: boolean): Promise<void> {
  const cam = (await settled(page)).camera
  const on = await page.evaluate((c) => {
    const out: { x: number; y: number }[] = []
    for (let y = 8; y < c.height - 8; y += 10) for (let x = 8; x < c.width - 8; x += 10) if (window.__freeshapeViewport!.pickAt(x, y, 'mouse', ['face', 'edge'])) out.push({ x, y })
    return out
  }, cam)
  const [x0, y0, x1, y1] = [Math.min(...on.map((s) => s.x)), Math.min(...on.map((s) => s.y)), Math.max(...on.map((s) => s.x)), Math.max(...on.map((s) => s.y))]
  const canvas = (await page.locator('.viewport-canvas').boundingBox())!
  await page.mouse.move(canvas.x + x0 - 12, canvas.y + y0 - 12)
  await page.mouse.down()
  await page.mouse.move(canvas.x + (x0 + x1) / 2, canvas.y + y1 + 12, { steps: 8 })
  if (!release) return page.getByTestId('select-box').waitFor()
  await page.mouse.up()
  // the pointer rests a little off the box's corner, where the count shows beside it
  await page.mouse.move(canvas.x + (x0 + x1) / 2 + 20, canvas.y + y1 - 20)
}

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
