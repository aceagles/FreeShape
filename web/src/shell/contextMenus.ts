import type { PickKind } from '../viewport/pick'
import type { FeatureEntry } from '../wire'
import { STANDARD_VIEW_ITEMS } from './menus'
import type { Commands, MenuEntry, MenuItem } from './menus'

/**
 * The right-click menus (card I9.10), as data in the menu format of menus.ts: the desktop's items in its order
 * (freeshape/ui/mainwindow.py `_viewport_context`, `_tree_context`, `_part_context`, `_tab_context`; freeshape/ui/assembly_ui.py
 * `instance_menu`, `mate_menu`, `viewport_menu`). A builder is given what the owner of the menu can do as optional
 * callbacks. An item whose callback is not given is listed without a command, so the Menu component shows it disabled with
 * the tooltip "Desktop only for now". Conditions the desktop puts on an item (a Context row's items, a sketch's DXF export)
 * decide here whether it is listed at all.
 */
export interface ContextMenuData {
  entries: readonly MenuEntry[]
  commands: Commands
}

/** The tooltip of the one item that waits for a named card, in place of "Desktop only for now". */
export const WAITS_FOR_I10_4 = 'Waits for the mate connector card (I10.4)'

class Builder {
  readonly entries: MenuEntry[] = []
  readonly commands: Record<string, () => void> = {}

  /** An item; with a `run` it is wired, and `on` false shows it disabled all the same. */
  item(command: string, label: string, run?: () => void, extra: Partial<MenuItem> = {}): this {
    if (run) this.commands[command] = run
    this.entries.push({ command, label, ...extra })
    return this
  }

  /** A line between two groups; none at the start, none twice in a row. */
  sep(): this {
    const last = this.entries.at(-1)
    if (last !== undefined && last.kind !== 'separator') this.entries.push({ kind: 'separator' })
    return this
  }

  /** The disabled line that carries a failure's message. */
  note(command: string, text: string): this {
    this.commands[command] = () => {}
    this.entries.push({ command, label: text, enabled: () => false })
    return this
  }

  done(): ContextMenuData {
    if (this.entries.at(-1)?.kind === 'separator') this.entries.pop()
    return { entries: this.entries, commands: this.commands }
  }
}

/** What a feature row's menu can do; `edit` is absent while a dialog or a sketch is open. */
export interface FeatureActions {
  edit?: () => void
  rename: () => void
  setHidden: (hidden: boolean) => void
  setSuppressed: (value: boolean) => void
  /** Roll the bar to `index` features that build. */
  rollTo: (index: number) => void
  remove: () => void
}

/** The kinds that have an eye. */
const EYED = new Set(['sketch', 'plane', 'axis'])

/**
 * A feature row (`_tree_context`): Edit, Rename, the Context row's items, Hide or Show, New sketch on plane, Export DXF,
 * Suppress, Roll to here, Roll to end, the folder items, Delete, and a failed feature's message. Edit, Rename, Hide or Show
 * (and Hide or Show context), Suppress, Roll to here, Roll to end and Delete are the client's. A folder's New folder and Remove
 * from folder, Update context, New sketch on plane and Export DXF are not.
 */
