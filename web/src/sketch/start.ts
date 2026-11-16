import type { SelectionItem } from '../selection/store'
import { fetchRefs } from '../dialog/selectionFields'
import type { SketchStore } from './store'

/** The three default planes the Sketch tool offers when nothing is selected, by the names a sketch's plane param uses. */
export const DEFAULT_PLANE_NAMES = ['Top', 'Front', 'Right'] as const

/**
 * Where the selection says a new sketch goes: a plane (a default plane or one of the studio's) as a sketch's `plane` param
 * stores it, or a planar face (the refs route turns it into the plane reference). Null when the selection is not exactly one
 * plane or face.
 */
export function sketchSite(items: readonly SelectionItem[]): { plane: Record<string, unknown> } | { face: Record<string, unknown> } | null {
  const only = items.length === 1 ? items[0]! : null
  if (only === null) return null
  if (only.kind === 'plane' && only.key !== null) {
    return (DEFAULT_PLANE_NAMES as readonly string[]).includes(only.key) ? { plane: { kind: 'std', name: only.key } } : { plane: { kind: 'plane', fid: only.key } }
  }
  if (only.kind === 'face' && only.part !== null) return { face: { part: only.part, kind: 'face', index: only.index + 1, want: ['plane'] } }
  return null
}

/**
 * The Sketch tool (card I7.13). With a plane or a planar face selected it opens a new sketch there and returns null; with
 * nothing usable selected it returns 'choose' (the caller asks which plane); a face that is not flat, or a refused request,
 * returns the sentence to show.
 */
export async function startSketch(o: { store: SketchStore; document: string; tab: string; items: readonly SelectionItem[] }): Promise<'choose' | string | null> {
  const site = sketchSite(o.items)
  if (site === null) return 'choose'
  if ('plane' in site) {
    o.store.open({ document: o.document, tab: o.tab, plane: site.plane })
    return null
  }
  const reply = await fetchRefs(o.document, o.tab, [site.face])
  const got = reply.items[0]
  if (got === undefined) return 'The server gave no answer for that face.'
  if (!got.ok) return got.reason
  // the refs route's "plane" item for a face is already the whole plane reference ({kind: 'face', face, origin, xdir});
  // wrapping it again made the server read a plane reference as a face reference and refuse the open (F12)
  o.store.open({ document: o.document, tab: o.tab, plane: got.ref as Record<string, unknown> })
  return null
}
