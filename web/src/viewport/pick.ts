import { DoubleSide, Ray, Mesh, Vector3 } from 'three'
import type { Object3D } from 'three'
import type { MeshBVH } from 'three-mesh-bvh'
import { elementName } from '../wire'
import type { Part, StudioOutputs, Vec3 } from '../wire'
import type { ViewCamera } from './camera'
import { axisEnds, DEFAULT_PLANES } from './scene'
import type { BuiltScene } from './scene'

/**
 * Picking: what is under a view position. A port of `Viewport.pick` in freeshape/ui/viewport.py (the desktop's order
 * of precedence, its rule for hidden edges and vertices, how planes and axes compete with faces), with the card's
 * changes: larger tolerances that depend on the pointer, and a deterministic tie-break. It is a library: no
 * listeners, no drawing, no selection state. The pointer module's caller (card I4.2) turns a pen or finger position
 * into a call.
 *
 * What the desktop's `pick` can return, and what is here:
 *   face, edge, vertex, part ........ here (part only when the filter has `part` and not `face`, as on the desktop)
 *   plane (default and the studio's), axis ... here
 *   region, sketch_line, sketch_point, connector ... not here: sketch items and mate connectors are drawn by the
 *   sketch slice, and the scene has nothing to hit before that. Their kinds are not in `PickKind` yet.
 *
 * INDEX BASE. `index` is 0-based, as everywhere in the client (see wire/types.ts). Add 1 when it goes back to the
 * server. `name` is the Element name the outputs carry for it (null when the element has none), which needs no index.
 *
 * Order of precedence, as on the desktop: vertex, edge, axis, plane (when level with or in front of the nearest
 * face), then face. Vertices and edges count inside the pointer's tolerance; those behind the nearest face under
 * the pointer are skipped.
 */

export type PickKind = 'face' | 'edge' | 'vertex' | 'part' | 'plane' | 'axis'

/** `touch` is a finger (the Pointer Events name); the pointer module's pen and mouse use the same words. */
export type PickPointer = 'pen' | 'touch' | 'mouse'

/** Screen-space reach, in CSS pixels, of each kind of pick. */
export interface Tolerances {
  vertex: number
  edge: number
  axis: number
}

/**
 * The card gives the pen 10 and a finger 22 (for vertices and edges alike). The mouse is not named by the card and
 * keeps the desktop's own: 9 for vertices, 7 for edges and axes.
 */
export const TOLERANCE: Readonly<Record<PickPointer, Tolerances>> = {
  pen: { vertex: 10, edge: 10, axis: 10 },
  touch: { vertex: 22, edge: 22, axis: 22 },
  mouse: { vertex: 9, edge: 7, axis: 7 },
}

/** What a pick considers when no filter is given: everything the scene has (the desktop leaves vertices out; the card puts them first). */
export const DEFAULT_KINDS: readonly PickKind[] = ['face', 'edge', 'vertex', 'plane', 'axis']

interface PickBase {
  /** Where, in the studio's coordinates (mm): the ray's hit on a face or plane, the nearest point of an edge or axis, the vertex. */
  readonly point: Vec3
}

/** A face, edge or vertex of a part. */
export interface ElementPick extends PickBase {
  readonly kind: 'face' | 'edge' | 'vertex'
  /** Id of the part. */
  readonly part: string
  /** 0-based index of the face, edge or vertex in the part. */
  readonly index: number
  /** The Element name, or null when the outputs carry none for it. */
  readonly name: string | null
  /** Screen distance from the pointer in CSS pixels: how far an edge or vertex was; null for a face (the ray hit it). */
  readonly distancePx: number | null
  /** Id of the Assembly instance the element is on (card I8.1); absent for a Part Studio, where `part` is the part's id. In an Assembly `part` is the part's hash. */
  readonly instance?: string
}

/** A whole part (the filter asked for parts, not faces). */
export interface PartPick extends PickBase {
  readonly kind: 'part'
  readonly part: string
  readonly index: -1
  readonly name: null
  readonly distancePx: null
}

