import type { PickFilter, PickHit, PickKind } from '../viewport/pick'
import { DEFAULT_PLANES } from '../viewport/scene'
import type { ElementKind, Part, StudioOutputs } from '../wire'

/**
 * The selection of one Part Studio view: a list of items, the mode (single or Multi), and the item under the pen (the
 * Aim). One store serves the viewport, the selection chip, the Parts list and, later, the dialogs (card I5.7) and the
 * references (card I4.3). Nothing here draws or listens to the screen.
 *
 * An item is identified by its part and Element name, not by its index: indexes can move when the model rebuilds, names
 * follow the element. `reconcile` carries the selection over a rebuild and drops what is gone. An element without a
 * name is identified by its index, which is all there is.
 */
export interface SelectionItem {
  /** Identity: kind, part and Element name (or key, or index when there is no name). Equal ids are the same item. */
  readonly id: string
  readonly kind: PickKind
  /** Id of the part; null for a plane or axis. */
  readonly part: string | null
  /** 0-based index in the part's faces, edges or vertices as of the last outputs; -1 for a part, plane or axis. */
  readonly index: number
  /** The Element name, or null for a part, a plane, an axis, or an element the outputs gave no name. */
  readonly name: string | null
  /** Feature id of a plane or axis (Top, Front, Right for the default planes); null otherwise. */
  readonly key: string | null
  /** Id of the Assembly instance the item is on (card I8.1); absent in a Part Studio. `part` is then the part's hash. */
  readonly instance?: string
  /** Where the pick hit, in the studio's mm (a face, an edge's nearest point, a vertex): a dialog sends it with a face for a Mate connector's frame (card I10.4). Not part of the item's identity. */
  readonly point?: readonly [number, number, number]
}

export const idOfItem = (kind: PickKind, part: string | null, name: string | null, index: number, key: string | null, instance?: string): string => {
  // in an Assembly two instances of one part have the same Element names: the instance tells them apart
  if (kind === 'connector') return `connector|${instance ?? ''}|${key}`
  if (kind === 'canvas') return `canvas|${key}`
  if (instance !== undefined) return `${kind}|${instance}|${name ?? `#${index}`}`
  if (kind === 'part') return `part|${part}`
  if (kind === 'plane' || kind === 'axis') return `${kind}|${key}`
  return `${kind}|${part}|${name ?? `#${index}`}`
}

/** The item a pick stands for. */
export function itemOf(hit: PickHit): SelectionItem {
  const key = 'key' in hit ? hit.key : null
  const instance = 'instance' in hit ? hit.instance : undefined
  const item = { id: idOfItem(hit.kind, hit.part, hit.name, hit.index, key, instance), kind: hit.kind, part: hit.part, index: hit.index, name: hit.name, key, point: hit.point }
  return instance === undefined ? item : { ...item, instance }
}

/** A whole part, as the Parts list selects it. */
export const partItem = (id: string): SelectionItem => ({ id: idOfItem('part', id, null, -1, null), kind: 'part', part: id, index: -1, name: null, key: null })

/** What a reader of the selection sees. A new object whenever the items or the mode change (not when only the Aim does), so `useSyncExternalStore` can compare it. */
export interface SelectionState {
  readonly items: readonly SelectionItem[]
  /** Multi mode is on: the toggle, or a dialog's field that takes several items. */
  readonly multi: boolean
  /** Counts the changes of the items, so a reader can tell that the selection moved since it looked. */
  readonly revision: number
  /** A dialog's selection field is active (card I5.7): picks go to the field, and the chip hides Clear. */
  readonly field: boolean
}

const SINGULAR: Record<PickKind, string> = { face: 'face', edge: 'edge', vertex: 'vertex', part: 'part', plane: 'plane', axis: 'axis', region: 'region', connector: 'mate connector', canvas: 'canvas' }
const PLURAL: Record<PickKind, string> = { face: 'faces', edge: 'edges', vertex: 'vertices', part: 'parts', plane: 'planes', axis: 'axes', region: 'regions', connector: 'mate connectors', canvas: 'canvases' }

