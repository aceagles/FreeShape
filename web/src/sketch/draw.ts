import type { SketchConstraint, SketchData, SketchEntity, SketchStatus, Status, Uv } from './wire'

/** The id of the sketch's fixed origin point. */
export const ORIGIN = 'origin'

/** One curve of the sketch as a polyline in sketch coordinates. */
export interface Stroke {
  id: string
  kind: 'line' | 'circle' | 'arc' | 'ellipse' | 'spline' | 'point'
  pts: Uv[]
  closed: boolean
  /** Drawn dashed, and makes no region. */
  construction: boolean
  status: Status
}

export interface DrawnPoint {
  id: string
  at: Uv
  status: Status
  origin: boolean
}

export type DimKind = 'length' | 'hlen' | 'vlen' | 'radius' | 'diameter' | 'angle'

/** A dimension: what it measures, and its label. Where the dimension line sits is a screen matter (layout.ts). */
export interface Dim {
  id: string
  kind: DimKind
  /** length, hlen, vlen: the two measured points; angle: a point of each line; radius and diameter: the centre in `a`. */
  a: Uv
  b: Uv
  /** angle: the corner where the two lines meet. radius and diameter: the radius in mm in `r`. */
  corner?: Uv
  r?: number
  /** angle: the direction of each line away from the corner. */
  dirs?: [Uv, Uv]
  text: string
  ok: boolean
}

/** A constraint glyph: the constraint, and the places beside its items where the glyph may sit. */
export interface Glyph {
  id: string
  type: string
  ok: boolean
  /** One anchor per referenced item (at most two): where it is, and the direction along it (a line's direction; [1, 0] for a point). */
  anchors: { at: Uv; dir: Uv }[]
}

export interface DrawStats {
  lines: number
  circles: number
  arcs: number
  points: number
  dashed: number
  labels: number
  glyphs: number
  regions: number
}

export interface SketchDrawing {
  strokes: Stroke[]
  points: DrawnPoint[]
  /** Closed outlines of the shaded regions (every one, holes included: fill them with the even-odd rule). */
  regions: Uv[][]
  dims: Dim[]
  glyphs: Glyph[]
  stats: DrawStats
}

const DIMENSION_TYPES = new Set(['distance', 'hdistance', 'vdistance', 'radius', 'diameter', 'angle'])
/** Constraints with no glyph: the points they join are drawn already. */
const NO_GLYPH = new Set(['coincident', 'point_on', 'offset', ...DIMENSION_TYPES])

const CIRCLE_SEGMENTS = 64

const sub = (a: Uv, b: Uv): Uv => [a[0] - b[0], a[1] - b[1]]
const len = (a: Uv): number => Math.hypot(a[0], a[1])
const unit = (a: Uv): Uv => {
  const l = len(a)
  return l < 1e-12 ? [1, 0] : [a[0] / l, a[1] / l]
}

/** A number for a dimension label: up to three decimals, no trailing zeros. */
export function formatValue(v: number): string {
  return String(Number(v.toFixed(3)))
}

function arcPoints(c: Uv, p1: Uv, p2: Uv): Uv[] {
  const r = len(sub(p1, c))
  const a1 = Math.atan2(p1[1] - c[1], p1[0] - c[0])
  let a2 = Math.atan2(p2[1] - c[1], p2[0] - c[0])
  while (a2 <= a1 + 1e-9) a2 += 2 * Math.PI
  const n = Math.max(8, Math.floor((CIRCLE_SEGMENTS * (a2 - a1)) / (2 * Math.PI)) + 2)
  const out: Uv[] = []
  for (let i = 0; i < n; i++) {
    const a = a1 + ((a2 - a1) * i) / (n - 1)
    out.push([c[0] + r * Math.cos(a), c[1] + r * Math.sin(a)])
  }
  return out
}

function circlePoints(c: Uv, r: number): Uv[] {
  const out: Uv[] = []
  for (let i = 0; i < CIRCLE_SEGMENTS; i++) {
    const a = (2 * Math.PI * i) / CIRCLE_SEGMENTS
    out.push([c[0] + r * Math.cos(a), c[1] + r * Math.sin(a)])
  }
  return out
}

function ellipsePoints(c: Uv, a: Uv, r: number): Uv[] {
  const major = sub(a, c)
  const minor = unit([-major[1], major[0]])
  const out: Uv[] = []
  for (let i = 0; i < CIRCLE_SEGMENTS; i++) {
    const t = (2 * Math.PI * i) / CIRCLE_SEGMENTS
    out.push([c[0] + Math.cos(t) * major[0] + Math.sin(t) * r * minor[0], c[1] + Math.cos(t) * major[1] + Math.sin(t) * r * minor[1]])
  }
  return out
}

