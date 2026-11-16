import type { Dim, SketchDrawing, Stroke } from './draw'
import type { Status, Uv } from './wire'

export interface P {
  x: number
  y: number
}
export type Project = (uv: Uv) => P

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface ScreenStroke {
  id: string
  kind: Stroke['kind']
  pts: P[]
  closed: boolean
  construction: boolean
  status: Status
}

export interface ScreenDim {
  id: string
  ok: boolean
  /** Extension and dimension lines. */
  segs: [P, P][]
  /** Arrow heads: the tip and the unit direction the arrow points. */
  arrows: { tip: P; dir: P }[]
  /** An angle's arc. */
  arc: P[]
  label: Rect & { text: string }
}

export interface ScreenGlyph {
  id: string
  type: string
  ok: boolean
  x: number
  y: number
  /** Side of the square box. */
  size: number
}

/** The sketch as it lies on screen: what the SVG draws and what a pick measures against. */
export interface ScreenLayout {
  strokes: ScreenStroke[]
  points: { id: string; x: number; y: number; status: Status; origin: boolean }[]
  regions: P[][]
  /** The sketch's x and y axes through the origin, as dotted lines across the view. */
  axes: [P, P][]
  dims: ScreenDim[]
  glyphs: ScreenGlyph[]
}

/** Layout constants in CSS pixels, from the layout mock's sketch state. */
export const DIM = {
  /** Gap between the geometry and the start of an extension line, and how far the dimension line sits past the geometry. */
  gap: 14,
  offset: 32,
  overshoot: 10,
  arrowLength: 12,
  arrowHalf: 6,
  labelHeight: 28,
  labelPad: 16,
  charWidth: 8.2,
} as const
export const GLYPH_SIZE = 20
const GLYPH_STEP = 24
const GLYPH_SLIDE = 12
const GLYPH_GAP = 3
const GLYPH_OFFSET = 18
const AXIS_REACH = 6000

const sub = (a: P, b: P): P => ({ x: a.x - b.x, y: a.y - b.y })
const add = (a: P, b: P, k = 1): P => ({ x: a.x + b.x * k, y: a.y + b.y * k })
const dot = (a: P, b: P): number => a.x * b.x + a.y * b.y
const norm = (a: P): P => {
  const l = Math.hypot(a.x, a.y)
  return l < 1e-9 ? { x: 1, y: 0 } : { x: a.x / l, y: a.y / l }
}

/** The side of a line a label or glyph goes: above a flat line, left of a steep one. */
function sideOf(d: P): P {
  const n = { x: -d.y, y: d.x }
  return Math.abs(n.y) >= Math.abs(n.x) ? (n.y > 0 ? n : { x: -n.x, y: -n.y }) : n.x < 0 ? n : { x: -n.x, y: -n.y }
}

/** `n` turned (if need be) to point away from `centre`, as seen from `at`; unchanged when `at` is the centre. */
function outward(n: P, at: P, centre: P): P {
  const d = (at.x - centre.x) * n.x + (at.y - centre.y) * n.y
  return d < -1e-6 ? { x: -n.x, y: -n.y } : n
}

const labelBox = (centre: P, text: string): Rect & { text: string } => {
  const w = Math.max(40, Math.round(text.length * DIM.charWidth + DIM.labelPad))
  return { x: centre.x - w / 2, y: centre.y - DIM.labelHeight / 2, w, h: DIM.labelHeight, text }
}