/** The chip's text: "Nothing selected", "1 face", "3 edges", "2 faces, 1 edge" (two kinds), "5 items" (three or more kinds). */
export function countText(items: readonly { kind: PickKind }[]): string {
  if (items.length === 0) return 'Nothing selected'
  const counts = new Map<PickKind, number>()
  for (const i of items) counts.set(i.kind, (counts.get(i.kind) ?? 0) + 1)
  if (counts.size > 2) return `${items.length} items`
  return [...counts].map(([k, n]) => `${n} ${n === 1 ? SINGULAR[k] : PLURAL[k]}`).join(', ')
}

type Listener = () => void

export class SelectionStore {
  private items: readonly SelectionItem[] = []
  private aimedItem: SelectionItem | null = null
  private userMulti = false
  private dialogMulti = false
  private revision = 0
  private state: SelectionState = { items: [], multi: false, revision: 0, field: false }
  /** The general selection as it was when a dialog took the view over (card I5.7), or null when no dialog has. */
  private saved: readonly SelectionItem[] | null = null
  private picker: ((item: SelectionItem | null) => void) | null = null
  private readonly listeners = new Set<Listener>()
  private filter: PickFilter | undefined = undefined
  /** The Select menu's kinds (I9.14, Desktop mode), or undefined: no menu filter. A dialog's own `filter` goes first. */
  private userFilter: readonly PickKind[] | undefined = undefined
  private doubleClickHandler: ((item: SelectionItem) => void) | null = null

  /** Called after any change of the items, the mode or the Aim, once per change. Returns the way to stop. */
  readonly subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  /** The items and the mode. The same object until one of them changes. */
  readonly getState = (): SelectionState => this.state

  /** The item the pen (or the mouse) is over, or null. */
  getAimed(): SelectionItem | null {
    return this.aimedItem
  }

  /** Multi mode is on: the Multi toggle is on, or a dialog's field that takes several items is active. */
  get multi(): boolean {
    return this.userMulti || this.dialogMulti
  }

  /** What a pick may land on (a dialog's field asks for faces only, say); undefined: everything the scene has. */
  get pickFilter(): PickFilter | undefined {
    return this.filter ?? this.userFilter
  }

  /**
   * The Select menu's filter (card I9.14; the desktop's `user_filter`): the kinds a click or a box may take, or undefined for
   * the client's own defaults (Tablet mode). A dialog's selection field still decides what it takes (`setPickFilter`).
   * Narrowing it drops the selected items it no longer allows, as the desktop does (not while a dialog has the view, and not when the mode changes).
   */
  setUserFilter(kinds: readonly PickKind[] | undefined): void {
    const same = kinds === this.userFilter || (kinds !== undefined && this.userFilter !== undefined && kinds.length === this.userFilter.length && kinds.every((k, i) => k === this.userFilter![i]))
    if (same) return
    const was = this.userFilter
    this.userFilter = kinds
    // what a tick took away goes from the selection; what the filter never allowed (a part the Parts list selected) stays
    if (kinds !== undefined && was !== undefined && this.saved === null) this.setItems(this.items.filter((i) => kinds.includes(i.kind) || !was.includes(i.kind)), true)
  }

  /** The shell's answer to a mouse double click on an item (Desktop mode, I9.14): open its sketch or its plane's dialog. */
  setDoubleClickHandler(handler: ((item: SelectionItem) => void) | null): void {
    this.doubleClickHandler = handler
  }

  doubleClick(item: SelectionItem): void {
    this.doubleClickHandler?.(item)
  }

  setPickFilter(filter: PickFilter | undefined): void {
    this.filter = filter
  }

  /** The Multi toggle of the chip. */
  setMulti(on: boolean): void {
    if (on === this.userMulti) return
    this.userMulti = on
    this.publish(false)
  }

  /** A dialog's selection field that takes several items is active (card I5.7): Multi is on while it is, whatever the toggle says. */
  setDialogMulti(on: boolean): void {
    if (on === this.dialogMulti) return
    this.dialogMulti = on
    this.publish(false)
  }

