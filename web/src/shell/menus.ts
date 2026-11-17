import { chordFor } from '../input/bindings'

/**
 * The Desktop-mode menus, as data: the desktop's Document, View and Select menus (freeshape/ui/mainwindow.py,
 * `_build_topbar`) in its order and with its separators, plus the bottom strip's + menu. One Menu component draws them.
 *
 * An item names a command. The shell supplies the commands the client can do (`Commands`); an item whose command is not
 * supplied is shown disabled with the tooltip `DESKTOP_ONLY`. A card that wires an item adds its command to the shell,
 * nothing here changes.
 */

/** What an enabled rule reads. */
export interface MenuState {
  /** The Document is open read-only: nothing that edits or saves. */
  readOnly: boolean
  /** The kind of the tab on screen (studio, assembly, ...), or null with no tab. */
  tabKind: string | null
  /** A sketch is open on the tab on screen. */
  sketchOpen?: boolean
  /** The state of check items the client keeps itself, by command (View > Show planes): overrides the item's `checked`. */
  checks?: Readonly<Record<string, boolean>>
}

export interface MenuItem {
  kind?: 'item'
  /** The command that runs it, "<menu>.<name>". */
  command: string
  label: string
  /** The key that runs it, shown at the right: read from input/bindings.ts. */
  chord?: string
  /** A check item: shown with a mark when `checked`. */
  check?: boolean
  checked?: boolean
  /** Opens a submenu on the desktop. */
  submenu?: boolean
  /** The submenu's entries, when the client has them: the item then opens them (below it) instead of running a command. */
  items?: readonly MenuEntry[]
  /** Whether the item is on now, when its command is wired. Default: on. */
  enabled?: (state: MenuState) => boolean
  /** The tooltip of the item while its command is not wired, in place of DESKTOP_ONLY (a context menu's item that waits for a named card). */
  note?: string
}

export interface MenuSeparator {
  kind: 'separator'
}

export type MenuEntry = MenuItem | MenuSeparator

/** The commands the shell can run, by name. */
export type Commands = Readonly<Record<string, () => void>>

/** The tooltip of an item the client cannot do yet. */
export const DESKTOP_ONLY = 'Desktop only for now'

const SEP: MenuSeparator = { kind: 'separator' }
const editable = (s: MenuState): boolean => !s.readOnly
const canExport = (s: MenuState): boolean => (s.tabKind === 'studio' || s.tabKind === 'assembly') && s.sketchOpen !== true
const hasView = (s: MenuState): boolean => s.tabKind === 'studio' || s.tabKind === 'assembly'

/** Document (mainwindow.py `_build_topbar`, the Document menu). */
export const documentMenu: readonly MenuEntry[] = [
  { command: 'document.newStudio', label: 'New Part Studio', chord: chordFor('document.newStudio'), enabled: editable },
  { command: 'document.newAssembly', label: 'New Assembly', chord: chordFor('document.newAssembly'), enabled: editable },
  { command: 'document.newDrawing', label: 'New Drawing…' },
  { command: 'document.open', label: 'Open…', chord: chordFor('document.open') },
  { command: 'document.newFolder', label: 'New Document folder…' },
  { command: 'document.openFolder', label: 'Open Document folder…' },
  { command: 'document.openArchive', label: 'Open Document archive…', note: 'This server holds one Document and cannot add another from a file' },
  { command: 'document.addFiles', label: 'Add files to Document…' },
  { command: 'document.recent', label: 'Open recent', submenu: true },
  { command: 'document.closeTab', label: 'Close tab', chord: chordFor('document.closeTab'), enabled: (s) => !s.readOnly && s.tabKind !== null },
  { command: 'document.cancelSketch', label: 'Cancel sketch', enabled: (s) => s.sketchOpen === true },
  SEP,
  { command: 'document.save', label: 'Save', chord: chordFor('document.save'), enabled: editable },
  { command: 'document.saveAs', label: 'Save as…', chord: chordFor('document.saveAs') },
  { command: 'document.saveArchive', label: 'Save Document archive…' },
  SEP,
  { command: 'document.importNew', label: 'Import STEP / BREP (new tab)…', enabled: editable },
  { command: 'document.importInto', label: 'Import STEP / BREP into this tab…', enabled: (s) => !s.readOnly && s.tabKind === 'studio' && s.sketchOpen !== true },
  { command: 'document.library', label: 'Component library…', chord: chordFor('document.library') },
  { command: 'document.exportStep', label: 'Export STEP…', enabled: canExport },
  { command: 'document.exportStl', label: 'Export STL…', enabled: canExport },
  { command: 'document.exportBrep', label: 'Export BREP…' },
  { command: 'document.exportDxf', label: 'Export DXF (selected sketches / flat faces)…' },
  { command: 'document.nest', label: 'Nest sheet metal for cutting…' },
  SEP,
  { command: 'document.plugins', label: 'Plugins', submenu: true },
  SEP,
  { command: 'document.relation', label: 'Add relation (Assembly)', submenu: true },
  SEP,
  { command: 'document.shortcuts', label: 'Keyboard shortcuts' },
  { command: 'document.about', label: 'About FreeShape' },
  { command: 'document.quit', label: 'Quit', chord: chordFor('document.quit') },
]

