import type { PickPointer } from '../../viewport/pick'
import type { P, ScreenLayout } from '../layout'
import { segmentDistance, SKETCH_TOLERANCE } from '../pick'
import type { FrameEdge, SketchData, Uv } from '../wire'

/** What an Aim snapped to: an existing point, the middle of a line (by line id), a line or circle (by entity id), or a model edge (no id to tie to). */
export type Snap = { kind: 'point'; id: string } | { kind: 'mid'; id: string } | { kind: 'on'; id: string } | { kind: 'edge' } | null

/** Where the pen's Aim lands: the sketch point, where that is on screen, what it snapped to and the alignment it found. */
export interface Aim {
  uv: Uv
  at: P
  snap: Snap
  /** 'H' or 'V': the line being drawn is horizontal or vertical (within the alignment cone). */
  infer: 'H' | 'V' | null
  /** The start of that alignment, where the dotted guide begins. */
  guide: Uv | null
}

export interface SnapContext {
  sketch: SketchData
  layout: ScreenLayout
  edges: readonly FrameEdge[]
  toUv(x: number, y: number): Uv | null
  toView(uv: Uv): P
  pointer: PickPointer
}

/** An alignment needs the pen at least this far from its start (screen px), and within this sine of the axis (3.4 degrees): the desktop's values (Sketcher._axis_hit). */
const AXIS_MIN_PX = 12
const AXIS_SINE = 0.06
/** How far (screen px) a level or plumb alignment may be from the curve it crosses and still land on it (Sketcher.ALIGNED_ON_PX). */
const ALIGNED_ON_PX = 12

const sub = (a: Uv, b: Uv): Uv => [a[0] - b[0], a[1] - b[1]]

/** The nearest point of the sketch within reach of (x, y) on screen, or null. */
function pointHit(layout: ScreenLayout, x: number, y: number, reach: number): { id: string; d: number } | null {
  let best: { id: string; d: number } | null = null
  for (const p of layout.points) {
    const d = Math.hypot(p.x - x, p.y - y)
    if (d <= reach && (!best || d < best.d)) best = { id: p.id, d }
  }
  return best
}

/**
 * The middle of the line whose middle is nearest (x, y) on screen within reach, or null (Sketcher._hit_midpoint). Every line offers its
 * middle: the line being drawn is not an entity yet, and the desktop's callers pass no exclusions.
 */
function midpointHit(ctx: SnapContext, x: number, y: number, reach: number): { id: string; uv: Uv } | null {
  let best: { id: string; uv: Uv } | null = null
  let bestD = reach
  for (const [id, e] of Object.entries(ctx.sketch.entities)) {
    if (e.type !== 'line' || !e.p1 || !e.p2) continue
    const a = ctx.sketch.points[e.p1]
    const b = ctx.sketch.points[e.p2]
    if (!a || !b) continue
    const uv: Uv = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
    const s = ctx.toView(uv)
    const d = Math.hypot(s.x - x, s.y - y)
    if (d < bestD) {
      bestD = d
      best = { id, uv }
    }
  }
  return best
}

/** The nearest curve (not a point entity) within reach of (x, y) on screen, or null. */
function entityHit(layout: ScreenLayout, x: number, y: number, reach: number): string | null {
  let best: string | null = null
  let bestD = reach
  for (const s of layout.strokes) {
    if (s.kind === 'point') continue
    const pts = s.closed ? [...s.pts, s.pts[0]!] : s.pts
    let d = Infinity
    for (let i = 1; i < pts.length; i++) d = Math.min(d, segmentDistance(x, y, pts[i - 1]!, pts[i]!))
    if (d <= bestD) {
      bestD = d
      best = s.id
    }
  }
  return best
}

