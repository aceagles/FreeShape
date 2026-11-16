import type { BoxDrag } from '../input/pointer'
import { idOfItem } from '../selection/store'
import type { SelectionItem } from '../selection/store'
import { visibleRegions } from '../sketch/regions'
import { elementName } from '../wire'
import type { StudioOutputs } from '../wire'
import type { ViewCamera } from './camera'
import type { PickFilter, PickKind } from './pick'

/**
 * Box selection (I9.9), worked out on the client from what the view already draws: a port of the desktop's
 * `Viewport._items_in_rect` (freeshape/ui/viewport.py) and its helpers (freeshape/ui/boxsel.py). Dragged left to right
 * the box takes what lies wholly inside it; right to left (crossing) also what it touches. Like the desktop it looks at
 * screen positions only: an element behind another is taken too. No server call, no listeners, no drawing.
 *
 * Cost: one projection of every drawn mesh vertex, edge point and vertex (a few multiply-adds each), then one pass over
 * the triangles of each face. It runs once, when the button is released, never while the box is dragged.
 */

/** A rectangle in view pixels, `x0 <= x1` and `y0 <= y1`. */
export interface Rect {
  x0: number
  y0: number
  x1: number
  y1: number
}

/** A box dragged right to left selects what it crosses (boxsel.is_crossing). */
export const isCrossing = (b: BoxDrag): boolean => b.x1 < b.x0

export const normRect = (b: BoxDrag): Rect => ({ x0: Math.min(b.x0, b.x1), y0: Math.min(b.y0, b.y1), x1: Math.max(b.x0, b.x1), y1: Math.max(b.y0, b.y1) })

/**
 * What a box takes when no dialog's filter says otherwise: the desktop's Select menu defaults that a box can reach (faces,
 * edges, sketch regions; vertices and whole parts are off there, and its box never takes planes or axes). I9.14's Select
 * filter replaces this.
 */
export const BOX_KINDS: readonly PickKind[] = ['face', 'edge', 'region']

/**
 * Screen positions (x y pairs, CSS pixels from the view's top left) of points given as x y z triples, optionally moved by
 * a 4 x 4 matrix first (three.js `Matrix4.elements`, column-major).
 */
export function projectPoints(view: ViewCamera, src: ArrayLike<number>, matrix?: ArrayLike<number>): Float64Array {
  const { right, up } = view.axes()
  const { target, scale } = view
  const hw = view.width / 2
  const hh = view.height / 2
  const n = Math.floor(src.length / 3)
  const out = new Float64Array(n * 2)
  const m = matrix
  for (let i = 0; i < n; i++) {
    let x = src[3 * i] as number
    let y = src[3 * i + 1] as number
    let z = src[3 * i + 2] as number
    if (m) {
      const wx = (m[0] as number) * x + (m[4] as number) * y + (m[8] as number) * z + (m[12] as number)
      const wy = (m[1] as number) * x + (m[5] as number) * y + (m[9] as number) * z + (m[13] as number)
      const wz = (m[2] as number) * x + (m[6] as number) * y + (m[10] as number) * z + (m[14] as number)
      x = wx
      y = wy
      z = wz
    }
    const dx = x - target.x
    const dy = y - target.y
    const dz = z - target.z
    out[2 * i] = hw + (dx * right.x + dy * right.y + dz * right.z) / scale
    out[2 * i + 1] = hh - (dx * up.x + dy * up.y + dz * up.z) / scale
  }
  return out
}

/** Which screen points (x y pairs) lie in the rectangle, edges included. */
export function pointsInRect(S: ArrayLike<number>, r: Rect): Uint8Array {
  const n = Math.floor(S.length / 2)
  const inside = new Uint8Array(n)
  for (let i = 0; i < n; i++) {
    const x = S[2 * i] as number
    const y = S[2 * i + 1] as number
    inside[i] = x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1 ? 1 : 0
  }
  return inside
}