  /** Set what is under the pen (Aim). Nothing is published when it is the item that is already there. */
  aim(item: SelectionItem | null): void {
    if ((item?.id ?? null) === (this.aimedItem?.id ?? null)) {
      // the same item under a moved pointer: it keeps the Aim (nothing is published) but takes the new hit point, which a Commit sends
      if (item !== null && item.point !== undefined) this.aimedItem = item
      return
    }
    this.aimedItem = item
    this.publish(false)
  }

  /**
   * Commit on release: act on `item` (the Aimed one), or on empty space when it is null. Single mode: the item replaces
   * the selection, the one selected item is deselected, empty space clears. Multi mode: the item is added or removed,
   * empty space does nothing. The Aim ends with it.
   */
  commit(item: SelectionItem | null): void {
    if (this.saved !== null) {
      // a dialog has the view: the pick goes to its field (or nowhere), never to the general selection
      this.aimedItem = null
      this.picker?.(item)
      this.publish(false)
      return
    }
    const was = this.items
    let next: readonly SelectionItem[] = was
    if (this.multi) {
      if (item) next = was.some((i) => i.id === item.id) ? was.filter((i) => i.id !== item.id) : [...was, item]
    } else if (item === null) next = []
    else next = was.length === 1 && was[0]!.id === item.id ? [] : [item]
    this.setItems(next, true)
  }

  /**
   * A mouse click in Desktop mode (I9.9, the desktop's `click_pick`): an item toggles in the selection and the rest stays;
   * empty space clears it unless `additive` (Shift or Ctrl held). While a dialog has the view the click goes to its field,
   * as `commit` sends it. The Aim ends with it.
   */
  click(item: SelectionItem | null, additive: boolean): void {
    if (this.saved !== null) return this.commit(item)
    const was = this.items
    if (item === null) this.setItems(additive ? was : [], true)
    else this.setItems(was.some((i) => i.id === item.id) ? was.filter((i) => i.id !== item.id) : [...was, item], true)
  }

  /**
   * A selection box was released (I9.9, the desktop's `_box_select`): `found` replaces the selection, or with `additive`
   * is added to it (what is already selected stays selected, in its place).
   */
  addBox(found: readonly SelectionItem[], additive: boolean): void {
    const next = additive ? [...this.items] : []
    const ids = new Set(next.map((i) => i.id))
    for (const it of found) {
      if (ids.has(it.id)) continue
      ids.add(it.id)
      next.push(it)
    }
    this.setItems(next, true)
  }

  /** A feature dialog has the view: picks go to its field (no selection box is drawn then, as on the desktop). */
  get dialogOpen(): boolean {
    return this.saved !== null
  }

  /**
   * A feature dialog opens (card I5.7): the general selection is put aside and the view shows what the dialog says
   * (`set`). While it is open `commit` calls the active field's picker instead of changing the items.
   */
  enterDialog(): void {
    if (this.saved !== null) return
    this.saved = this.items
    this.setItems([], true)
  }

  /** The dialog is over: the general selection is as it was, and the filter, the field's Multi and the picker are gone. */
  leaveDialog(): void {
    if (this.saved === null) return
    const back = this.saved
    this.saved = null
    this.picker = null
    this.filter = undefined
    this.dialogMulti = false
    this.setItems(back, true)
    this.publish(false)
  }

  /** The dialog's active selection field, or null when none is active: what a Commit goes to while a dialog is open. */
  setFieldPicker(picker: ((item: SelectionItem | null) => void) | null): void {
    if (picker === this.picker) return
    this.picker = picker
    this.publish(false)
  }

  /** Replace the selection (the Parts list selects one part). */
  set(items: readonly SelectionItem[]): void {
    this.setItems(items, true)
  }

  clear(): void {
    this.set([])
  }

  /** The items now, to put back with `restore` (the double tap takes back the tap it began with). */
  snapshot(): readonly SelectionItem[] {
    return this.items
  }