export function featureMenu(f: FeatureEntry, c: { index: number; total: number; rollback: number; busy: boolean; actions: FeatureActions }): ContextMenuData {
  const { actions: a, busy } = c
  const b = new Builder()
  b.item('feature.edit', 'Edit…', a.edit ?? (() => {}), { enabled: () => a.edit !== undefined && !busy })
  b.item('feature.rename', 'Rename…', a.rename, { enabled: () => !busy })
  if (f.type === 'context') {
    b.item('feature.updateContext', 'Update context')
    b.item('feature.hideContext', f.hidden ? 'Show context' : 'Hide context', () => a.setHidden(!f.hidden), { enabled: () => !busy })
  }
  if (EYED.has(f.type)) b.item('feature.hide', f.hidden ? 'Show' : 'Hide', () => a.setHidden(!f.hidden), { enabled: () => !busy })
  if (f.type === 'plane') b.item('feature.sketchOnPlane', 'New sketch on plane')
  if (f.type === 'sketch') b.item('feature.exportDxf', 'Export DXF…')
  b.sep()
  b.item('feature.suppress', f.suppressed ? 'Unsuppress' : 'Suppress', () => a.setSuppressed(!f.suppressed), { enabled: () => !busy })
  b.item('feature.rollHere', 'Roll to here', () => a.rollTo(c.index + 1), { enabled: () => !busy && c.rollback !== c.index + 1 })
  if (c.rollback < c.total) b.item('feature.rollEnd', 'Roll to end', () => a.rollTo(c.total), { enabled: () => !busy })
  b.sep()
  b.item('feature.newFolder', 'Add to new folder')
  if (f.folder !== null) b.item('feature.removeFromFolder', 'Remove from folder')
  b.sep()
  b.item('feature.delete', 'Delete', a.remove, { enabled: () => !busy })
  if (f.error !== null) b.sep().note('feature.error', `⚠ ${f.error}`)
  return b.done()
}

export interface PartActions {
  rename: () => void
  colour: () => void
  setHidden: (hidden: boolean) => void
  /** Absent where the view cannot isolate. */
  isolate?: () => void
  /** Absent where the client cannot open the Delete part feature. */
  remove?: () => void
}

/**
 * A part row (`_part_context`): Rename, Change colour, Material, Hide or Show, Isolate, Mass properties, Create drawing, the
 * three Export items, Delete part. Export stays off here: the parts-list export is left for later (decision d-s11-part-menu).
 * The sheet metal items are not listed: the client does not know a part is sheet metal.
 */
export function partMenu(part: { name: string; hidden: boolean }, c: { busy: boolean; actions: PartActions }): ContextMenuData {
  const { actions: a, busy } = c
  const b = new Builder()
  b.item('part.rename', 'Rename…', a.rename, { enabled: () => !busy })
  b.item('part.colour', 'Change colour…', a.colour, { enabled: () => !busy })
  b.item('part.material', 'Material…')
  b.item('part.hide', part.hidden ? 'Show' : 'Hide', () => a.setHidden(!part.hidden), { enabled: () => !busy })
  b.item('part.isolate', 'Isolate', a.isolate)
  b.sep()
  b.item('part.mass', 'Mass properties')
  b.item('part.drawing', 'Create drawing…')
  b.item('part.exportStep', 'Export STEP…')
  b.item('part.exportStl', 'Export STL…')
  b.item('part.exportBrep', 'Export BREP…')
  b.sep()
  b.item('part.delete', 'Delete part…', a.remove)
  return b.done()
}

/** A tab (`_tab_context`): Rename, Duplicate, Close. The client has no closed tabs: Close takes the tab out of the Document, as Delete does. */
export function tabMenu(c: { rename: () => void; close: () => void }): ContextMenuData {
  return new Builder().item('tab.rename', 'Rename…', c.rename).item('tab.duplicate', 'Duplicate').sep().item('tab.close', 'Close', c.close).done()
}

/** The standard views as the desktop's "Views" submenu lists them. */
const viewsItem = (b: Builder, view: (name: (typeof STANDARD_VIEW_ITEMS)[number][0]) => void): void => {
  for (const [name] of STANDARD_VIEW_ITEMS) b.commands[`view.${name}`] = () => view(name)
  b.entries.push({ command: 'view.views', label: 'Views', submenu: true, items: STANDARD_VIEW_ITEMS.map(([name, label]): MenuItem => ({ command: `view.${name}`, label })) })
}