/** A smooth curve through the points (Catmull-Rom): near the kernel's spline, not the same curve. */
function splinePoints(pts: Uv[], closed: boolean): Uv[] {
  if (pts.length < 3) return pts
  const n = pts.length
  const at = (i: number): Uv => (closed ? pts[((i % n) + n) % n]! : pts[Math.max(0, Math.min(n - 1, i))]!)
  const out: Uv[] = []
  const segments = closed ? n : n - 1
  for (let i = 0; i < segments; i++) {
    const [p0, p1, p2, p3] = [at(i - 1), at(i), at(i + 1), at(i + 2)]
    for (let k = 0; k < 8; k++) {
      const t = k / 8
      const t2 = t * t
      const t3 = t2 * t
      const f = (j: 0 | 1) => 0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2 + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3)
      out.push([f(0), f(1)])
    }
  }
  if (!closed) out.push(pts[n - 1]!)
  return out
}

/** Shaded regions: every closed loop of the entities that are not construction. */
function regionLoops(strokes: Stroke[], entities: Record<string, SketchEntity>): Uv[][] {
  const loops: Uv[][] = []
  const chains: { id: string; a: string; b: string; pts: Uv[] }[] = []
  for (const s of strokes) {
    const e = entities[s.id]
    if (!e || s.construction || e.helper) continue
    if (s.kind === 'circle' || s.kind === 'ellipse' || (s.kind === 'spline' && s.closed)) loops.push(s.pts)
    else if ((s.kind === 'line' || s.kind === 'arc') && e.p1 && e.p2) chains.push({ id: s.id, a: e.p1, b: e.p2, pts: s.pts })
  }
  const at = new Map<string, number[]>()
  chains.forEach((c, i) => {
    for (const p of [c.a, c.b]) at.set(p, [...(at.get(p) ?? []), i])
  })
  const used = new Set<number>()
  chains.forEach((first, i) => {
    if (used.has(i)) return
    const walked: number[] = [i]
    const loop: Uv[] = [...first.pts]
    const from = first.a
    let here = first.b
    let closed = false
    for (;;) {
      if (here === from) {
        closed = walked.length > 1
        break
      }
      const next = (at.get(here) ?? []).filter((j) => j !== walked[walked.length - 1])
      if (next.length !== 1 || walked.includes(next[0]!)) break
      const j = next[0]!
      const c = chains[j]!
      const forward = c.a === here
      loop.push(...(forward ? c.pts : [...c.pts].reverse()).slice(1))
      walked.push(j)
      here = forward ? c.b : c.a
    }
    if (closed) {
      for (const j of walked) used.add(j)
      loops.push(loop)
    }
  })
  return loops
}

/** The anchor of an item a constraint refers to: a point, or an entity (a line's middle, a circle's top). */
function anchorOf(ref: string, data: SketchData): { at: Uv; dir: Uv } | null {
  const p = data.points[ref]
  if (p) return { at: p, dir: [1, 0] }
  const e = data.entities[ref]
  if (!e) return null
  const pt = (id: string | undefined): Uv | null => (id ? (data.points[id] ?? null) : null)
  if (e.type === 'line') {
    const a = pt(e.p1)
    const b = pt(e.p2)
    return a && b ? { at: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], dir: unit(sub(b, a)) } : null
  }
  const c = pt(e.c)
  if (c && (e.type === 'circle' || e.type === 'arc' || e.type === 'ellipse')) {
    const r = e.type === 'arc' ? len(sub(pt(e.p1) ?? c, c)) : (e.r ?? 0)
    return { at: [c[0], c[1] + r], dir: [1, 0] }
  }
  const q = pt(e.p)
  return q ? { at: q, dir: [1, 0] } : null
}

/** A point a reference names: a point, or the point of a point entity. */
function pointOf(ref: string, data: SketchData): Uv | null {
  const p = data.points[ref]
  if (p) return p
  const e = data.entities[ref]
  if (e?.type === 'point' && e.p) return data.points[e.p] ?? null
  return null
}

function lineEnds(ref: string, data: SketchData): [Uv, Uv] | null {
  const e = data.entities[ref]
  if (e?.type !== 'line' || !e.p1 || !e.p2) return null
  const a = data.points[e.p1]
  const b = data.points[e.p2]
  return a && b ? [a, b] : null
}

/** The foot of the perpendicular from `p` on the line through a and b. */
function foot(p: Uv, a: Uv, b: Uv): Uv {
  const d = unit(sub(b, a))
  const t = (p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]
  return [a[0] + d[0] * t, a[1] + d[1] * t]
}