/** A construction plane or axis; `key` is its feature id, or Top, Front, Right for the three default planes. */
export interface DatumPick extends PickBase {
  readonly kind: 'plane' | 'axis'
  readonly part: null
  readonly index: -1
  readonly name: null
  readonly key: string
  /** Screen distance for an axis; null for a plane. */
  readonly distancePx: number | null
}

export type PickHit = ElementPick | PartPick | DatumPick

export type PickFilter = ReadonlySet<PickKind> | readonly PickKind[]

/** A view position (CSS pixels from the top left) as a ray into the model: a point well in front of everything, looking along the view. */
export function viewRay(view: ViewCamera, x: number, y: number): Ray {
  const { back } = view.axes()
  return new Ray(view.unproject(x, y).addScaledVector(back, view.depthRadius * 8), back.clone().negate())
}

/** The nearest point where a ray meets a drawn part (the Context bodies too), or null. Shared with the controller's `pickPoint`. */
export function rayPoint(root: Object3D, ray: Ray): Vector3 | null {
  let best: Vector3 | null = null
  let bestDistance = Infinity
  root.updateMatrixWorld(true)
  root.traverse((o) => {
    const bvh = o.userData['bvh'] as MeshBVH | undefined
    if (!(o instanceof Mesh) || !bvh) return
    const local = ray.clone().applyMatrix4(o.matrixWorld.clone().invert())
    const hit = bvh.raycastFirst(local, DoubleSide)
    if (!hit) return
    const world = hit.point.clone().applyMatrix4(o.matrixWorld)
    const d = world.distanceTo(ray.origin)
    if (d < bestDistance) {
      bestDistance = d
      best = world
    }
  })
  return best
}

/** Two screen distances closer than this (pixels) are equal; two depths closer than this (mm) are level. */
const TIE_PX = 1e-6
const TIE_MM = 1e-7

/** Depth the desktop allows between a face and what counts as level with it: 12 screen pixels (`scale * 3 * 4`). */
const LEVEL_PX = 12
/** The desktop's edge tolerance, which its 12 px of depth slack goes with. A larger tolerance gets proportionally more slack. */
const DESKTOP_EDGE_PX = 7

interface PartEntry {
  readonly id: string
  readonly part: Part
  readonly bvh: MeshBVH
  /** A Context body: drawn translucent, picked only when nothing of the studio's own parts is (the desktop's "select through context"). */
  readonly context: boolean
  readonly triangleFace: Int32Array
  /** First edge and vertex of this part in the picker's flat tables. */
  readonly edgeStart: number
  readonly vertexStart: number
  readonly vertexCount: number
}

interface DatumPlane {
  readonly key: string
  readonly o: Vector3
  readonly x: Vector3
  readonly y: Vector3
  readonly n: Vector3
}

interface DatumAxis {
  readonly key: string
  readonly a: Vector3
  readonly b: Vector3
}

/**
 * What picking needs from one scene: the drawn parts' BVHs, edges and vertices, the planes and the axes. Built when
 * the outputs change (`new PickIndex(outputs, built)`); it caches the screen positions of every edge point and vertex
 * and recomputes them only when the camera changes, so a pick on every pen move costs one pass over flat arrays.
 * A part the eye hides has no mesh in the scene and so is not here: it cannot be hit.
 */
export class PickIndex {
  private readonly parts: PartEntry[] = []
  private readonly planes: DatumPlane[] = []
  private readonly axes: DatumAxis[] = []
  private readonly planeHalf: number

  // flat tables over every drawn part, in the scene's order
  private edgeFirstPoint: Int32Array
  private edgePointCount: Int32Array
  private edgePart: Int32Array
  private edgeOfPart: Int32Array
  private edgePoints: Float32Array
  private vertexPart: Int32Array
  private vertexOfPart: Int32Array
  private vertexPoints: Float32Array

