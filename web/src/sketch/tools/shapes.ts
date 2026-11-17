import type { Snap } from './snap'
import type { Uv } from '../wire'

/** The tools that place points: the strip's Line, Rectangle, Circle, Arc and Point; 'center_rectangle' is the Rectangle's other form. */
export type DrawTool = 'line' | 'rectangle' | 'center_rectangle' | 'circle' | 'arc' | 'point'
export const DRAW_TOOLS: readonly string[] = ['line', 'rectangle', 'center_rectangle', 'circle', 'arc', 'point']
export const isDrawTool = (id: string): id is DrawTool => DRAW_TOOLS.includes(id)

/** A committed point of a shape: where, what it snapped to, and (a line's chain) the id of the existing point it is. */
export interface Pick {
  uv: Uv
  snap: Snap
  infer?: 'H' | 'V' | null
  /** The point of the sketch this pick is (the end of the line before it in a chain); the server is told so with {"at", "point"}. */
  point?: string
}

/** How many committed points make the shape: a circle's second is its rim, an arc's third is a point it passes through. */
export const PICKS: Record<DrawTool, number> = { line: 2, rectangle: 2, center_rectangle: 2, circle: 2, arc: 3, point: 1 }

const SHAPE: Record<DrawTool, string> = { line: 'line', rectangle: 'rect', center_rectangle: 'center_rect', circle: 'circle', arc: 'arc3', point: 'point' }

export interface Constraint {
  type: string
  refs: string[]
}

/**
 * The constraints the snaps of `picks` imply, in the server's words (sketch.add): a pick on an existing point is a coincident
 * with it, a pick on the middle of a line is a midpoint, a pick on a line or circle is a point_on, and a line whose end was level or plumb with its start is horizontal or
 * vertical. A model edge snap places the point and ties it to nothing (the server's sketch.add has no model reference).
 */
export function snapConstraints(tool: DrawTool, picks: readonly Pick[]): Constraint[] {
  const out: Constraint[] = []
  // the picks that become the sketch's own points: a circle's rim and an arc's through point are not constrained
  const placed = tool === 'circle' ? 1 : tool === 'arc' ? 2 : picks.length
  for (let i = 0; i < Math.min(placed, picks.length); i++) {
    const p = picks[i]!
    if (p.point) continue
    if (p.snap?.kind === 'point') out.push({ type: 'coincident', refs: [`$p${i}`, p.snap.id] })
    else if (p.snap?.kind === 'mid') out.push({ type: 'midpoint', refs: [`$p${i}`, p.snap.id] })
    else if (p.snap?.kind === 'on') out.push({ type: 'point_on', refs: [`$p${i}`, p.snap.id] })
  }
  if (tool === 'line' && picks.length === 2 && picks[1]!.infer) out.push({ type: picks[1]!.infer === 'H' ? 'horizontal' : 'vertical', refs: ['$e0'] })
  return out
}

const pickBody = (p: Pick): Uv | { at: Uv; point: string } => (p.point ? { at: p.uv, point: p.point } : p.uv)

/** The sketch.add message (without its type) for a finished shape. */
export function addBody(tool: DrawTool, picks: readonly Pick[], construction = false): Record<string, unknown> {
  const constraints = snapConstraints(tool, picks)
  const body: Record<string, unknown> = { shape: SHAPE[tool], picks: (tool === 'circle' ? picks.slice(0, 1) : picks).map(pickBody) }
  if (tool === 'circle') {
    const [c, r] = [picks[0]!.uv, picks[1]!.uv]
    body['radius'] = Math.hypot(r[0] - c[0], r[1] - c[1])
  }
  if (construction) body['construction'] = true
  if (constraints.length > 0) body['constraints'] = constraints
  return body
}

const dist = (a: Uv, b: Uv): number => Math.hypot(a[0] - b[0], a[1] - b[1])

/** Whether the next point is too close to the ones before it to make the shape (a zero length line, a zero radius circle). */
export function degenerate(tool: DrawTool, picks: readonly Pick[], next: Uv): boolean {
  if (tool === 'point') return false
  const eps = 1e-6
  if (picks.some((p) => dist(p.uv, next) < eps)) return true
  if (tool === 'arc' && picks.length === 2) return Math.abs(cross(picks[0]!.uv, picks[1]!.uv, next)) < eps
  if (tool.endsWith('rectangle') && picks.length === 1) return Math.abs(next[0] - picks[0]!.uv[0]) < eps || Math.abs(next[1] - picks[0]!.uv[1]) < eps
  return false
}

const cross = (a: Uv, b: Uv, c: Uv): number => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

const SEGMENTS = 64

/** The circle through three points: centre and radius, or null when they are in a line. */
export function circleThrough(a: Uv, b: Uv, c: Uv): { c: Uv; r: number } | null {
  const d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
  if (Math.abs(d) < 1e-12) return null
  const a2 = a[0] * a[0] + a[1] * a[1]
  const b2 = b[0] * b[0] + b[1] * b[1]
  const c2 = c[0] * c[0] + c[1] * c[1]
  const ux = (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d
  const uy = (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d
  return { c: [ux, uy], r: Math.hypot(a[0] - ux, a[1] - uy) }
}

/** The rubber band: the shape so far as polylines in sketch coordinates, with the pen at `cur`. */
export function rubberBand(tool: DrawTool, picks: readonly Pick[], cur: Uv): Uv[][] {
  const p0 = picks[0]?.uv
  if (!p0) return []
  switch (tool) {
    case 'line':
      return [[p0, cur]]
    case 'rectangle':
      return [[p0, [cur[0], p0[1]], cur, [p0[0], cur[1]], p0]]
    case 'center_rectangle': {
      const dx = cur[0] - p0[0]
      const dy = cur[1] - p0[1]
      return [[[p0[0] - dx, p0[1] - dy], [p0[0] + dx, p0[1] - dy], [p0[0] + dx, p0[1] + dy], [p0[0] - dx, p0[1] + dy], [p0[0] - dx, p0[1] - dy]]]
    }
    case 'circle': {
      const r = dist(p0, cur)
      return [Array.from({ length: SEGMENTS + 1 }, (_, i) => [p0[0] + r * Math.cos((2 * Math.PI * i) / SEGMENTS), p0[1] + r * Math.sin((2 * Math.PI * i) / SEGMENTS)] as Uv)]
    }
    case 'arc': {
      if (picks.length === 1) return [[p0, cur]]
      const [a, b] = [p0, picks[1]!.uv]
      const o = circleThrough(a, b, cur)
      if (!o) return [[a, cur, b]]
      // from the start through the pen to the end, the way round that passes the pen
      const ang = (p: Uv) => Math.atan2(p[1] - o.c[1], p[0] - o.c[0])
      const [sa, ma, ea] = [ang(a), ang(cur), ang(b)]
      const ccw = (x: number, y: number) => (((y - x) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI)
      const sweep = ccw(sa, ma) <= ccw(sa, ea) ? ccw(sa, ea) : -ccw(ea, sa)
      return [Array.from({ length: SEGMENTS + 1 }, (_, i) => [o.c[0] + o.r * Math.cos(sa + (sweep * i) / SEGMENTS), o.c[1] + o.r * Math.sin(sa + (sweep * i) / SEGMENTS)] as Uv)]
    }
    default:
      return []
  }
}
