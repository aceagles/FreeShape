import { ORIGIN } from './draw'
import type { SketchHit } from './pick'
import type { SketchData } from './wire'

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
 * What the Construct button sends for a selection, or null when no entity is selected: construction is a flag of an
 * entity. When every selected entity is construction it turns them real, otherwise it makes them all construction, so a
 * mixed selection ends in one state.
 */
export function constructionPlan(sketch: SketchData, selected: readonly SketchHit[]): { ids: string[]; value: boolean } | null {
  const ids = selected.filter((h) => h.kind === 'entity' && h.id in sketch.entities).map((h) => h.id)
  if (ids.length === 0) return null
  return { ids, value: !ids.every((id) => sketch.entities[id]?.construction === true) }
}