/** The point of a line, circle or arc nearest to `uv` (Sketcher._project_on: an arc is projected on its whole circle). */
export function projectOn(sketch: SketchData, id: string, uv: Uv): Uv {
  const e = sketch.entities[id]
  if (!e) return uv
  if (e.type === 'line' && e.p1 && e.p2) {
    const a = sketch.points[e.p1]
    const b = sketch.points[e.p2]
    if (!a || !b) return uv
    const d = sub(b, a)
    const t = Math.max(0, Math.min(1, ((uv[0] - a[0]) * d[0] + (uv[1] - a[1]) * d[1]) / Math.max(d[0] * d[0] + d[1] * d[1], 1e-12)))
    return [a[0] + t * d[0], a[1] + t * d[1]]
  }
  if ((e.type === 'circle' || e.type === 'arc') && e.c) {
    const c = sketch.points[e.c]
    if (!c) return uv
    const r = radiusOf(sketch, id)
    const v = sub(uv, c)
    const n = Math.hypot(v[0], v[1])
    return n > 1e-12 ? [c[0] + (v[0] / n) * r, c[1] + (v[1] / n) * r] : [c[0] + r, c[1]]
  }
  return uv
}

function radiusOf(sketch: SketchData, id: string): number {
  const e = sketch.entities[id]
  if (!e) return 0
  if (e.type === 'circle') return e.r ?? 0
  if (e.type === 'arc' && e.c && e.p1) {
    const c = sketch.points[e.c]
    const p = sketch.points[e.p1]
    return c && p ? Math.hypot(p[0] - c[0], p[1] - c[1]) : 0
  }
  return 0
}

/** 'H' or 'V' when (x, y) is on screen nearly level or plumb (along the sketch's x or y axis) from `src` (Sketcher._axis_hit). */
function axisHit(ctx: SnapContext, src: Uv, x: number, y: number): 'H' | 'V' | null {
  const sa = ctx.toView(src)
  const m = { x: x - sa.x, y: y - sa.y }
  const nm = Math.hypot(m.x, m.y)
  if (nm <= AXIS_MIN_PX) return null
  const dir = (du: number, dv: number): P => {
    const q = ctx.toView([src[0] + du, src[1] + dv])
    const v = { x: q.x - sa.x, y: q.y - sa.y }
    const l = Math.hypot(v.x, v.y) || 1
    return { x: v.x / l, y: v.y / l }
  }
  const cross = (a: P, b: P): number => a.x * b.y - a.y * b.x
  const u = dir(1, 0)
  const v = dir(0, 1)
  const mn = { x: m.x / nm, y: m.y / nm }
  if (Math.abs(cross(u, mn)) < AXIS_SINE) return 'H'
  if (Math.abs(cross(v, mn)) < AXIS_SINE) return 'V'
  return null
}

/** The point of a curve that keeps a level or plumb alignment (`uv` is already aligned), within ALIGNED_ON_PX of the pen, or null (Sketcher._aligned_on). */
function alignedOn(ctx: SnapContext, id: string, uv: Uv, axis: 'H' | 'V', x: number, y: number): Uv | null {
  const e = ctx.sketch.entities[id]
  if (!e) return null
  const j: 0 | 1 = axis === 'V' ? 0 : 1 // the coordinate the alignment holds: u for plumb, v for level
  const k: 0 | 1 = j === 0 ? 1 : 0
  const cands: Uv[] = []
  if (e.type === 'line' && e.p1 && e.p2) {
    const a = ctx.sketch.points[e.p1]
    const b = ctx.sketch.points[e.p2]
    if (a && b) {
      const d = sub(b, a)
      if (Math.abs(d[j]) > 1e-12) {
        const t = (uv[j] - a[j]) / d[j]
        if (t >= -1e-9 && t <= 1 + 1e-9) cands.push([a[0] + t * d[0], a[1] + t * d[1]])
      }
    }
  } else if ((e.type === 'circle' || e.type === 'arc') && e.c) {
    const c = ctx.sketch.points[e.c]
    const r = radiusOf(ctx.sketch, id)
    if (c) {
      const off = uv[j] - c[j]
      if (Math.abs(off) <= r) {
        for (const sgn of [1, -1]) {
          const q: [number, number] = [uv[0], uv[1]]
          q[k] = c[k] + sgn * Math.sqrt(r * r - off * off)
          cands.push(q)
        }
      }
    }
  }
  let best: Uv | null = null
  let bestD = ALIGNED_ON_PX
  for (const q of cands) {
    const s = ctx.toView(q)
    const d = Math.hypot(s.x - x, s.y - y)
    if (d < bestD) {
      bestD = d
      best = q
    }
  }
  return best
}