  // screen cache, valid for `cacheKey`: sx, sy, depth per edge point and vertex, and a screen box per edge
  private cacheKey = ''
  private edgeScreen = new Float64Array(0)
  private vertexScreen = new Float64Array(0)
  private edgeBox = new Float64Array(0)

  constructor(outputs: StudioOutputs, built: Pick<BuiltScene, 'root' | 'stats'>) {
    const byId = new Map<string, Part>()
    const contextIds = new Set<string>()
    for (const p of outputs.parts) byId.set(p.id, p)
    for (const p of outputs.context) {
      if (byId.has(p.id)) continue
      byId.set(p.id, p)
      contextIds.add(p.id)
    }
    const edgeFirst: number[] = []
    const edgeCount: number[] = []
    const edgePart: number[] = []
    const edgeOfPart: number[] = []
    const points: number[] = []
    const vertexPart: number[] = []
    const vertexOfPart: number[] = []
    const vertexPoints: number[] = []
    // the scene lists the studio's parts first and the Context bodies after them
    for (const o of built.root.children) {
      const bvh = o.userData['bvh'] as MeshBVH | undefined
      const id = o.userData['partId'] as string | undefined
      if (!(o instanceof Mesh) || !bvh || id === undefined) continue
      const part = byId.get(id)
      const mesh = part?.mesh
      if (!part || !mesh) continue
      const pi = this.parts.length
      const edgeStart = edgeFirst.length
      for (let e = 0; e < mesh.edges.length; e++) {
        const poly = mesh.edges[e] as Float32Array
        edgeFirst.push(points.length / 3)
        edgeCount.push(Math.floor(poly.length / 3))
        edgePart.push(pi)
        edgeOfPart.push(e)
        for (let k = 0; k < poly.length; k++) points.push(poly[k] as number)
      }
      const vertexStart = vertexPart.length
      for (let v = 0; v * 3 + 2 < mesh.vertices.length; v++) {
        vertexPart.push(pi)
        vertexOfPart.push(v)
        vertexPoints.push(mesh.vertices[3 * v] as number, mesh.vertices[3 * v + 1] as number, mesh.vertices[3 * v + 2] as number)
      }
      this.parts.push({
        id,
        part,
        bvh,
        context: contextIds.has(id),
        triangleFace: mesh.triangleFace,
        edgeStart,
        vertexStart,
        vertexCount: vertexPart.length - vertexStart,
      })
    }
    this.edgeFirstPoint = Int32Array.from(edgeFirst)
    this.edgePointCount = Int32Array.from(edgeCount)
    this.edgePart = Int32Array.from(edgePart)
    this.edgeOfPart = Int32Array.from(edgeOfPart)
    this.edgePoints = Float32Array.from(points)
    this.vertexPart = Int32Array.from(vertexPart)
    this.vertexOfPart = Int32Array.from(vertexOfPart)
    this.vertexPoints = Float32Array.from(vertexPoints)

    this.planeHalf = built.stats.planeHalf
    const frame = (key: string, o: Vec3, x: Vec3, n: Vec3): DatumPlane => {
      const xv = new Vector3(...x).normalize()
      const nv = new Vector3(...n)
      const y = new Vector3().crossVectors(nv, xv).normalize()
      return { key, o: new Vector3(...o), x: xv, y, n: nv.clone().normalize() }
    }
    for (const p of DEFAULT_PLANES) this.planes.push(frame(p.name, p.o, p.x, p.n))
    for (const [key, p] of Object.entries(outputs.planes)) this.planes.push(frame(key, p.o, p.x, p.n))
    for (const [key, a] of Object.entries(outputs.axes)) {
      const [pa, pb] = axisEnds(a.o, a.d, a.span)
      this.axes.push({ key, a: pa, b: pb })
    }
  }

  /** Ids of the parts that can be hit (the drawn ones), in the scene's order. */
  get partIds(): string[] {
    return this.parts.map((p) => p.id)
  }