export interface StudioViewActions {
  /** New sketch on the plane or face under the pointer. */
  newSketch?: () => void
  /** Look straight at the plane or face under the pointer. */
  normalTo?: () => void
  setPartsHidden: (ids: readonly string[], hidden: boolean) => void
  isolate: (ids: readonly string[]) => void
  selectPart: (id: string) => void
  /** Open the Delete part feature on the part. */
  deletePart?: (id: string) => void
  editSketch: () => void
  hideSketch: () => void
  hidePlane: () => void
  hideAxis: () => void
  showAll: () => void
  fit: () => void
  view: (name: (typeof STANDARD_VIEW_ITEMS)[number][0]) => void
}

/**
 * The Part Studio's view (`_viewport_context`, idle): what the pointer is over (New sketch and Normal to for a plane or face;
 * the part's items for a face, edge, part or vertex; Edit sketch and Hide sketch for a region; Hide plane; Hide axis),
 * then Zoom to fit, Views, Show all parts and Auto colour all parts. `kind` is what is under the pointer, null over empty space.
 * `parts` are the parts the menu acts on (the part under the pointer, or the selection when it holds it). Mate connector at
 * origin, Select other, Mass properties and Auto colour are the desktop's only; Export DXF for a flat face is not listed (the client cannot tell a face is flat).
 */
export function studioViewMenu(c: { kind: PickKind | null; parts: readonly { id: string; hidden: boolean }[]; anyHidden: boolean; partCount: number; readOnly: boolean; actions: StudioViewActions }): ContextMenuData {
  const { actions: a, kind } = c
  const b = new Builder()
  const edits = (): boolean => !c.readOnly
  if (kind === 'plane' || kind === 'face') {
    b.item('view.newSketch', 'New sketch', a.newSketch, { enabled: edits })
    b.item('view.normalTo', 'Normal to', a.normalTo)
  }
  if (kind === 'face' || kind === 'edge') {
    const ids = c.parts.map((p) => p.id)
    const hide = c.parts.some((p) => !p.hidden)
    const many = ids.length > 1
    b.item('view.hideParts', `${hide ? 'Hide' : 'Show'} ${many ? `${ids.length} parts` : 'part'}`, () => a.setPartsHidden(ids, hide), { enabled: edits })
    if (many) {
      b.item('view.isolate', `Isolate ${ids.length} parts`, () => a.isolate(ids))
      b.item('view.autoColour', `Auto colour ${ids.length} parts`)
    } else {
      b.item('view.isolate', 'Isolate part', () => a.isolate(ids))
      b.item('view.selectPart', 'Select part', () => a.selectPart(ids[0]!))
      b.item('view.mass', 'Mass properties')
      b.item('view.deletePart', 'Delete part…', a.deletePart && (() => a.deletePart!(ids[0]!)), { enabled: edits })
    }
  }
  if (kind === 'region') {
    b.item('view.editSketch', 'Edit sketch', a.editSketch, { enabled: edits })
    b.item('view.hideSketch', 'Hide sketch', a.hideSketch, { enabled: edits })
  }
  if (kind === 'plane') b.item('view.hidePlane', 'Hide plane', a.hidePlane, { enabled: edits })
  if (kind === 'axis') b.item('view.hideAxis', 'Hide axis', a.hideAxis, { enabled: edits })
  b.sep()
  b.item('view.fit', 'Zoom to fit', a.fit)
  viewsItem(b, a.view)
  if (c.anyHidden) b.item('view.showAll', 'Show all parts', a.showAll, { enabled: edits })
  if (c.partCount > 1) b.item('view.autoColourAll', 'Auto colour all parts')
  return b.done()
}

/** An open sketch's view (`_viewport_context`, sketch mode): Normal to sketch, Finish sketch, Cancel sketch. */
export function sketchViewMenu(a: { normalTo: () => void; finish: () => void; cancel: () => void }): ContextMenuData {
  return new Builder().item('sketch.normalTo', 'Normal to sketch', a.normalTo).item('sketch.finish', 'Finish sketch', a.finish).item('sketch.cancel', 'Cancel sketch', a.cancel).done()
}

