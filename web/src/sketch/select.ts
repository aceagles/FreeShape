import type { BoxDrag } from '../input/pointer'
import { isCrossing, normRect, polylineSelected } from '../viewport/boxSelect'
import { ORIGIN } from './draw'
import type { ScreenLayout } from './layout'
import type { SketchHit } from './pick'
import type { SketchData } from './wire'

const same = (a: SketchHit, b: SketchHit): boolean => a.kind === b.kind && a.id === b.id

/** A mouse click in Desktop mode (I9.9): the item toggles and the rest stays; empty space clears unless `additive` (Shift or Ctrl). */
export function clickSelection(selected: readonly SketchHit[], hit: SketchHit | null, additive: boolean): readonly SketchHit[] {
  if (hit === null) return additive ? selected : []
  return selected.some((s) => same(s, hit)) ? selected.filter((s) => !same(s, hit)) : [...selected, hit]
}

/** A released box: `found` replaces the selection, or with `additive` is added to it. */
export function boxSelection(selected: readonly SketchHit[], found: readonly SketchHit[], additive: boolean): readonly SketchHit[] {
  const next = additive ? [...selected] : []
  for (const f of found) if (!next.some((s) => same(s, f))) next.push(f)
  return next
}

/**
 * The sketch entities a box takes (I9.9, the desktop's sketch branch of `_items_in_rect`): an entity whose drawn strokes
 * lie wholly inside the box, or (dragged right to left) any of whose strokes touches it. Points are not taken, as on the
 * desktop. Worked out from the strokes on screen; no server call.
 */
export function sketchBoxHits(layout: ScreenLayout, box: BoxDrag): SketchHit[] {
  const r = normRect(box)
  const crossing = isCrossing(box)
  const byId = new Map<string, boolean[]>()
  for (const st of layout.strokes) {
    if (st.pts.length < 2) continue
    const pts = st.closed ? [...st.pts, st.pts[0]!] : st.pts
    const S = new Float64Array(pts.length * 2)
    pts.forEach((p, i) => {
      S[2 * i] = p.x
      S[2 * i + 1] = p.y
    })
    const list = byId.get(st.id) ?? []
    list.push(polylineSelected(S, r, crossing))
    byId.set(st.id, list)
  }
  const out: SketchHit[] = []
  for (const [id, hits] of byId) if (crossing ? hits.some(Boolean) : hits.every(Boolean)) out.push({ kind: 'entity', id })
  return out
}

/** The entity types the server drags (freeshape/sketch_session.py drag_begin): a line or arc by its points, a circle by its radius. */
const MOVABLE_ENTITIES: ReadonlySet<string> = new Set(['line', 'circle', 'arc'])

/** What a drag moves: a point or an entity (the server drags it), or a dimension's label (its position only, no solve). */
export type DragTarget = { kind: 'point' | 'entity' | 'dimension'; id: string }

/** Whether an item of the sketch can be dragged: any point but the origin, a line, circle or arc, a dimension's label. Constraint glyphs stay put. */
export function isMovable(sketch: SketchData, hit: SketchHit | null): hit is SketchHit & DragTarget {
  if (hit === null) return false
  if (hit.kind === 'point') return hit.id !== ORIGIN && hit.id in sketch.points
  if (hit.kind === 'entity') return MOVABLE_ENTITIES.has(sketch.entities[hit.id]?.type ?? '')
  if (hit.kind === 'dimension') return sketch.constraints.some((c) => c.id === hit.id)
  return false
}

/** The ids `sketch.delete` takes for a selection: points, entities and constraints (a dimension is a constraint). The origin cannot be deleted. */
export function deletionIds(selected: readonly SketchHit[]): string[] {
  return selected.filter((h) => !(h.kind === 'point' && h.id === ORIGIN)).map((h) => h.id)
}

/**
 * What the Construct button sends for a selection, or null when no line, curve or text is selected (the button then sets the construction mode): construction is a flag of an
 * entity. When every selected entity is construction it turns them real, otherwise it makes them all construction, so a
 * mixed selection ends in one state.
 */
export function constructionPlan(sketch: SketchData, selected: readonly SketchHit[]): { ids: string[]; value: boolean } | null {
  // a point entity is always construction and the server refuses it, so it is left out
  const ids = selected.filter((h) => h.kind === 'entity' && h.id in sketch.entities && sketch.entities[h.id]!.type !== 'point').map((h) => h.id)
  if (ids.length === 0) return null
  return { ids, value: !ids.every((id) => sketch.entities[id]?.construction === true) }
}