  /**
   * What is at (x, y), CSS pixels from the view's top left, for this kind of pointer, or null. `filter` limits the kinds
   * considered (default `DEFAULT_KINDS`). With Context bodies in the scene the studio's own geometry goes first, as the
   * desktop's "select through context" does: a face, edge, vertex or part of a studio part wins, otherwise everything is tried.
   */
  pick(view: ViewCamera, x: number, y: number, pointer: PickPointer, filter?: PickFilter): PickHit | null {
    const allowed = new Set<PickKind>(filter ?? DEFAULT_KINDS)
    this.project(view)
    if (this.parts.some((p) => p.context)) {
      const hit = this.pass(view, x, y, pointer, allowed, true)
      if (hit && (hit.kind === 'face' || hit.kind === 'edge' || hit.kind === 'vertex' || hit.kind === 'part')) return hit
    }
    return this.pass(view, x, y, pointer, allowed, false)
  }

  /** The screen position of every edge point and vertex for this camera, cached until the camera changes. */
  private project(view: ViewCamera): void {
    const s = view.state()
    const key = [...s.target, s.scale, ...s.back, ...s.up, s.roll, s.width, s.height].join(',')
    if (key === this.cacheKey) return
    this.cacheKey = key
    const { right, up, back } = view.axes()
    const { target, scale } = view
    const hw = view.width / 2
    const hh = view.height / 2
    const project = (src: Float32Array, out: Float64Array): void => {
      for (let i = 0, o = 0; i + 2 < src.length; i += 3, o += 3) {
        const dx = (src[i] as number) - target.x
        const dy = (src[i + 1] as number) - target.y
        const dz = (src[i + 2] as number) - target.z
        out[o] = hw + (dx * right.x + dy * right.y + dz * right.z) / scale
        out[o + 1] = hh - (dx * up.x + dy * up.y + dz * up.z) / scale
        out[o + 2] = dx * back.x + dy * back.y + dz * back.z
      }
    }
    if (this.edgeScreen.length !== this.edgePoints.length) this.edgeScreen = new Float64Array(this.edgePoints.length)
    if (this.vertexScreen.length !== this.vertexPoints.length) this.vertexScreen = new Float64Array(this.vertexPoints.length)
    project(this.edgePoints, this.edgeScreen)
    project(this.vertexPoints, this.vertexScreen)
    const n = this.edgeFirstPoint.length
    if (this.edgeBox.length !== n * 4) this.edgeBox = new Float64Array(n * 4)
    for (let e = 0; e < n; e++) {
      let x0 = Infinity
      let x1 = -Infinity
      let y0 = Infinity
      let y1 = -Infinity
      const first = this.edgeFirstPoint[e] as number
      for (let k = 0; k < (this.edgePointCount[e] as number); k++) {
        const sx = this.edgeScreen[(first + k) * 3] as number
        const sy = this.edgeScreen[(first + k) * 3 + 1] as number
        if (sx < x0) x0 = sx
        if (sx > x1) x1 = sx
        if (sy < y0) y0 = sy
        if (sy > y1) y1 = sy
      }
      this.edgeBox[e * 4] = x0
      this.edgeBox[e * 4 + 1] = x1
      this.edgeBox[e * 4 + 2] = y0
      this.edgeBox[e * 4 + 3] = y1
    }
  }