export interface InstanceActions {
  rename: () => void
  setFixed: (fixed: boolean) => void
  setHidden: (hidden: boolean) => void
  isolate: () => void
  move: () => void
  remove: () => void
}

/**
 * An Assembly instance (`instance_menu`): Rename, Fix or Unfix, Hide or Show, Isolate, Suppress, Change colour, Add mate connector
 * to instance origin (off until I10.4), Move / rotate, Group, Duplicate, Mass properties, Delete. The client wires Rename, Fix or
 * Unfix, Hide or Show, Isolate, Move and Delete. Select other, Use the part's colour, Open Part Studio, Edit in context, the
 * standard part items and the degrees of freedom line are not listed: the client does not know what they depend on.
 */
export function instanceMenu(i: { name: string; hidden: boolean; fixed: boolean }, c: { busy: boolean; actions: InstanceActions }): ContextMenuData {
  const { actions: a, busy } = c
  const b = new Builder()
  b.item('instance.rename', 'Rename…', a.rename, { enabled: () => !busy })
  b.item('instance.fix', i.fixed ? 'Unfix' : 'Fix', () => a.setFixed(!i.fixed), { enabled: () => !busy })
  b.item('instance.hide', i.hidden ? 'Show' : 'Hide', () => a.setHidden(!i.hidden), { enabled: () => !busy })
  b.item('instance.isolate', 'Isolate', a.isolate)
  b.item('instance.suppress', 'Suppress')
  b.item('instance.colour', 'Change colour…')
  b.sep()
  b.item('instance.connector', 'Add mate connector to instance origin', undefined, { note: WAITS_FOR_I10_4 })
  b.item('instance.move', 'Move / rotate…', a.move, { enabled: () => !busy })
  b.item('instance.group', 'Group')
  b.item('instance.duplicate', 'Duplicate')
  b.item('instance.mass', 'Mass properties')
  b.sep()
  b.item('instance.delete', 'Delete', a.remove, { enabled: () => !busy })
  return b.done()
}

/** Mate types that have a value to set or animate (freeshape/assembly.py `MATE_VALUE_DOFS`: every type with a freedom). */
const VALUED_MATES: ReadonlySet<string> = new Set(['revolute', 'slider', 'cylindrical', 'planar', 'pin_slot', 'parallel'])

/**
 * An Assembly mate (`mate_menu`): Edit, Rename, Suppress or Unsuppress, Set value and Animate for a mate with a freedom, Delete, and
 * the mate's error. The client has no mate editing yet: every item is the desktop's only. Apply limit position is not listed (the
 * client does not know a mate's limits).
 */
export function mateMenu(m: { type: string; suppressed: boolean }, error: string | undefined): ContextMenuData {
  const b = new Builder()
  b.item('mate.edit', 'Edit…')
  b.item('mate.rename', 'Rename…')
  b.item('mate.suppress', m.suppressed ? 'Unsuppress' : 'Suppress')
  if (VALUED_MATES.has(m.type)) b.item('mate.value', 'Set value…').item('mate.animate', 'Animate…')
  b.sep()
  b.item('mate.delete', 'Delete')
  if (error !== undefined) b.sep().note('mate.error', `⚠ ${error}`)
  return b.done()
}

/** The Assembly's empty view (`viewport_menu`): Insert parts, Mass properties, Zoom to fit, Views, Show all. */
export function assemblyViewMenu(c: { anyHidden: boolean; readOnly: boolean; insert?: () => void; fit: () => void; view: (name: (typeof STANDARD_VIEW_ITEMS)[number][0]) => void; showAll: () => void }): ContextMenuData {
  const b = new Builder()
  b.item('view.insert', 'Insert parts…', c.insert, { enabled: () => !c.readOnly })
  b.item('view.mass', 'Mass properties')
  b.item('view.fit', 'Zoom to fit', c.fit)
  viewsItem(b, c.view)
  if (c.anyHidden) b.item('view.showAll', 'Show all', c.showAll, { enabled: () => !c.readOnly })
  return b.done()
}
