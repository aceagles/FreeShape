/**
 * The desktop's keys, as data (card I9.6): one entry per chord, with the command it runs, its label and the workspaces it
 * works in. The keymap (input/keys.ts), the menus' chord hints (shell/menus.ts), the tool tooltips and the shortcut list all
 * read this table. Read from freeshape/ui/mainwindow.py (`_bind_shortcuts`, the menus' `_add_action` shortcuts, `_escape`,
 * `_delete_key`) and freeshape/ui/viewport.py (`keyPressEvent`).
 *
 * Browser-reserved chords. web/e2e/s9_keys.spec.ts presses each chord on a page that takes it with preventDefault and
 * records whether the page got it and whether the browser then acted (a tab opened or closed). Playwright 1.64.1:
 *
 *   chord          WebKit (headless, headed)   Chromium (headless, headed)   real desktop browsers (not visible to Playwright)
 *   Ctrl+W         reaches page, held          reaches page, held            Chrome, Edge, Firefox close the tab: kept
 *   Ctrl+N         reaches page, held          reaches page, held            Chrome, Edge, Firefox open a window: kept
 *   Ctrl+Shift+N   reaches page, held          reaches page, held            Chrome, Edge open a private window: kept
 *   Ctrl+O         reaches page, held          reaches page, held            preventable in Chrome, Firefox, Safari
 *   Ctrl+Q         reaches page, held          reaches page, held            Firefox on Linux quits: kept there
 *   Ctrl+T         reaches page, held          reaches page, held            Chrome, Edge, Firefox open a tab: kept
 *
 * Playwright sends its keys to the page past the browser's own shortcuts: headed Chromium with no preventDefault at all opens
 * no tab on Ctrl+T and closes none on Ctrl+W. So the test cannot see what a real browser keeps, and the last column is
 * Chromium's list of reserved shortcuts (a page can never take them) and Firefox's. Safari on the iPad uses Cmd for its own
 * shortcuts, so all six Ctrl chords reach the page there. Nothing here binds a chord in the last column marked kept: New Part
 * Studio, New Assembly and Close tab take Alt+N, Alt+Shift+N and Alt+W until Adam decides (the desktop's chords are the
 * ones the browser keeps). Ctrl+O stays. Ctrl+Q (Quit) and Ctrl+T are not bound: Quit is desktop only.
 */

/** Where a key works: a Part Studio, an open sketch, an Assembly, or a feature dialog. */
export type Workspace = 'studio' | 'sketch' | 'assembly' | 'dialog'

export interface Binding {
  /** The chord as input/keys.ts `chordOf` writes it: modifiers in the order Ctrl, Alt, Shift, Meta, then the key. */
  chord: string
  /** The command it runs (the menus' names where a menu item does the same). */
  command: string
  label: string
  where: readonly Workspace[]
  /** Shown beside a desktop-only menu item but not bound: the client cannot do it yet. */
  shownOnly?: true
}

const ALL: readonly Workspace[] = ['studio', 'sketch', 'assembly', 'dialog']
const VIEWS: readonly Workspace[] = ['studio', 'assembly']

/** The standard views, Shift+1 to Shift+7, in the desktop's order. */
const STANDARD_VIEWS = [
  ['front', 'Front'],
  ['back', 'Back'],
  ['left', 'Left'],
  ['right', 'Right'],
  ['top', 'Top'],
  ['bottom', 'Bottom'],
  ['iso', 'Isometric'],
] as const