/** Whether the segment a-b touches the rectangle: their boxes overlap and the rectangle's corners are not all on one side of it (boxsel.segs_hit_rect). */
export function segmentHitsRect(ax: number, ay: number, bx: number, by: number, r: Rect): boolean {
  if (Math.max(ax, bx) < r.x0 || Math.min(ax, bx) > r.x1 || Math.max(ay, by) < r.y0 || Math.min(ay, by) > r.y1) return false
  const dx = bx - ax
  const dy = by - ay
  let lo = Infinity
  let hi = -Infinity
  for (const [cx, cy] of [
    [r.x0, r.y0],
    [r.x1, r.y0],
    [r.x1, r.y1],
    [r.x0, r.y1],
  ] as const) {
    const c = dx * (cy - ay) - dy * (cx - ax)
    if (c < lo) lo = c
    if (c > hi) hi = c
  }
  return lo <= 0 && hi >= 0
}

/** Whether a screen triangle overlaps the rectangle (separating axes, boxsel.tris_hit_rect). */
export function triangleHitsRect(P: readonly number[], r: Rect): boolean {
  const xs = [P[0]!, P[2]!, P[4]!]
  const ys = [P[1]!, P[3]!, P[5]!]
  if (Math.max(...xs) < r.x0 || Math.min(...xs) > r.x1 || Math.max(...ys) < r.y0 || Math.min(...ys) > r.y1) return false
  const corners = [r.x0, r.y0, r.x1, r.y0, r.x1, r.y1, r.x0, r.y1]
  for (let i = 0; i < 3; i++) {
    const j = (i + 1) % 3
    const nx = -(ys[j]! - ys[i]!)
    const ny = xs[j]! - xs[i]!
    let tlo = Infinity
    let thi = -Infinity
    for (let k = 0; k < 3; k++) {
      const d = xs[k]! * nx + ys[k]! * ny
      tlo = Math.min(tlo, d)
      thi = Math.max(thi, d)
    }
    let clo = Infinity
    let chi = -Infinity
    for (let k = 0; k < 4; k++) {
      const d = corners[2 * k]! * nx + corners[2 * k + 1]! * ny
      clo = Math.min(clo, d)
      chi = Math.max(chi, d)
    }
    if (thi < clo || tlo > chi) return false
  }
  return true
}

/** A screen polyline (x y pairs): wholly inside the box, or (crossing) touching it (boxsel.polyline_selected). */
export function polylineSelected(S: ArrayLike<number>, r: Rect, crossing: boolean): boolean {
  const n = Math.floor(S.length / 2)
  if (n === 0) return false
  const inside = pointsInRect(S, r)
  if (!crossing) return inside.every((v) => v === 1)
  if (inside.some((v) => v === 1)) return true
  for (let i = 0; i + 1 < n; i++) if (segmentHitsRect(S[2 * i] as number, S[2 * i + 1] as number, S[2 * i + 2] as number, S[2 * i + 3] as number, r)) return true
  return false
}

/**
 * Triangles `t0` to `t1` (exclusive) of `indices` over the screen points `S`: wholly inside the box, or (crossing)
 * touching it (boxsel.mesh_part_selected). `inside` is `pointsInRect(S, r)`, shared by every face of a mesh.
 */
export function trianglesSelected(S: ArrayLike<number>, indices: ArrayLike<number>, t0: number, t1: number, inside: Uint8Array, r: Rect, crossing: boolean): boolean {
  if (t1 <= t0) return false
  if (!crossing) {
    for (let k = 3 * t0; k < 3 * t1; k++) if (inside[indices[k] as number] !== 1) return false
    return true
  }
  for (let k = 3 * t0; k < 3 * t1; k++) if (inside[indices[k] as number] === 1) return true
  const P = [0, 0, 0, 0, 0, 0]
  for (let t = t0; t < t1; t++) {
    for (let c = 0; c < 3; c++) {
      const v = indices[3 * t + c] as number
      P[2 * c] = S[2 * v] as number
      P[2 * c + 1] = S[2 * v + 1] as number
    }
    if (triangleHitsRect(P, r)) return true
  }
  return false
}