  restore(items: readonly SelectionItem[]): void {
    this.set(items)
  }

  /**
   * Carry the selection over new outputs: an item stays when its part and Element name (or plane or axis key) are in
   * them, with its index brought up to date; the others drop out. Call it when a tab's outputs change.
   */
  reconcile(outputs: StudioOutputs): void {
    const parts = new Map(outputs.parts.map((p) => [p.id, p]))
    const names = new Map<string, Map<string, number>>()
    const indexOf = (part: Part, kind: ElementKind, name: string): number | undefined => {
      const k = `${part.id}|${kind}`
      let m = names.get(k)
      if (!m) {
        m = new Map([...part.names[kind]].map(([i, n]) => [n, i] as [string, number]))
        names.set(k, m)
      }
      return m.get(name)
    }
    const count = (part: Part, kind: ElementKind): number => (kind === 'face' ? (part.mesh?.faceRanges.length ?? 0) : kind === 'edge' ? (part.mesh?.edges.length ?? 0) : (part.mesh?.vertices.length ?? 0) / 3)
    const keep: SelectionItem[] = []
    for (const it of this.items) {
      if (it.kind === 'plane') {
        if (it.key !== null && (it.key in outputs.planes || DEFAULT_PLANES.some((p) => p.name === it.key))) keep.push(it)
        continue
      }
      if (it.kind === 'axis') {
        if (it.key !== null && it.key in outputs.axes) keep.push(it)
        continue
      }
      if (it.kind === 'connector') {
        // a connector is the studio's own (`key` is its feature id)
        if (it.key !== null && outputs.connectors.some((c) => c.id === it.key)) keep.push(it)
        continue
      }
      if (it.kind === 'canvas') {
        // a canvas is the studio's own (`key` is its feature id); one the eye hides or that stopped being selectable is not selected
        if (it.key !== null && outputs.canvases.some((c) => c.id === it.key && !c.hidden && c.selectable)) keep.push(it)
        continue
      }
      if (it.kind === 'region') {
        // a region is the sketch's (`part` is the sketch feature's id): found again by its Element name, else by its number
        const sk = outputs.sketches.find((x) => x.id === it.part)
        const at = sk === undefined ? -1 : it.name !== null ? sk.regionNames.indexOf(it.name) : it.index < sk.regions.length ? it.index : -1
        if (at >= 0) keep.push(at === it.index ? it : { ...it, index: at })
        continue
      }
      const part = it.part === null ? undefined : parts.get(it.part)
      if (!part) continue
      if (it.kind === 'part') keep.push(it)
      else if (it.name !== null) {
        const index = indexOf(part, it.kind, it.name)
        if (index !== undefined) keep.push(index === it.index ? it : { ...it, index })
      } else if (it.index < count(part, it.kind)) keep.push(it)
    }
    const same = keep.length === this.items.length && keep.every((k, i) => k === this.items[i])
    if (same) {
      this.aim(null) // an Aimed item's index may have moved with the rebuild
      return
    }
    this.setItems(keep, true)
  }

  /** Set the items (and, when `endAim`, end the Aim). Tells the listeners once, and only if something changed. */
  private setItems(next: readonly SelectionItem[], endAim: boolean): void {
    const itemsChanged = next.length !== this.items.length || next.some((n, i) => n.id !== this.items[i]!.id || n.index !== this.items[i]!.index)
    const aimChanged = endAim && this.aimedItem !== null
    if (!itemsChanged && !aimChanged) return
    if (itemsChanged) this.items = next
    if (aimChanged) this.aimedItem = null
    this.publish(itemsChanged)
  }

  /** Make a new state object when the items or the mode changed, and tell everyone. */
  private publish(itemsChanged: boolean): void {
    const multi = this.multi
    if (itemsChanged) this.revision++
    const field = this.picker !== null
    if (itemsChanged || multi !== this.state.multi || field !== this.state.field) this.state = { items: this.items, multi, revision: this.revision, field }
    for (const l of [...this.listeners]) l()
  }
}