function dimOf(c: SketchConstraint, data: SketchData, ok: boolean): Dim | null {
  const v = c.value
  if (v === undefined || !DIMENSION_TYPES.has(c.type)) return null
  const refs = c.refs
  if (c.type === 'distance' || c.type === 'hdistance' || c.type === 'vdistance') {
    let a: Uv | null = null
    let b: Uv | null = null
    if (refs.length === 1) {
      const ends = lineEnds(refs[0]!, data)
      if (ends) [a, b] = ends
    } else if (refs.length === 2) {
      const pa = pointOf(refs[0]!, data)
      const pb = pointOf(refs[1]!, data)
      if (pa && pb) [a, b] = [pa, pb]
      else {
        // a point and a line: measured to the foot of the perpendicular
        const l = lineEnds(refs[1]!, data)
        const l0 = lineEnds(refs[0]!, data)
        if (pa && l) [a, b] = [pa, foot(pa, l[0], l[1])]
        else if (pb && l0) [a, b] = [pb, foot(pb, l0[0], l0[1])]
      }
    }
    if (!a || !b) return null
    return { id: c.id, kind: c.type === 'distance' ? 'length' : c.type === 'hdistance' ? 'hlen' : 'vlen', a, b, text: `${formatValue(v)} mm`, ok }
  }
  if (c.type === 'radius' || c.type === 'diameter') {
    const e = data.entities[refs[0] ?? '']
    const centre = e?.c ? data.points[e.c] : undefined
    if (!e || !centre) return null
    const r = e.type === 'arc' ? len(sub(data.points[e.p1 ?? ''] ?? centre, centre)) : (e.r ?? 0)
    return { id: c.id, kind: c.type, a: centre, b: centre, r, text: `${c.type === 'radius' ? 'R' : '⌀'} ${formatValue(v)} mm`, ok }
  }
  // angle between two lines
  const l1 = lineEnds(refs[0] ?? '', data)
  const l2 = lineEnds(refs[1] ?? '', data)
  if (!l1 || !l2) return null
  const near = (p: Uv, q: Uv) => len(sub(p, q)) < 1e-6
  let corner: Uv | null = null
  for (const p of l1) for (const q of l2) if (!corner && near(p, q)) corner = p
  corner ??= [(l1[0][0] + l1[1][0] + l2[0][0] + l2[1][0]) / 4, (l1[0][1] + l1[1][1] + l2[0][1] + l2[1][1]) / 4]
  const away = (l: [Uv, Uv]): Uv => unit(len(sub(l[0], corner)) > len(sub(l[1], corner)) ? sub(l[0], corner) : sub(l[1], corner))
  return { id: c.id, kind: 'angle', a: l1[0], b: l2[0], corner, dirs: [away(l1), away(l2)], text: `${formatValue(v)}°`, ok }
}

/** Everything the viewport draws of a sketch, in sketch coordinates, with the solve status that colours it. */
export function drawSketch(data: SketchData, status: SketchStatus): SketchDrawing {
  const strokes: Stroke[] = []
  const stat = (map: Record<string, Status>, id: string): Status => map[id] ?? 'free'
  for (const [id, e] of Object.entries(data.entities)) {
    const pt = (k: string | undefined): Uv | null => (k ? (data.points[k] ?? null) : null)
    let kind: Stroke['kind'] | null = null
    let pts: Uv[] = []
    let closed = false
    if (e.type === 'line') {
      const a = pt(e.p1)
      const b = pt(e.p2)
      if (a && b) [kind, pts] = ['line', [a, b]]
    } else if (e.type === 'circle') {
      const c = pt(e.c)
      if (c && e.r !== undefined) [kind, pts, closed] = ['circle', circlePoints(c, e.r), true]
    } else if (e.type === 'arc') {
      const [c, p1, p2] = [pt(e.c), pt(e.p1), pt(e.p2)]
      if (c && p1 && p2) [kind, pts] = ['arc', arcPoints(c, p1, p2)]
    } else if (e.type === 'ellipse') {
      const [c, a] = [pt(e.c), pt(e.a)]
      if (c && a && e.r !== undefined) [kind, pts, closed] = ['ellipse', ellipsePoints(c, a, e.r), true]
    } else if (e.type === 'spline') {
      const ps = (e.pts ?? []).map(pt).filter((p): p is Uv => p !== null)
      if (ps.length >= 2) [kind, pts, closed] = ['spline', splinePoints(ps, e.closed === true), e.closed === true]
    } else if (e.type === 'point') {
      const p = pt(e.p)
      if (p) [kind, pts] = ['point', [p]]
    }
    if (kind === null) continue
    strokes.push({ id, kind, pts, closed, construction: e.construction === true, status: stat(status.entities, id) })
  }
  const points: DrawnPoint[] = Object.entries(data.points).map(([id, at]) => ({ id, at, status: id === ORIGIN ? 'defined' : stat(status.points, id), origin: id === ORIGIN }))
  const dims: Dim[] = []
  const glyphs: Glyph[] = []
  for (const c of data.constraints) {
    const ok = status.constraints[c.id] !== 'conflicting'
    if (DIMENSION_TYPES.has(c.type)) {
      const d = dimOf(c, data, ok)
      if (d) dims.push(d)
    } else if (!NO_GLYPH.has(c.type)) {
      const anchors = c.refs.slice(0, 2).map((r) => anchorOf(r, data)).filter((a): a is { at: Uv; dir: Uv } => a !== null)
      if (anchors.length > 0) glyphs.push({ id: c.id, type: c.type, ok, anchors })
    }
  }
  const regions = regionLoops(strokes, data.entities)
  const count = (k: Stroke['kind']) => strokes.filter((s) => s.kind === k).length
  return {
    strokes,
    points,
    regions,
    dims,
    glyphs,
    stats: {
      lines: count('line'),
      circles: count('circle'),
      arcs: count('arc'),
      points: count('point'),
      dashed: strokes.filter((s) => s.construction).length,
      labels: dims.length,
      glyphs: glyphs.reduce((n, g) => n + g.anchors.length, 0),
      regions: regions.length,
    },
  }
}
