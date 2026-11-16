import { idOfItem } from '../selection/store'
import type { SelectionItem } from '../selection/store'
import type { PickKind } from '../viewport/pick'
import type { ElementKind, StudioOutputs } from '../wire'
import { json, tabPath } from '../net/api'
import type { SelItem } from './dialogApi'

/**
 * Selection fields of a feature dialog (card I5.7): what a field's `want` lets the view pick, the pick sent to the refs
 * route, how the answer joins the field, how an item reads in a chip, and which drawn elements an item stands for.
 * Nothing here draws or holds state; `useSelectionFields` wires it to the dialog and the selection store.
 */

/**
 * The kinds the view may pick for a field that wants `want` (the route's own strings). A face also fills a plane or an
 * axis field (the route turns it into one). What the refs route has no pick for (a sketch region, a datum plane or
 * axis, a connector) is left out: a field that wants only those cannot be filled from the view, and nothing is pickable.
 */
const KINDS_OF_WANT: Record<string, readonly PickKind[]> = {
  face: ['face'],
  edge: ['edge'],
  vertex: ['vertex'],
  plane: ['face'],
  axis: ['face'],
  part: ['part'],
  path: ['edge'],
  element: ['face', 'edge'],
}

export function pickKinds(want: readonly string[] | undefined): PickKind[] {
  const out: PickKind[] = []
  for (const w of want ?? []) for (const k of KINDS_OF_WANT[w] ?? []) if (!out.includes(k)) out.push(k)
  return out
}

/** A pick for the refs route, from what the view picked: the index is 1-based there. `want` is the field's own list. */
export function refPick(item: SelectionItem, want: readonly string[]): Record<string, unknown> | null {
  if (item.part === null) return null
  if (item.kind === 'part') return { part: item.part, kind: 'part', want }
  if (item.kind !== 'face' && item.kind !== 'edge' && item.kind !== 'vertex') return null
  return { part: item.part, kind: item.kind, index: item.index + 1, want }
}

export const NOT_IN_MODEL = 'That element is not in the model this feature is built on, so it cannot be picked here.'
export const BASE_LOADING = 'The model this feature is built on has not arrived yet, so nothing can be picked for it. Pick again in a moment.'

/**
 * The 0-based index of the picked element in the dialog's base model (card F3: the model before the edited feature, or
 * at the rollback bar for a new one), which is what the refs route counts in when it is sent the dialog id. The view
 * draws the base while it has it, so a pick there keeps its index. A pick made on anything else (the committed model,
 * before the base has arrived) is found in the base by its Element name: null when the base has no such element (the
 * feature itself made it), and `undefined` when there is no base yet. An element without a name keeps its index only on
 * the base itself: elsewhere there is nothing to go by.
 */
export function baseIndex(item: SelectionItem, drawn: StudioOutputs | null, base: StudioOutputs | null): number | null | undefined {
  if (base === null) return undefined
  if (drawn === base || item.kind === 'part') return item.index
  if (item.name === null || item.part === null || (item.kind !== 'face' && item.kind !== 'edge' && item.kind !== 'vertex')) return null
  const part = base.parts.find((p) => p.id === item.part)
  if (!part) return null
  for (const [i, n] of part.names[item.kind]) if (n === item.name) return i
  return null
}

/**
 * An engine message as the dialog's banner shows it. The engine puts the lost element's Element name on a line of its own
 * ("\nElement: l12;SKT;..."): an encoded id for bug reports, which the desktop shows and a person cannot read. The banner
 * drops that line (the first line already says which kind of element on which part); the engine's text is unchanged.
 */
export function readableError(text: string): string
export function readableError(text: string | null): string | null
export function readableError(text: string | null): string | null {
  if (text === null) return null
  return text
    .split('\n')
    .filter((line) => !/^\s*Element:/.test(line))
    .join('\n')
    .trim()
}

export interface RefsReply {
  items: Array<{ ok: true; kind: string; ref: unknown; name: string | null } | { ok: false; code: string; reason: string }>
}

/**
 * Picks to stored references. With `dialog` (card F3) the server resolves them against that dialog's base model, which
 * is what the view draws while the dialog is open; the dialog belongs to `client`, sent as X-Client-Id.
 */