/** Every desktop key, in the order a shortcut list shows them. */
export const BINDINGS: readonly Binding[] = [
  { chord: 'Ctrl+Z', command: 'edit.undo', label: 'Undo', where: ['studio', 'sketch'] },
  { chord: 'Ctrl+Y', command: 'edit.redo', label: 'Redo', where: ['studio', 'sketch'] },
  { chord: 'Ctrl+Shift+Z', command: 'edit.redo', label: 'Redo', where: ['studio', 'sketch'] },

  { chord: 'Ctrl+S', command: 'document.save', label: 'Save', where: ALL },
  { chord: 'Ctrl+Shift+S', command: 'document.saveAs', label: 'Save as…', where: ALL, shownOnly: true },
  { chord: 'Alt+N', command: 'document.newStudio', label: 'New Part Studio', where: ALL },
  { chord: 'Alt+Shift+N', command: 'document.newAssembly', label: 'New Assembly', where: ALL },
  { chord: 'Alt+W', command: 'document.closeTab', label: 'Close tab', where: ALL },
  { chord: 'Ctrl+O', command: 'document.open', label: 'Open…', where: ALL },
  { chord: 'Ctrl+Shift+L', command: 'document.library', label: 'Component library…', where: ALL, shownOnly: true },
  { chord: 'Ctrl+Q', command: 'document.quit', label: 'Quit', where: ALL, shownOnly: true },

  { chord: 'Shift+S', command: 'feature.sketch', label: 'New sketch', where: ['studio'] },
  { chord: 'Shift+E', command: 'feature.extrude', label: 'Extrude', where: ['studio', 'sketch'] },
  { chord: 'Shift+W', command: 'feature.revolve', label: 'Revolve', where: ['studio', 'sketch'] },
  { chord: 'Shift+F', command: 'feature.fillet', label: 'Fillet (Fastened mate in an Assembly)', where: ['studio', 'assembly'] },

  { chord: 'N', command: 'view.normal', label: 'Normal to', where: ['studio', 'sketch'] },
  { chord: 'F', command: 'view.fit', label: 'Zoom to fit', where: VIEWS },
  ...STANDARD_VIEWS.map(([name, label], i): Binding => ({ chord: `Shift+${i + 1}`, command: `view.${name}`, label, where: VIEWS })),
  { chord: 'Ctrl+Shift+X', command: 'view.section', label: 'Section view', where: VIEWS, shownOnly: true },
  { chord: 'P', command: 'view.planes', label: 'Show planes', where: VIEWS, shownOnly: true },
  { chord: 'ArrowLeft', command: 'view.turnLeft', label: 'Turn the view 15°', where: VIEWS },
  { chord: 'ArrowRight', command: 'view.turnRight', label: 'Turn the view 15°', where: VIEWS },
  { chord: 'ArrowUp', command: 'view.turnUp', label: 'Turn the view 15°', where: VIEWS },
  { chord: 'ArrowDown', command: 'view.turnDown', label: 'Turn the view 15°', where: VIEWS },
  { chord: 'Ctrl+Shift+ArrowLeft', command: 'view.panLeft', label: 'Pan a tenth of the view', where: VIEWS },
  { chord: 'Ctrl+Shift+ArrowRight', command: 'view.panRight', label: 'Pan a tenth of the view', where: VIEWS },
  { chord: 'Ctrl+Shift+ArrowUp', command: 'view.panUp', label: 'Pan a tenth of the view', where: VIEWS },
  { chord: 'Ctrl+Shift+ArrowDown', command: 'view.panDown', label: 'Pan a tenth of the view', where: VIEWS },

  { chord: 'Space', command: 'select.clear', label: 'Clear the selection', where: ['studio', 'sketch', 'assembly'] },
  { chord: 'Delete', command: 'delete', label: 'Delete', where: ['studio', 'sketch', 'assembly'] },
  { chord: 'Backspace', command: 'delete', label: 'Delete', where: ['studio', 'sketch', 'assembly'] },
  { chord: 'Escape', command: 'cancel', label: 'Cancel / step back', where: ALL },
  { chord: 'Enter', command: 'dialog.accept', label: 'Accept the dialog', where: ['dialog'] },
]

/**
 * Decision d-s9-tablet-keys (open for Adam): which chords Tablet mode binds. F13's Esc and Delete for now; set it to 'all'
 * to give Tablet mode every desktop key. The one place that decides it.
 */
export const TABLET_CHORDS: readonly string[] | 'all' = ['Escape', 'Delete', 'Backspace']

/** The chord shown beside a command (a menu item's hint, a tooltip), or undefined when no key runs it. */
export function chordFor(command: string): string | undefined {
  return BINDINGS.find((b) => b.command === command)?.chord
}

/** The chords bound in Desktop mode: chord to command name. */
export const desktopKeymap: Readonly<Record<string, string>> = Object.fromEntries(BINDINGS.filter((b) => !b.shownOnly).map((b) => [b.chord, b.command]))
