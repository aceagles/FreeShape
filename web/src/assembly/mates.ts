import { json, seg, tabPath } from '../net/api'
import { idOfItem } from '../selection/store'
import type { SelectionItem } from '../selection/store'
import { PREVIEW_MATE, placeFrame } from '../viewport/connectors'
import { elementName } from '../wire'
import type { AssemblyOutputs, Frame, MateEntry, Vec3 } from '../wire'

/**
 * The mate dialog's side of the server (card I10.6): the routes of freeshape/serve.py "Mate dialogs" (I10.5), what a pick of
 * the view becomes in a request, and what the view draws from a reply. A reply is a preview: nothing changes in the Assembly
 * until Accept. Nothing here holds state or draws.
 */

/** One end of the mate as the server shows it: the instance, what was picked on it and the connector frame as previewed. */
export interface MateSide {
  instance: string
  /** "face", "edge", "connector", "named", ... */
  kind: string
  /** The 1-based element index; null for a connector. */
  index: number | null
  /** The connector's id for a connector pick, else null. */
  connector: string | null
  /** "circular edge", "flat face", ...; null when the server has none. */
  what: string | null
  world: { o: Vec3; z: Vec3; x: Vec3 } | null
}

/** One Mate value of the previewed mate, read-only. */
export interface MateValue {
  dof: string
  label: string
  value: number
  unit: string
}

/** The view of a mate dialog: what every reply of the session carries. */
export interface MateView {
  mate: string
  new: boolean
  type: string
  name: string
  flip: boolean
  offset: number
  angle: number
  a: MateSide | null
  b: MateSide | null
  /** Every instance's 4 x 4 placement as previewed. */
  placements: Record<string, number[][]>
  values: MateValue[]
  /** A refused pick, else the previewed mate's solver error; null for none. */
  error: string | null
  dof: number
}

export interface MateOpened extends MateView {
  dialog: string
  document: string
  tab: string
  revision: number
}

/** A side pick: a face or an edge of an instance (1-based index, the click point for a round face), or one of its own connectors. */
export type SidePick = { instance: string; kind: 'face' | 'edge'; index: number; point?: readonly [number, number, number] } | { instance: string; connector: string }

/** What an update may set (serve.py "Mate dialogs", update). */
export interface MateUpdate {
  type?: string
  name?: string
  a?: SidePick | null
  b?: SidePick | null
  flip?: boolean
  offset?: number
  angle?: number
  turn90?: boolean
}

export type Side = 'a' | 'b'
export const otherSide = (s: Side): Side => (s === 'a' ? 'b' : 'a')

const base = (doc: string, tab: string) => `${tabPath(doc, tab)}/mates/dialog`
const at = (doc: string, tab: string, dialog: string, verb: string) => `${base(doc, tab)}/${seg(dialog)}/${verb}`
const as = (client: string) => ({ 'X-Client-Id': client })

/** Open a dialog for a new mate of `type` (with up to two preselected face or edge picks), or for the stored mate `mate`. */
export const openMateDialog = (doc: string, tab: string, client: string, what: { type: string; picks: readonly SidePick[] } | { mate: string }) =>
  json<MateOpened>('POST', base(doc, tab), { body: 'mate' in what ? { mate: what.mate } : { type: what.type, ...(what.picks.length > 0 ? { picks: what.picks } : {}) }, headers: as(client) })
export const updateMateDialog = (doc: string, tab: string, dialog: string, client: string, changes: MateUpdate) =>
  json<MateOpened>('POST', at(doc, tab, dialog, 'update'), { body: changes, headers: as(client) })
export const acceptMateDialog = (doc: string, tab: string, dialog: string, client: string) =>
  json<{ rebuilt: string[]; revision: number | null; mate: string }>('POST', at(doc, tab, dialog, 'accept'), { body: {}, headers: as(client) })
export const cancelMateDialog = (doc: string, tab: string, dialog: string, client: string) =>
  json<{ cancelled: boolean }>('POST', at(doc, tab, dialog, 'cancel'), { body: {}, headers: as(client) })

/** The kinds a mate field takes in the view: faces and edges of instances, and their mate connectors (card I10.3). */
export const MATE_PICK_KINDS = ['face', 'edge', 'connector'] as const

/** An Assembly pick as a side pick, or null for what a mate cannot use (a whole instance, a vertex). `index` of an item is 0-based. */
export function sidePick(item: SelectionItem): SidePick | null {
  if (item.instance === undefined) return null
  if (item.kind === 'connector') return item.key === null ? null : { instance: item.instance, connector: item.key }
  if (item.kind !== 'face' && item.kind !== 'edge') return null
  if (item.index < 0) return null
  const pick: SidePick = { instance: item.instance, kind: item.kind, index: item.index + 1 }
  // a round face is picked at its end nearest the click
  return item.kind === 'face' && item.point !== undefined ? { ...pick, point: item.point } : pick
}