export const fetchRefs = (doc: string, tab: string, picks: Record<string, unknown>[], dialog: { id: string; client: string } | null = null) =>
  json<RefsReply>('POST', `${tabPath(doc, tab)}/refs`, dialog === null ? { body: { picks } } : { body: { picks, dialog: dialog.id }, headers: { 'X-Client-Id': dialog.client } })

/**
 * A refs answer as the items a field holds. A face picked for an edge field comes back as kind "edges" with a list of
 * edge refs: each edge becomes its own item (the server stores one ref per edge, and the person removes them one by one).
 */
export function itemsOfAnswer(kind: string, ref: unknown, name: string | null): SelItem[] {
  if (kind === 'edges' && Array.isArray(ref)) return ref.map((r) => ({ ok: true, kind: 'edge', ref: r, name: nameOfRef(r) }))
  return [{ ok: true, kind, ref, name }]
}

const asRecord = (x: unknown): Record<string, unknown> | null => (typeof x === 'object' && x !== null && !Array.isArray(x) ? (x as Record<string, unknown>) : null)

function nameOfRef(ref: unknown): string | null {
  const n = asRecord(ref)?.['name']
  return typeof n === 'string' && n !== '' ? n : null
}

const sameItem = (a: SelItem, b: SelItem): boolean => a.kind === b.kind && JSON.stringify(a.ref) === JSON.stringify(b.ref)

/** MAX_DIALOG_ITEMS of the server. */
export const MAX_FIELD_ITEMS = 200

/**
 * The field's items after a pick. A field of one item takes the new item in place of the old; a field of several adds
 * it, and a pick of an item already there takes it out again (as the desktop and the Multi selection do).
 */
export function afterPick(items: readonly SelItem[], added: readonly SelItem[], multi: boolean): SelItem[] {
  if (!multi) return added.slice(0, 1)
  let next = [...items]
  for (const a of added) {
    const at = next.findIndex((i) => sameItem(i, a))
    if (at >= 0) next.splice(at, 1)
    else next.push(a)
  }
  if (next.length > MAX_FIELD_ITEMS) next = next.slice(0, MAX_FIELD_ITEMS)
  return next
}

/** The item as it is sent back to the server: only what the update route reads. */
export const asSent = (i: SelItem): SelItem => ({ ok: true, kind: i.kind ?? 'item', ref: i.ref, name: i.name ?? null })

const KIND_WORD: Record<string, string> = { face: 'Face', edge: 'Edge', vertex: 'Vertex', plane: 'Plane', axis: 'Axis', part: 'Part', path: 'Path', region: 'Region', sketch: 'Sketch curve', sketch_line: 'Sketch line', sketch_point: 'Sketch point', connector: 'Connector' }

/** Index of the region of `sketch` that the reference's centroid and area point at (0-based), or -1. */
function regionIndex(ref: Record<string, unknown>, outputs: StudioOutputs, sketchId: string): number {
  const sketch = outputs.sketches.find((s) => s.id === sketchId)
  if (!sketch) return -1
  const name = ref['name']
  if (typeof name === 'string') {
    const byName = sketch.regionNames.indexOf(name)
    if (byName >= 0) return byName
  }
  const c = ref['c']
  const area = ref['area']
  if (!Array.isArray(c) || c.length !== 3 || typeof area !== 'number') return -1
  let best = -1
  let bestScore = Infinity
  sketch.regions.forEach((r, i) => {
    const score = Math.hypot(r.c[0] - Number(c[0]), r.c[1] - Number(c[1]), r.c[2] - Number(c[2])) + Math.abs(r.area - area) / Math.max(area, r.area, 1e-9)
    if (score < bestScore) {
      bestScore = score
      best = i
    }
  })
  return bestScore < 1e-3 * Math.max(1, Math.sqrt(area)) + 1e-3 ? best : -1
}

/**
 * What a chip says. An element with an Element name shows the name. A sketch region (which has no name until the sketch
 * has named it) reads "Region 2 of Sketch 1": the sketch's name from the feature list, the number from matching the
 * reference against the sketch's regions. A plane or axis by feature shows the feature's name. Anything else is its kind.
 */
