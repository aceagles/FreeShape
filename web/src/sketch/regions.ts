import type { Sketch, StudioOutputs, Vec3 } from '../wire'
import { drawSketch } from './draw'
import { parseSketchData } from './wire'
import type { SketchStatus, Uv } from './wire'

/** A sketch region the view can draw and pick: the server's region number, and its outline in the sketch plane (the outer loop, then the holes). */
export interface RegionShape {
  /** 0-based index into the sketch's `regions` and `regionNames`. */
  index: number
  loops: Uv[][]
}

/** Every region of a committed sketch that could be drawn, with the plane it lies in. */
export interface SketchRegions {
  sketch: string
  origin: Vec3
  x: Vec3
  y: Vec3
  normal: Vec3
  shapes: RegionShape[]
}

const NO_STATUS: SketchStatus = { ok: true, dof: 0, entities: {}, points: {}, constraints: {} }

/** Signed area of a loop (positive counter-clockwise) and the centroid of the area it bounds. */
function areaOf(loop: readonly Uv[]): { area: number; cx: number; cy: number } {
  let a = 0
  let cx = 0
  let cy = 0
  for (let i = 0; i < loop.length; i++) {
    const [x0, y0] = loop[i]!
    const [x1, y1] = loop[(i + 1) % loop.length]!
    const f = x0 * y1 - x1 * y0
    a += f
    cx += (x0 + x1) * f
    cy += (y0 + y1) * f
  }
  if (Math.abs(a) < 1e-12) return { area: 0, cx: loop[0]?.[0] ?? 0, cy: loop[0]?.[1] ?? 0 }
  return { area: a / 2, cx: cx / (3 * a), cy: cy / (3 * a) }
}

/** Even-odd point in polygon. */
function inside(loop: readonly Uv[], u: number, v: number): boolean {
  let on = false
  for (let i = 0, j = loop.length - 1; i < loop.length; j = i++) {
    const [xi, yi] = loop[i]!
    const [xj, yj] = loop[j]!
    if (yi > v !== yj > v && u < ((xj - xi) * (v - yi)) / (yj - yi) + xi) on = !on
  }
  return on
}

/** Whether (u, v) is in the region: inside its outer loop and in none of its holes. */
export function inRegion(shape: RegionShape, u: number, v: number): boolean {
  const [outer, ...holes] = shape.loops
  return outer !== undefined && inside(outer, u, v) && !holes.some((h) => inside(h, u, v))
}

const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]

/**
 * The outlines of a sketch's regions, matched to the server's regions. The client has no region geometry on the wire (only
 * a centroid and an area per region), so the outlines come from the sketch: every closed loop of non-construction curves is
 * a region, with the loops directly inside it as its holes. Each of the server's regions takes the outline whose area and
 * centroid agree; a region that no outline agrees with (loops that cross each other split into pieces the client does not
 * make) has no shape here and is neither drawn nor picked.
 */
export function regionsOfSketch(sk: Sketch): SketchRegions | null {
  const data = parseSketchData(sk.sketch)
  if (!data || sk.regions.length === 0) return null
  const loops = drawSketch(data, NO_STATUS).regions.filter((l) => l.length >= 3)
  const info = loops.map((l) => areaOf(l))
  // the smallest loop that holds a loop is its parent; the loops inside a loop are its holes
  const parent = loops.map((l, i) => {
    let best = -1
    for (let j = 0; j < loops.length; j++) {
      if (j === i || Math.abs(info[j]!.area) <= Math.abs(info[i]!.area)) continue
      if (inside(loops[j]!, l[0]![0], l[0]![1]) && (best < 0 || Math.abs(info[j]!.area) < Math.abs(info[best]!.area))) best = j
    }
    return best
  })
  const faces = loops.map((l, i) => {
    const holes = loops.flatMap((_, j) => (parent[j] === i ? [j] : []))
    let area = Math.abs(info[i]!.area)
    let mx = info[i]!.cx * area
    let my = info[i]!.cy * area
    for (const j of holes) {
      const a = Math.abs(info[j]!.area)
      area -= a
      mx -= info[j]!.cx * a
      my -= info[j]!.cy * a
    }
    return { loops: [l, ...holes.map((j) => loops[j]!)], area, c: [mx / area, my / area] as Uv }
  })
  const x = sk.plane.xdir
  const n = sk.plane.normal
  const y = cross(n, x)
  const o = sk.plane.origin
  const world = (c: Uv): Vec3 => [o[0] + c[0] * x[0] + c[1] * y[0], o[1] + c[0] * x[1] + c[1] * y[1], o[2] + c[0] * x[2] + c[1] * y[2]]
  const taken = new Set<number>()
  const shapes: RegionShape[] = []
  sk.regions.forEach((r, index) => {
    let best = -1
    let bestScore = Infinity
    faces.forEach((f, i) => {
      if (taken.has(i) || !(f.area > 0)) return
      const w = world(f.c)
      const d = Math.hypot(w[0] - r.c[0], w[1] - r.c[1], w[2] - r.c[2])
      const da = Math.abs(f.area - r.area) / Math.max(r.area, 1e-9)
      // a polygon of a circle or spline is a little smaller than the curve: a few percent of area and a small share of the size
      if (da > 0.05 || d > 0.1 * Math.sqrt(r.area) + 0.5) return
      const score = da + d / (Math.sqrt(r.area) + 1)
      if (score < bestScore) [best, bestScore] = [i, score]
    })
    if (best >= 0) {
      taken.add(best)
      shapes.push({ index, loops: faces[best]!.loops })
    }
  })
  return { sketch: sk.id, origin: o, x, y, normal: n, shapes }
}

const cache = new WeakMap<StudioOutputs, SketchRegions[]>()

/**
 * The regions of every sketch the eye shows, for drawing and picking (computed once per outputs). A hidden sketch has
 * none: it is not drawn, so it cannot be picked.
 */
export function visibleRegions(outputs: StudioOutputs): SketchRegions[] {
  let r = cache.get(outputs)
  if (r === undefined) {
    const hidden = new Set(outputs.features.filter((f) => f.hidden || f.suppressed).map((f) => f.id))
    r = outputs.sketches.filter((s) => !hidden.has(s.id)).flatMap((s) => regionsOfSketch(s) ?? [])
    cache.set(outputs, r)
  }
  return r
}