type Rows4 = readonly (readonly number[])[]
const apply = (T: Rows4, p: Vec3): Vec3 => [0, 1, 2].map((r) => T[r]![0]! * p[0] + T[r]![1]! * p[1] + T[r]![2]! * p[2] + T[r]![3]!) as unknown as Vec3
/** The inverse of a rigid placement (a rotation and a shift). */
function inverse(T: Rows4): number[][] {
  const R = [0, 1, 2].map((r) => [0, 1, 2].map((c) => T[c]![r]!))
  const t = [0, 1, 2].map((r) => -(R[r]![0]! * T[0]![3]! + R[r]![1]! * T[1]![3]! + R[r]![2]! * T[2]![3]!))
  return [...R.map((row, r) => [...row, t[r]!]), [0, 0, 0, 1]]
}

/**
 * A pick as the session takes it. The view draws the Assembly as previewed, and the session picks on the committed one, so a
 * click point on an instance the preview has moved is carried back to where that instance is committed.
 */
export function pickOf(item: SelectionItem, outputs: AssemblyOutputs | null, placements: Readonly<Record<string, number[][]>> | null): SidePick | null {
  const pick = sidePick(item)
  if (pick === null || !('point' in pick) || pick.point === undefined || outputs === null || placements === null) return pick
  const committed = outputs.instances.find((i) => i.id === pick.instance)?.T
  const previewed = placements[pick.instance]
  if (committed === undefined || previewed === undefined) return pick
  const back = apply(committed, apply(inverse(previewed), [...pick.point] as unknown as Vec3))
  return { ...pick, point: back }
}

/**
 * The preselection of a new mate: the selection's faces and edges on instances, the first two on different instances. Only
 * those go in the open request (it takes no connector); when a connector is among them none goes there, and `later` holds the
 * picks for the dialog to send as an update once it is open.
 */
export function preselection(items: readonly SelectionItem[]): { picks: SidePick[]; later: SidePick[] } {
  const usable = items.flatMap((i) => {
    const p = sidePick(i)
    return p === null ? [] : [p]
  })
  const chosen: SidePick[] = []
  for (const p of usable) {
    if (chosen.length === 2) break
    if (chosen.length === 1 && chosen[0]!.instance === p.instance) continue
    chosen.push(p)
  }
  if (chosen.every((p) => 'kind' in p)) return { picks: chosen, later: [] }
  return { picks: [], later: chosen }
}

/** The text of a field: "Base plate <1> - circular edge", or the connector's id. */
export function sideText(side: MateSide | null, instanceName: (id: string) => string): string | null {
  if (side === null) return null
  const name = instanceName(side.instance)
  if (side.kind === 'face' || side.kind === 'edge') return `${name} — ${side.what ?? side.kind}`
  return `${name} — ${side.what ?? (side.connector !== null ? `connector ${side.connector}` : side.kind)}`
}

/** The view's selection for a side: the face or edge picked, so it is drawn as selected. Connectors show as their triad. */
export function sideItem(side: MateSide | null, outputs: AssemblyOutputs | null): SelectionItem | null {
  if (side === null || side.index === null || (side.kind !== 'face' && side.kind !== 'edge') || outputs === null) return null
  const hash = outputs.instances.find((i) => i.id === side.instance)?.part ?? null
  if (hash === null) return null
  const part = outputs.parts[hash]
  if (part === undefined) return null
  const index = side.index - 1
  const name = elementName(part, side.kind, index) ?? null
  return { id: idOfItem(side.kind, hash, name, index, null, side.instance), kind: side.kind, part: hash, index, name, key: null, instance: side.instance }
}

const frameOf = (w: MateSide['world']): Frame | null => (w === null ? null : { o: w.o, z: w.z, x: w.x })

/**
 * The Assembly as the dialog previews it: each instance where the session put it, its connectors with it, and the mate being
 * made or edited drawn by its two ends (PREVIEW_MATE; the stored mate being edited is left out, its ends are the preview's).
 */
export function previewOutputs(outputs: AssemblyOutputs, view: MateView): AssemblyOutputs {
  const instances = outputs.instances.map((i) => {
    const T = view.placements[i.id]
    return T === undefined ? i : { ...i, T, connectors: i.connectors.map((c) => ({ ...c, world: placeFrame(c.frame, T) })) }
  })
  const ends: MateEntry = {
    id: PREVIEW_MATE,
    name: view.name,
    type: view.type,
    suppressed: false,
    a: view.a?.instance ?? '',
    b: view.b?.instance ?? '',
    ends: [frameOf(view.a?.world ?? null), frameOf(view.b?.world ?? null)],
  }
  const others = outputs.mates.filter((m) => m.id !== view.mate)
  return { ...outputs, instances, mates: [...others, ends], dof: view.dof }
}

/** Which rows a type shows (the desktop's `set_type`): Angle and Rotate 90 for fastened and slider; Offset for five types; Flip for all but ball. */
export const hasAngle = (type: string): boolean => type === 'fastened' || type === 'slider'
export const hasOffset = (type: string): boolean => ['fastened', 'revolute', 'planar', 'pin_slot', 'ball'].includes(type)
export const hasFlip = (type: string): boolean => type !== 'ball'

/** The server's words when an Assembly has fewer than two parts; the mate tools say it as the reason they are off. */
export const NEED_TWO_PARTS = 'Insert at least two parts before adding a mate'