export function itemLabel(item: SelItem, outputs: StudioOutputs | null, fallback: StudioOutputs | null = null): string {
  const ref = asRecord(item.ref)
  const featureName = (id: unknown): string | null => (typeof id === 'string' ? (outputs?.features.find((f) => f.id === id)?.name ?? null) : null)
  if (item.kind === 'region' && ref) {
    const id = typeof ref['sketch'] === 'string' ? ref['sketch'] : ''
    const sketch = featureName(id) ?? (id === '' ? null : id)
    const n = outputs ? regionIndex(ref, outputs, id) : -1
    return `Region${n >= 0 ? ` ${n + 1}` : ''}${sketch ? ` of ${sketch}` : ''}`
  }
  if ((item.kind === 'plane' || item.kind === 'axis') && ref) {
    if (ref['kind'] === 'std' && typeof ref['name'] === 'string') return ref['name']
    const f = featureName(ref['fid'])
    if (f) return f
  }
  const word = KIND_WORD[item.kind ?? ''] ?? item.kind ?? 'Item'
  // an element reads "Edge 7 of Part 1": its 1-based index in the drawn part and the part's name. The Element name is an
  // encoded id for matching and is never shown; an item the drawn model has no such name for is just its kind
  // (the fallback model is tried when the drawn one lacks the element)
  for (const model of [outputs, fallback]) {
    if (!item.name || !model || (item.kind !== 'face' && item.kind !== 'edge' && item.kind !== 'vertex')) continue
    const owner = ref?.['part']
    const want = typeof owner === 'string' ? owner : null
    for (const [id, kinds] of nameIndex(model)) {
      if (want !== null && id !== want) continue
      const i = kinds.get(item.kind)?.get(item.name)
      if (i === undefined) continue
      return `${word} ${i + 1} of ${model.parts.find((p) => p.id === id)?.name ?? id}`
    }
  }
  return word
}

/** The count text of a field, as the mock's "1 item". */
export const countOf = (n: number): string => (n === 0 ? 'Nothing selected' : n === 1 ? '1 item' : `${n} items`)

type NameIndex = Map<string, Map<ElementKind, Map<string, number>>>

/** Part id -> kind -> Element name -> 0-based index, for the parts of these outputs. */
export function nameIndex(outputs: StudioOutputs): NameIndex {
  const out: NameIndex = new Map()
  for (const p of outputs.parts) {
    const kinds = new Map<ElementKind, Map<string, number>>()
    for (const k of ['face', 'edge', 'vertex'] as const) kinds.set(k, new Map([...p.names[k]].map(([i, n]) => [n, i] as [string, number])))
    out.set(p.id, kinds)
  }
  return out
}

/**
 * The drawn elements a field's items stand for, to highlight. An item is a reference, not a pick: it is matched to the
 * outputs by Element name within its part (the part is `ref.part`; without one, the first part that has the name). A plane
 * or axis taken from a face highlights that face. An item without a name (a legacy reference, a face's edges, a region, a
 * part) is listed in the chips and not highlighted: nothing in the outputs says which element it is.
 */
export function highlightItems(items: readonly SelItem[], outputs: StudioOutputs | null): SelectionItem[] {
  if (outputs === null) return []
  const index = nameIndex(outputs)
  const out: SelectionItem[] = []
  for (const it of items) {
    const ref = asRecord(it.ref)
    const sub = asRecord(ref?.['face']) ?? asRecord(ref?.['edge'])
    const name = it.name ?? (it.kind === 'plane' || it.kind === 'axis' ? nameOfRef(sub) : null)
    if (!name) continue
    const kind: ElementKind = it.kind === 'edge' ? 'edge' : it.kind === 'vertex' ? 'vertex' : 'face'
    if (it.kind !== 'face' && it.kind !== 'edge' && it.kind !== 'vertex' && it.kind !== 'plane' && it.kind !== 'axis') continue
    const owner = ref?.['part'] ?? sub?.['part']
    const want = typeof owner === 'string' ? owner : null
    for (const [part, kinds] of index) {
      if (want !== null && part !== want) continue
      const i = kinds.get(kind)?.get(name)
      if (i === undefined) continue
      if (!out.some((o) => o.id === idOfItem(kind, part, name, i, null))) out.push({ id: idOfItem(kind, part, name, i, null), kind, part, index: i, name, key: null })
      break
    }
  }
  return out
}
