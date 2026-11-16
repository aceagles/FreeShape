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
}

export interface MenuItem {
  kind?: 'item'
  /** The command that runs it, "<menu>.<name>". */
  command: string
  label: string
  /** The desktop's shortcut, shown at the right. */
  chord?: string
  /** A check item: shown with a mark when `checked`. */
  check?: boolean
  checked?: boolean
  /** Opens a submenu on the desktop. */
  submenu?: boolean
  /** Whether the item is on now, when its command is wired. Default: on. */
  enabled?: (state: MenuState) => boolean
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
const hasView = (s: MenuState): boolean => s.tabKind === 'studio' || s.tabKind === 'assembly'

/** Document (mainwindow.py `_build_topbar`, the Document menu). */
export const documentMenu: readonly MenuEntry[] = [
  { command: 'document.newStudio', label: 'New Part Studio', chord: 'Ctrl+N', enabled: editable },
  { command: 'document.newAssembly', label: 'New Assembly', chord: 'Ctrl+Shift+N', enabled: editable },
  { command: 'document.newDrawing', label: 'New Drawing…' },
  { command: 'document.open', label: 'Open…', chord: 'Ctrl+O' },
  { command: 'document.newFolder', label: 'New Document folder…' },
  { command: 'document.openFolder', label: 'Open Document folder…' },
  { command: 'document.addFiles', label: 'Add files to Document…' },
  { command: 'document.recent', label: 'Open recent', submenu: true },
  { command: 'document.closeTab', label: 'Close tab', chord: 'Ctrl+W', enabled: (s) => !s.readOnly && s.tabKind !== null },
  SEP,
  { command: 'document.save', label: 'Save', chord: 'Ctrl+S', enabled: editable },
  { command: 'document.saveAs', label: 'Save as…', chord: 'Ctrl+Shift+S' },
  SEP,
  { command: 'document.importNew', label: 'Import STEP / BREP (new tab)…' },
  { command: 'document.importInto', label: 'Import STEP / BREP into this tab…' },
  { command: 'document.library', label: 'Component library…', chord: 'Ctrl+Shift+L' },
  { command: 'document.exportStep', label: 'Export STEP…' },
  { command: 'document.exportStl', label: 'Export STL…' },
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
  { command: 'document.quit', label: 'Quit', chord: 'Ctrl+Q' },
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

/** View (mainwindow.py, the View menu). */
export const viewMenu: readonly MenuEntry[] = [
  ...STANDARD_VIEW_ITEMS.map(([name, label], i): MenuItem => ({ command: `view.${name}`, label, chord: `Shift+${i + 1}`, enabled: hasView })),
  SEP,
  { command: 'view.section', label: 'Section view', chord: 'Ctrl+Shift+X', check: true, checked: false },
  { command: 'view.fit', label: 'Zoom to fit', chord: 'F', enabled: hasView },
  { command: 'view.planes', label: 'Show planes', chord: 'P', check: true, checked: true },
  { command: 'view.showAll', label: 'Show all parts' },
  { command: 'view.shiny', label: 'Shiny surfaces', check: true, checked: false },
  { command: 'view.through', label: 'Select through context', check: true, checked: false },
  { command: 'view.rotate', label: 'Rotate around', submenu: true },
  { command: 'view.mouse', label: 'Mouse style', submenu: true },
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

/** Whether the client can do the item at all now (its command is supplied). */
export const isWired = (item: MenuItem, commands: Commands): boolean => commands[item.command] !== undefined

/** Whether the item can be run now: wired, and its enabled rule holds. */
export const isRunnable = (item: MenuItem, commands: Commands, state: MenuState): boolean => isWired(item, commands) && (item.enabled?.(state) ?? true)