function layoutDim(d: Dim, proj: Project, centre: P): ScreenDim {
  const out: ScreenDim = { id: d.id, ok: d.ok, segs: [], arrows: [], arc: [], label: labelBox({ x: 0, y: 0 }, d.text) }
  if (d.kind === 'length' || d.kind === 'hlen' || d.kind === 'vlen') {
    const A = proj(d.a)
    const B = proj(d.b)
    // the direction the dimension line runs on screen: along the measured segment, or along the sketch's x or y axis
    let along: P
    if (d.kind === 'length') along = norm(sub(B, A))
    else {
      const o = proj([0, 0])
      along = norm(sub(proj(d.kind === 'hlen' ? [1, 0] : [0, 1]), o))
    }
    // sideOf wants the normal's side; the normal of `along` is what it returns
    const mid = { x: (A.x + B.x) / 2, y: (A.y + B.y) / 2 }
    const N = outward(sideOf(along), mid, centre)
    const place = d.at ? proj(d.at) : null
    const t = place ? dot(place, N) : Math.max(dot(A, N), dot(B, N)) + DIM.offset
    const A2 = add(A, N, t - dot(A, N))
    const B2 = add(B, N, t - dot(B, N))
    out.segs.push([add(A, N, DIM.gap), add(A2, N, DIM.overshoot)], [add(B, N, DIM.gap), add(B2, N, DIM.overshoot)], [A2, B2])
    const dir = norm(sub(B2, A2))
    out.arrows.push({ tip: A2, dir: { x: -dir.x, y: -dir.y } }, { tip: B2, dir })
    out.label = labelBox(place ?? { x: (A2.x + B2.x) / 2, y: (A2.y + B2.y) / 2 }, d.text)
    return out
  }
  if (d.kind === 'radius' || d.kind === 'diameter') {
    const c = proj(d.a)
    const r = d.r ?? 0
    const k = Math.SQRT1_2
    const edge = proj([d.a[0] + r * k, d.a[1] + r * k])
    const dir = norm(sub(edge, c))
    const far = d.kind === 'diameter' ? sub(c, sub(edge, c)) : c
    out.segs.push([far, add(edge, dir, DIM.overshoot)])
    out.arrows.push({ tip: edge, dir })
    if (d.kind === 'diameter') out.arrows.push({ tip: far, dir: { x: -dir.x, y: -dir.y } })
    const lab = labelBox(d.at ? proj(d.at) : add(edge, dir, DIM.overshoot + 36), d.text)
    out.label = lab
    return out
  }
  // angle: an arc between the two lines about the corner, the label past its middle
  const corner = proj(d.corner ?? d.a)
  const dirs = (d.dirs ?? [[1, 0], [0, 1]]).map((u) => norm(sub(proj([(d.corner ?? d.a)[0] + u[0], (d.corner ?? d.a)[1] + u[1]]), corner)))
  const a1 = Math.atan2(dirs[0]!.y, dirs[0]!.x)
  let a2 = Math.atan2(dirs[1]!.y, dirs[1]!.x)
  let sweep = a2 - a1
  while (sweep > Math.PI) sweep -= 2 * Math.PI
  while (sweep < -Math.PI) sweep += 2 * Math.PI
  a2 = a1 + sweep
  const R = 44
  const steps = Math.max(6, Math.ceil(Math.abs(sweep) / 0.15))
  for (let i = 0; i <= steps; i++) {
    const a = a1 + (sweep * i) / steps
    out.arc.push({ x: corner.x + R * Math.cos(a), y: corner.y + R * Math.sin(a) })
  }
  const mid = a1 + sweep / 2
  out.label = labelBox(d.at ? proj(d.at) : { x: corner.x + (R + 28) * Math.cos(mid), y: corner.y + (R + 28) * Math.sin(mid) }, d.text)
  const tail = (a: number, s: number): P => ({ x: -Math.sin(a) * s, y: Math.cos(a) * s })
  out.arrows.push({ tip: out.arc[0]!, dir: norm(tail(a1, -Math.sign(sweep) || -1)) }, { tip: out.arc[out.arc.length - 1]!, dir: norm(tail(a2, Math.sign(sweep) || 1)) })
  return out
}

/** Put a drawing on screen with `proj` (sketch coordinates to CSS pixels from the view's top left). */
export function layoutSketch(d: SketchDrawing, proj: Project): ScreenLayout {
  const strokes: ScreenStroke[] = d.strokes.map((s) => ({ id: s.id, kind: s.kind, pts: s.pts.map(proj), closed: s.closed, construction: s.construction, status: s.status }))
  const o = proj([0, 0])
  const axes = ([[1, 0], [0, 1]] as const).map((u): [P, P] => {
    const dir = norm(sub(proj(u), o))
    return [add(o, dir, -AXIS_REACH), add(o, dir, AXIS_REACH)]
  })
  const allPts = d.points.map((p) => proj(p.at))
  const centre = allPts.length ? { x: allPts.reduce((n, p) => n + p.x, 0) / allPts.length, y: allPts.reduce((n, p) => n + p.y, 0) / allPts.length } : { x: 0, y: 0 }
  const dims = d.dims.map((x) => layoutDim(x, proj, centre))
  const glyphs: ScreenGlyph[] = []
  // A glyph is placed after the labels: it slides along its item, then steps further off it, until its box touches no
  // dimension label and no other glyph. Labels win.
  const free = (x: number, y: number): boolean => {
    const half = GLYPH_SIZE / 2 + GLYPH_GAP
    for (const l of dims) if (x + half > l.label.x && x - half < l.label.x + l.label.w && y + half > l.label.y && y - half < l.label.y + l.label.h) return false
    for (const o of glyphs) if (Math.abs(o.x - x) < GLYPH_SIZE + GLYPH_GAP && Math.abs(o.y - y) < GLYPH_SIZE + GLYPH_GAP) return false
    return true
  }
  for (const g of d.glyphs) {
    for (const a of g.anchors) {
      const at = proj(a.at)
      const along = norm(sub(proj([a.at[0] + a.dir[0], a.at[1] + a.dir[1]]), at))
      // a glyph goes on the other side from a dimension: above a flat item, right of a steep one
      const away = sideOf(along)
      const side = { x: -away.x, y: -away.y }
      let pos = add(at, side, GLYPH_OFFSET)
      search: for (let ring = 0; ring < 6; ring++) {
        for (let k = 0; k <= 8; k++) {
          for (const sign of k === 0 ? [1] : [1, -1]) {
            const c = add(add(at, side, GLYPH_OFFSET + ring * GLYPH_STEP), along, sign * k * GLYPH_SLIDE)
            if (free(c.x, c.y)) {
              pos = c
              break search
            }
          }
        }
      }
      glyphs.push({ id: g.id, type: g.type, ok: g.ok, x: pos.x, y: pos.y, size: GLYPH_SIZE })
    }
  }
  return {
    strokes,
    points: d.points.map((p) => ({ ...proj(p.at), id: p.id, status: p.status, origin: p.origin })),
    regions: d.regions.map((r) => r.map(proj)),
    axes,
    dims,
    glyphs,
  }
}