/** The standard views, in the desktop's order (View menu, Shift+1 to Shift+7). */
export const STANDARD_VIEW_ITEMS = [
  ['front', 'Front'],
  ['back', 'Back'],
  ['left', 'Left'],
  ['right', 'Right'],
  ['top', 'Top'],
  ['bottom', 'Bottom'],
  ['iso', 'Isometric'],
] as const

/** The desktop's mouse styles (freeshape/ui/navstyles.py `STYLES`), in its order. The client navigates Onshape style only (I9.9). */
export const MOUSE_STYLES = [
  ['onshape', 'Onshape'],
  ['touchpad', 'Touchpad'],
  ['cad', 'CAD (FreeCAD)'],
  ['gesture', 'Gesture (FreeCAD)'],
  ['blender', 'Blender'],
  ['solidworks', 'SolidWorks'],
  ['openinventor', 'OpenInventor (FreeCAD)'],
  ['revit', 'Revit'],
  ['openscad', 'OpenSCAD'],
] as const

/** View > Mouse style: every desktop style listed, Onshape checked and the only one that can be chosen. */
export const mouseStyleMenu: readonly MenuEntry[] = MOUSE_STYLES.map(([id, label]): MenuItem => ({ command: `view.mouse.${id}`, label, check: true, checked: id === 'onshape' }))

/** Commands the menus answer themselves, with nothing of the shell's behind them: Onshape is the mouse style already. */
export const MENU_COMMANDS: Commands = { 'view.mouse.onshape': () => {} }

/** View (mainwindow.py, the View menu). */
export const viewMenu: readonly MenuEntry[] = [
  ...STANDARD_VIEW_ITEMS.map(([name, label]): MenuItem => ({ command: `view.${name}`, label, chord: chordFor(`view.${name}`), enabled: hasView })),
  SEP,
  { command: 'view.section', label: 'Section view', chord: chordFor('view.section'), check: true, checked: false },
  { command: 'view.fit', label: 'Zoom to fit', chord: chordFor('view.fit'), enabled: hasView },
  // the two the visibility keys share (card I9.8): P and Shift+Y; the tick of Show planes is `MenuState.checks`
  { command: 'view.planes', label: 'Show planes', chord: chordFor('view.planes'), check: true, checked: true, enabled: (s) => s.tabKind === 'studio' },
  { command: 'view.showAll', label: 'Show all parts', chord: chordFor('view.showAll'), enabled: hasView },
  { command: 'view.shiny', label: 'Shiny surfaces', check: true, checked: false },
  { command: 'view.through', label: 'Select through context', check: true, checked: false },
  { command: 'view.rotate', label: 'Rotate around', submenu: true },
  { command: 'view.mouse', label: 'Mouse style', submenu: true, items: mouseStyleMenu },
  { command: 'view.zoomSpeed', label: 'Zoom speed', submenu: true },
  SEP,
  { command: 'view.theme', label: 'Theme', submenu: true },
]

/** Select: what a click or box selection may pick (mainwindow.py, the Select menu; the desktop's defaults). */
export const selectMenu: readonly MenuEntry[] = [
  { command: 'select.vertex', label: 'Vertices', check: true, checked: false },
  { command: 'select.edge', label: 'Edges', check: true, checked: true },
  { command: 'select.face', label: 'Faces', check: true, checked: true },
  { command: 'select.part', label: 'Solids (whole parts)', check: true, checked: false },
  { command: 'select.sketch', label: 'Sketches', check: true, checked: true },
  { command: 'select.plane', label: 'Planes', check: true, checked: true },
  { command: 'select.axis', label: 'Axes', check: true, checked: true },
  SEP,
  { command: 'select.reset', label: 'Reset to default' },
]

/** The bottom strip's + button (mainwindow.py `_build_ui`, the tab strip). */
export const createMenu: readonly MenuEntry[] = [
  { command: 'document.newStudio', label: 'Create Part Studio', enabled: editable },
  { command: 'document.newAssembly', label: 'Create Assembly', enabled: editable },
  { command: 'document.newDrawing', label: 'Create Drawing…' },
]

/** The menu buttons of the top bar, left to right. */
export const MENU_BAR: readonly { id: string; label: string; entries: readonly MenuEntry[] }[] = [
  { id: 'document', label: 'Document', entries: documentMenu },
  { id: 'view', label: 'View', entries: viewMenu },
  { id: 'select', label: 'Select', entries: selectMenu },
]

/** What runs an item: the shell's command, else one the menus answer themselves (MENU_COMMANDS); undefined when there is none. */
export const commandOf = (item: MenuItem, commands: Commands): (() => void) | undefined => commands[item.command] ?? MENU_COMMANDS[item.command]

/** Whether the client can do the item at all now (its command is supplied, or it opens a submenu the client has). */
export const isWired = (item: MenuItem, commands: Commands): boolean => item.items !== undefined || commandOf(item, commands) !== undefined

/** Whether the item can be run now: wired, and its enabled rule holds. */
export const isRunnable = (item: MenuItem, commands: Commands, state: MenuState): boolean => isWired(item, commands) && (item.enabled?.(state) ?? true)