  private pass(view: ViewCamera, x: number, y: number, pointer: PickPointer, allowed: ReadonlySet<PickKind>, skipContext: boolean): PickHit | null {
    const tol = TOLERANCE[pointer]
    const { back } = view.axes()
    const depthOf = (p: Vector3): number => p.clone().sub(view.target).dot(back)
    const ray = viewRay(view, x, y)
    const live = (p: PartEntry): boolean => !(skipContext && p.context)

    // the nearest face under the pointer: what hides edges and vertices, and competes with planes
    let face: { part: PartEntry; index: number; point: Vector3; z: number } | null = null
    let faceDistance = Infinity
    let anySolid = false
    if (allowed.has('face') || allowed.has('part') || allowed.has('edge') || allowed.has('vertex')) {
      for (const part of this.parts) {
        if (!live(part)) continue
        const hits = part.bvh.raycast(ray, DoubleSide)
        if (hits.length === 0) continue
        let near = Infinity
        for (const h of hits) if (h.distance < near) near = h.distance
        // among hits level with the nearest (a pointer exactly on an edge between two faces) the lowest face index wins
        let pick: (typeof hits)[number] | null = null
        let pickFace = Infinity
        for (const h of hits) {
          if (h.distance > near + TIE_MM * (1 + near)) continue
          const f = part.triangleFace[h.faceIndex ?? -1] ?? -1
          const rank = f < 0 ? Infinity : f
          if (pick === null || rank < pickFace) {
            pick = h
            pickFace = rank
          }
        }
        if (pick && near < faceDistance - TIE_MM * (1 + near)) {
          faceDistance = near
          anySolid = true
          face = { part, index: pickFace === Infinity ? -1 : pickFace, point: pick.point.clone(), z: depthOf(pick.point) }
        }
      }
    }
    const faceZ = anySolid && face ? face.z : -Infinity
    const levelSlack = view.scale * LEVEL_PX
    // an edge, vertex or axis this far behind the nearest face is hidden by it; a larger tolerance allows more slack
    const hiddenSlack = view.scale * Math.max(LEVEL_PX, (LEVEL_PX / DESKTOP_EDGE_PX) * tol.edge)
    const visible = (z: number): boolean => z >= faceZ - hiddenSlack

    if (allowed.has('vertex')) {
      let best: { d: number; z: number; v: number } | null = null
      const n = this.vertexPart.length
      for (let v = 0; v < n; v++) {
        if (!live(this.parts[this.vertexPart[v] as number] as PartEntry)) continue
        const dx = (this.vertexScreen[v * 3] as number) - x
        const dy = (this.vertexScreen[v * 3 + 1] as number) - y
        const d = Math.hypot(dx, dy)
        if (d > tol.vertex) continue
        const z = this.vertexScreen[v * 3 + 2] as number
        if (!visible(z)) continue
        if (best === null || d < best.d - TIE_PX || (Math.abs(d - best.d) <= TIE_PX && z > best.z + TIE_MM)) best = { d, z, v }
      }
      if (best) {
        const part = this.parts[this.vertexPart[best.v] as number] as PartEntry
        const index = this.vertexOfPart[best.v] as number
        const p = this.vertexPoints
        return {
          kind: 'vertex',
          part: part.id,
          index,
          name: elementName(part.part, 'vertex', index) ?? null,
          point: [p[best.v * 3] as number, p[best.v * 3 + 1] as number, p[best.v * 3 + 2] as number],
          distancePx: best.d,
        }
      }
    }

    if (allowed.has('edge')) {
      let best: { d: number; z: number; e: number; at: number; t: number } | null = null
      const n = this.edgeFirstPoint.length
      const reach = tol.edge
      for (let e = 0; e < n; e++) {
        if (!live(this.parts[this.edgePart[e] as number] as PartEntry)) continue
        if (x < (this.edgeBox[e * 4] as number) - reach || x > (this.edgeBox[e * 4 + 1] as number) + reach) continue
        if (y < (this.edgeBox[e * 4 + 2] as number) - reach || y > (this.edgeBox[e * 4 + 3] as number) + reach) continue
        const first = this.edgeFirstPoint[e] as number
        const count = this.edgePointCount[e] as number
        for (let k = 0; k + 1 < count; k++) {
          const i = (first + k) * 3
          const ax = this.edgeScreen[i] as number
          const ay = this.edgeScreen[i + 1] as number
          const bx = this.edgeScreen[i + 3] as number
          const by = this.edgeScreen[i + 4] as number
          const abx = bx - ax
          const aby = by - ay
          const l2 = abx * abx + aby * aby || 1e-12
          let t = ((x - ax) * abx + (y - ay) * aby) / l2
          t = t < 0 ? 0 : t > 1 ? 1 : t
          const d = Math.hypot(ax + t * abx - x, ay + t * aby - y)
          if (d > reach) continue
          const za = this.edgeScreen[i + 2] as number
          const z = za + t * ((this.edgeScreen[i + 5] as number) - za)
          if (!visible(z)) continue
          if (best === null || d < best.d - TIE_PX || (Math.abs(d - best.d) <= TIE_PX && z > best.z + TIE_MM)) best = { d, z, e, at: i / 3, t }
        }
      }
      if (best) {
        const part = this.parts[this.edgePart[best.e] as number] as PartEntry
        const index = this.edgeOfPart[best.e] as number
        const p = this.edgePoints
        const a = best.at * 3
        const point: Vec3 = [
          (p[a] as number) + best.t * ((p[a + 3] as number) - (p[a] as number)),
          (p[a + 1] as number) + best.t * ((p[a + 4] as number) - (p[a + 1] as number)),
          (p[a + 2] as number) + best.t * ((p[a + 5] as number) - (p[a + 2] as number)),
        ]
        return { kind: 'edge', part: part.id, index, name: elementName(part.part, 'edge', index) ?? null, point, distancePx: best.d }
      }
    }

    if (allowed.has('axis')) {
      let best: { d: number; key: string; point: Vec3 } | null = null
      for (const ax of this.axes) {
        const a = this.screen(view, ax.a)
        const b = this.screen(view, ax.b)
        const abx = b.x - a.x
        const aby = b.y - a.y
        const l2 = abx * abx + aby * aby || 1e-12
        let t = ((x - a.x) * abx + (y - a.y) * aby) / l2
        t = t < 0 ? 0 : t > 1 ? 1 : t
        const d = Math.hypot(a.x + t * abx - x, a.y + t * aby - y)
        if (d > tol.axis || !visible(a.z + t * (b.z - a.z))) continue
        if (best === null || d < best.d - TIE_PX) {
          const q = ax.a.clone().lerp(ax.b, t)
          best = { d, key: ax.key, point: [q.x, q.y, q.z] }
        }
      }
      if (best) return { kind: 'axis', part: null, index: -1, name: null, key: best.key, point: best.point, distancePx: best.d }
    }

    // planes win when they are in front of, or level with, the nearest face
    let plane: { depth: number; key: string; point: Vector3 } | null = null
    if (allowed.has('plane')) {
      const d = ray.direction
      for (const pl of this.planes) {
        const denom = d.dot(pl.n)
        if (Math.abs(denom) < 1e-9) continue
        const t = pl.o.clone().sub(ray.origin).dot(pl.n) / denom
        if (t <= 0) continue
        const q = ray.origin.clone().addScaledVector(d, t)
        const rel = q.clone().sub(pl.o)
        if (Math.abs(rel.dot(pl.x)) <= this.planeHalf && Math.abs(rel.dot(pl.y)) <= this.planeHalf && (plane === null || t < plane.depth - TIE_MM)) {
          plane = { depth: t, key: pl.key, point: q }
        }
      }
    }
    const faceWanted = allowed.has('face') || allowed.has('part')
    if (plane && (face === null || !faceWanted || plane.depth <= faceDistance + levelSlack)) {
      return { kind: 'plane', part: null, index: -1, name: null, key: plane.key, point: [plane.point.x, plane.point.y, plane.point.z], distancePx: null }
    }
    if (face && faceWanted) {
      const point: Vec3 = [face.point.x, face.point.y, face.point.z]
      if (!allowed.has('face')) return { kind: 'part', part: face.part.id, index: -1, name: null, point, distancePx: null }
      if (face.index >= 0) {
        return { kind: 'face', part: face.part.id, index: face.index, name: elementName(face.part.part, 'face', face.index) ?? null, point, distancePx: null }
      }
    }
    return null
  }

  private screen(view: ViewCamera, p: Vector3): { x: number; y: number; z: number } {
    const { right, up, back } = view.axes()
    const d = p.clone().sub(view.target)
    return { x: view.width / 2 + d.dot(right) / view.scale, y: view.height / 2 - d.dot(up) / view.scale, z: d.dot(back) }
  }
}