/** Whether a screen point is inside the closed loops (even-odd: holes are outside). */
function insideLoops(loops: readonly Float64Array[], x: number, y: number): boolean {
  let inside = false
  for (const L of loops) {
    const n = L.length / 2
    for (let i = 0, j = n - 1; i < n; j = i++) {
      const xi = L[2 * i]!
      const yi = L[2 * i + 1]!
      const xj = L[2 * j]!
      const yj = L[2 * j + 1]!
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside
    }
  }
  return inside
}

/**
 * The items of a Part Studio a box takes, in the scene's order: vertices, edges, then faces (or whole parts when the
 * kinds have `part` and not `face`) of each drawn part, then the regions of the sketches the eye shows. `drawn` holds the
 * ids of the parts the view draws (a part the eye hides is not taken); Context bodies are never taken, as on the desktop.
 */
export function studioBoxHits(outputs: StudioOutputs, drawn: ReadonlySet<string>, view: ViewCamera, box: BoxDrag, kinds: PickFilter = BOX_KINDS): SelectionItem[] {
  const want = new Set<PickKind>(kinds)
  const r = normRect(box)
  const crossing = isCrossing(box)
  const out: SelectionItem[] = []
  const add = (kind: PickKind, part: string, index: number, name: string | null) => out.push({ id: idOfItem(kind, part, name, index, null), kind, part, index, name, key: null })
  const doFace = want.has('face')
  const doPart = want.has('part') && !doFace
  for (const part of outputs.parts) {
    const mesh = part.mesh
    if (!mesh || !drawn.has(part.id)) continue
    if (want.has('vertex')) {
      const inside = pointsInRect(projectPoints(view, mesh.vertices), r)
      inside.forEach((v, i) => {
        if (v === 1) add('vertex', part.id, i, elementName(part, 'vertex', i) ?? null)
      })
    }
    if (want.has('edge')) {
      mesh.edges.forEach((poly, e) => {
        if (poly.length >= 6 && polylineSelected(projectPoints(view, poly), r, crossing)) add('edge', part.id, e, elementName(part, 'edge', e) ?? null)
      })
    }
    if (doFace || doPart) {
      const S = projectPoints(view, mesh.positions)
      const inside = pointsInRect(S, r)
      if (doFace) {
        mesh.faceRanges.forEach(([a, b], f) => {
          if (trianglesSelected(S, mesh.indices, a, b, inside, r, crossing)) add('face', part.id, f, elementName(part, 'face', f) ?? null)
        })
      } else if (trianglesSelected(S, mesh.indices, 0, mesh.indices.length / 3, inside, r, crossing)) {
        out.push({ id: idOfItem('part', part.id, null, -1, null), kind: 'part', part: part.id, index: -1, name: null, key: null })
      }
    }
  }
  if (want.has('region')) {
    for (const sk of visibleRegions(outputs)) {
      const names = outputs.sketches.find((s) => s.id === sk.sketch)?.regionNames ?? []
      const [o, x, y] = [sk.origin, sk.x, sk.y]
      for (const shape of sk.shapes) {
        const loops = shape.loops.map((loop) => {
          const world: number[] = []
          for (const [u, v] of loop) world.push(o[0] + x[0] * u + y[0] * v, o[1] + x[1] * u + y[1] * v, o[2] + x[2] * u + y[2] * v)
          // closed: the last point back to the first
          if (loop.length > 0) world.push(world[0]!, world[1]!, world[2]!)
          return projectPoints(view, world)
        })
        const hit = crossing
          ? loops.some((L) => polylineSelected(L, r, true)) || insideLoops(loops, r.x0, r.y0)
          : loops.length > 0 && loops.every((L) => polylineSelected(L, r, false))
        if (hit) out.push({ id: idOfItem('region', sk.sketch, names[shape.index] ?? null, shape.index, null), kind: 'region', part: sk.sketch, index: shape.index, name: names[shape.index] ?? null, key: null })
      }
    }
  }
  return out
}
