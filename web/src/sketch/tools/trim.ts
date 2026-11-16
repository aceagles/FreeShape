import type { SketchData, Uv } from '../wire'

const P = (sk: SketchData, id: string | undefined): Uv | null => (id === undefined ? null : (sk.points[id] ?? null))

/** The tools of this file's sibling, the edit tool: Trim, corner Fillet and Use edge (I7.12). */
export const EDIT_TOOLS: ReadonlySet<string> = new Set(['trim', 'corner_fillet', 'use_edge'])

/** Where the line a-b is crossed by the entity `oid`: the parameters t of a-b (not limited to 0..1), as the server's `_intersections_with_line` finds them. */
function crossings(sk: SketchData, oid: string, a: Uv, b: Uv): number[] {
  const o = sk.entities[oid]
  if (!o) return []
  const d: Uv = [b[0] - a[0], b[1] - a[1]]
  const out: number[] = []
  if (o.type === 'line') {
    const c = P(sk, o.p1)
    const e = P(sk, o.p2)
    if (!c || !e) return out
    const f: Uv = [e[0] - c[0], e[1] - c[1]]
    const den = d[0] * f[1] - d[1] * f[0]
    if (Math.abs(den) < 1e-12) return out
    const w: Uv = [c[0] - a[0], c[1] - a[1]]
    const t = (w[0] * f[1] - w[1] * f[0]) / den
    const s = (w[0] * d[1] - w[1] * d[0]) / den
    if (s >= -1e-9 && s <= 1 + 1e-9) out.push(t)
  } else if (o.type === 'circle' || o.type === 'arc') {
    const c = P(sk, o.c)
    if (!c) return out
    let r = o.r ?? 0
    let a1 = 0
    let a2 = 2 * Math.PI
    if (o.type === 'arc') {
      const p1 = P(sk, o.p1)
      const p2 = P(sk, o.p2)
      if (!p1 || !p2) return out
      r = Math.hypot(p1[0] - c[0], p1[1] - c[1])
      a1 = Math.atan2(p1[1] - c[1], p1[0] - c[0])
      a2 = Math.atan2(p2[1] - c[1], p2[0] - c[0])
      while (a2 < a1) a2 += 2 * Math.PI
    }
    const f: Uv = [a[0] - c[0], a[1] - c[1]]
    const A = d[0] * d[0] + d[1] * d[1]
    const B = 2 * (f[0] * d[0] + f[1] * d[1])
    const C = f[0] * f[0] + f[1] * f[1] - r * r
    const disc = B * B - 4 * A * C
    if (disc < 0 || A < 1e-12) return out
    for (const sgn of [-1, 1]) {
      const t = (-B + sgn * Math.sqrt(disc)) / (2 * A)
      if (o.type === 'arc') {
        let ang = Math.atan2(a[1] + t * d[1] - c[1], a[0] + t * d[0] - c[0])
        while (ang < a1) ang += 2 * Math.PI
        if (ang > a2) continue
      }
      out.push(t)
    }
  }
  return out
}

/**
 * The piece of entity `id` that Trim would remove when the click is at `uv`, as the server's `sketch_ops.trim` decides it:
 * a line goes from the crossing before the click to the one after it (the line's ends where there is none); a circle, an
 * arc or any other curve goes whole (`whole`). Null when the entity is not in the sketch.
 */
export function trimPiece(sk: SketchData, id: string, uv: Uv): { whole: true } | { whole: false; from: Uv; to: Uv } | null {
  const e = sk.entities[id]
  if (!e) return null
  if (e.type !== 'line') return { whole: true }
  const a = P(sk, e.p1)
  const b = P(sk, e.p2)
  if (!a || !b) return null
  const d: Uv = [b[0] - a[0], b[1] - a[1]]
  const ts: number[] = []
  for (const [oid, o] of Object.entries(sk.entities)) {
    if (oid === id || o.type === 'point') continue
    for (const t of crossings(sk, oid, a, b)) if (t > 1e-6 && t < 1 - 1e-6) ts.push(t)
  }
  const tc = Math.min(1, Math.max(0, ((uv[0] - a[0]) * d[0] + (uv[1] - a[1]) * d[1]) / Math.max(d[0] * d[0] + d[1] * d[1], 1e-12)))
  const lo = Math.max(0, ...ts.filter((t) => t < tc))
  const hi = Math.min(1, ...ts.filter((t) => t > tc))
  const at = (t: number): Uv => [a[0] + t * d[0], a[1] + t * d[1]]
  return { whole: false, from: at(lo), to: at(hi) }
}

/** The entities that use point `pid` (the server's `_users`: every curve with the point, points themselves left out). */
function usersOf(sk: SketchData, pid: string): string[] {
  return Object.entries(sk.entities)
    .filter(([, e]) => e.type !== 'point' && (e.p1 === pid || e.p2 === pid || e.c === pid || e.a === pid || e.p === pid || (e.pts ?? []).includes(pid)))
    .map(([id]) => id)
}

/** Whether point `pid` is a corner Fillet takes: exactly two lines meet there and nothing else uses it. Returns the length of the shorter line, or null. */
export function cornerOf(sk: SketchData, pid: string): number | null {
  if (!(pid in sk.points)) return null
  const users = usersOf(sk, pid)
  if (users.length !== 2) return null
  const here = sk.points[pid]!
  const lens: number[] = []
  for (const u of users) {
    const e = sk.entities[u]!
    if (e.type !== 'line') return null
    const other = P(sk, e.p1 === pid ? e.p2 : e.p1)
    if (!other) return null
    lens.push(Math.hypot(other[0] - here[0], other[1] - here[1]))
  }
  return Math.min(...lens)
}