/** The nearest model edge within reach of (x, y) on screen, and the point of it nearest the pen (sketch coordinates). */
function edgeHit(ctx: SnapContext, x: number, y: number, reach: number): Uv | null {
  let best: Uv | null = null
  let bestD = reach
  for (const e of ctx.edges) {
    for (let i = 1; i < e.points.length; i++) {
      const a = ctx.toView(e.points[i - 1]!)
      const b = ctx.toView(e.points[i]!)
      const d = segmentDistance(x, y, a, b)
      if (d > bestD) continue
      const dx = b.x - a.x
      const dy = b.y - a.y
      const l2 = dx * dx + dy * dy
      const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / l2))
      const p0 = e.points[i - 1]!
      const p1 = e.points[i]!
      bestD = d
      best = [p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1])]
    }
  }
  return best
}

/**
 * Where an Aim at (x, y) lands in the sketch, with the snap it found. A port of the desktop's Sketcher.snap for the kinds
 * milestone 1 has (an existing point, the middle of a line, a line or circle, the origin, a model edge, level or plumb from `anchor`,
 * the point the shape was started from). Left out: tangent (needs a port of Sketcher._tangent_infer and a "tan" snap sent as a tangent
 * constraint) and alignment with points or midpoints aimed at earlier (needs the client to remember the aimed sources and send a
 * horizontal or vertical constraint to each; a midpoint source also needs its construction point made first). The order is the desktop's: a point wins; then the middle of a
 * line (within the point reach; it wins over any alignment, as on the desktop); then an alignment with the anchor (an existing point at the aligned place still wins over it); then a curve
 * (where a level or plumb alignment crosses it, when there is one); then a model edge; else the free point.
 * Null when the view cannot place the pen on the plane (edge on).
 */
export function snapAim(ctx: SnapContext, x: number, y: number, anchor: Uv | null): Aim | null {
  const raw = ctx.toUv(x, y)
  if (!raw) return null
  const tol = SKETCH_TOLERANCE[ctx.pointer]
  const done = (uv: Uv, snap: Snap, infer: 'H' | 'V' | null = null, guide: Uv | null = null): Aim => ({ uv, at: ctx.toView(uv), snap, infer, guide })
  const hit = pointHit(ctx.layout, x, y, tol.point)
  if (hit) return done(ctx.sketch.points[hit.id]!, { kind: 'point', id: hit.id })
  const mid = midpointHit(ctx, x, y, tol.point)
  if (mid) return done(mid.uv, { kind: 'mid', id: mid.id })
  let uv = raw
  let axis: 'H' | 'V' | null = null
  if (anchor) {
    axis = axisHit(ctx, anchor, x, y)
    if (axis) {
      uv = axis === 'H' ? [raw[0], anchor[1]] : [anchor[0], raw[1]]
      // an existing point at the aligned place wins over the alignment (the line closes onto it)
      const s = ctx.toView(uv)
      const near = pointHit(ctx.layout, s.x, s.y, tol.point)
      if (near) {
        const np = ctx.sketch.points[near.id]!
        if (Math.hypot(np[0] - anchor[0], np[1] - anchor[1]) >= 1e-9) return done(np, { kind: 'point', id: near.id })
      }
    }
  }
  const curve = entityHit(ctx.layout, x, y, tol.entity)
  if (curve) {
    if (!axis) return done(projectOn(ctx.sketch, curve, uv), { kind: 'on', id: curve })
    const on = alignedOn(ctx, curve, uv, axis, x, y)
    if (on) return done(on, { kind: 'on', id: curve }, axis, anchor)
  }
  if (!axis) {
    const e = edgeHit(ctx, x, y, tol.entity)
    if (e) return done(e, { kind: 'edge' })
  }
  return done(uv, null, axis, axis ? anchor : null)
}
