import type { PickPointer } from '../viewport/pick'
import type { P, ScreenLayout } from './layout'

export type SketchPickKind = 'point' | 'entity' | 'dimension' | 'constraint'

/** What is under a view position in a sketch: a point or an entity (by its id), a dimension, or a constraint glyph. */
export interface SketchHit {
  kind: SketchPickKind
  id: string
}

/** Screen reach in CSS pixels. A point reaches less than a curve does, so the end of a line is a point near it and the line past it. */
export const SKETCH_TOLERANCE: Readonly<Record<PickPointer, { point: number; entity: number; label: number }>> = {
  pen: { point: 10, entity: 10, label: 2 },
  touch: { point: 16, entity: 22, label: 8 },
  mouse: { point: 9, entity: 7, label: 0 },
}

/** Distance from (x, y) to the segment a-b. */
export function segmentDistance(x: number, y: number, a: P, b: P): number {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const l2 = dx * dx + dy * dy
  const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / l2))
  return Math.hypot(x - (a.x + t * dx), y - (a.y + t * dy))
}

const inRect = (x: number, y: number, r: { x: number; y: number; w: number; h: number }, grow: number): boolean =>
  x >= r.x - grow && x <= r.x + r.w + grow && y >= r.y - grow && y <= r.y + r.h + grow

/**
 * What a pointer at (x, y) picks, or null. Points win over everything; then a dimension's label or a constraint's glyph
 * (they sit beside the geometry and are small); then the nearest curve. The sketch's origin is a point like any other.
 * It measures on screen, so it is the same in the 2D and the 3D view.
 */
export function pickSketch(layout: ScreenLayout, x: number, y: number, pointer: PickPointer = 'touch'): SketchHit | null {
  const tol = SKETCH_TOLERANCE[pointer]
  let best: SketchHit | null = null
  let bestD = tol.point
  for (const p of layout.points) {
    const d = Math.hypot(p.x - x, p.y - y)
    if (d <= bestD) {
      bestD = d
      best = { kind: 'point', id: p.id }
    }
  }
  if (best) return best
  for (const l of layout.dims) if (inRect(x, y, l.label, tol.label)) return { kind: 'dimension', id: l.id }
  for (const g of layout.glyphs) if (inRect(x, y, { x: g.x - g.size / 2, y: g.y - g.size / 2, w: g.size, h: g.size }, tol.label)) return { kind: 'constraint', id: g.id }
  bestD = tol.entity
  for (const s of layout.strokes) {
    const pts = s.closed ? [...s.pts, s.pts[0]!] : s.pts
    let d = Infinity
    if (pts.length === 1) d = Math.hypot(pts[0]!.x - x, pts[0]!.y - y)
    for (let i = 1; i < pts.length; i++) d = Math.min(d, segmentDistance(x, y, pts[i - 1]!, pts[i]!))
    if (d <= bestD) {
      bestD = d
      best = { kind: 'entity', id: s.id }
    }
  }
  return best
}
