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
}

export const idOfItem = (kind: PickKind, part: string | null, name: string | null, index: number, key: string | null, instance?: string): string => {
  // in an Assembly two instances of one part have the same Element names: the instance tells them apart
  if (instance !== undefined) return `${kind}|${instance}|${name ?? `#${index}`}`
  if (kind === 'part') return `part|${part}`
  if (kind === 'plane' || kind === 'axis') return `${kind}|${key}`
  return `${kind}|${part}|${name ?? `#${index}`}`
}

/** The item a pick stands for. */
export function itemOf(hit: PickHit): SelectionItem {
  const key = 'key' in hit ? hit.key : null
  const instance = 'instance' in hit ? hit.instance : undefined
  const item = { id: idOfItem(hit.kind, hit.part, hit.name, hit.index, key, instance), kind: hit.kind, part: hit.part, index: hit.index, name: hit.name, key }
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
}

const SINGULAR: Record<PickKind, string> = { face: 'face', edge: 'edge', vertex: 'vertex', part: 'part', plane: 'plane', axis: 'axis' }
const PLURAL: Record<PickKind, string> = { face: 'faces', edge: 'edges', vertex: 'vertices', part: 'parts', plane: 'planes', axis: 'axes' }

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
  private state: SelectionState = { items: [], multi: false, revision: 0 }
  private readonly listeners = new Set<Listener>()
  private filter: PickFilter | undefined = undefined

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
    return this.filter
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
    if ((item?.id ?? null) === (this.aimedItem?.id ?? null)) return
    this.aimedItem = item
    this.publish(false)
  }

  /**
   * Commit on release: act on `item` (the Aimed one), or on empty space when it is null. Single mode: the item replaces
   * the selection, the one selected item is deselected, empty space clears. Multi mode: the item is added or removed,
   * empty space does nothing. The Aim ends with it.
   */
  commit(item: SelectionItem | null): void {
    const was = this.items
    let next: readonly SelectionItem[] = was
    if (this.multi) {
      if (item) next = was.some((i) => i.id === item.id) ? was.filter((i) => i.id !== item.id) : [...was, item]
    } else if (item === null) next = []
    else next = was.length === 1 && was[0]!.id === item.id ? [] : [item]
    this.setItems(next, true)
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
    if (itemsChanged || multi !== this.state.multi) this.state = { items: this.items, multi, revision: this.revision }
    for (const l of [...this.listeners]) l()
  }
}
