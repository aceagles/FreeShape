import { DoubleSide, Matrix4, Vector3 } from 'three'
import type { MeshBVH } from 'three-mesh-bvh'
import { elementName } from '../wire'
import type { AssemblyOutputs, Part, Vec3 } from '../wire'
import type { ViewCamera } from '../viewport/camera'
import { TOLERANCE, viewRay } from '../viewport/pick'
import type { ElementPick, PickFilter, PickKind, PickPointer } from '../viewport/pick'
import type { AssemblyScene } from './scene'

/**
 * Picking in an Assembly: the face, edge or vertex under a view position, with the instance it is on. The rules are
 * the Part Studio's (viewport/pick.ts: vertex, then edge, then face; an edge or vertex behind the nearest face is hidden by
 * it; the pointer's tolerance), over the drawn instances in world coordinates. Two instances of one part have the same
 * Element names, so a hit is told apart by `instance`; `part` is the part's hash (the key into `outputs.parts`).
 * An instance the eye hides is not in the scene and cannot be hit. No planes, axes or whole-part picks here.
 */
interface Entry {
  readonly instance: string
  readonly hash: string
  readonly part: Part
  readonly bvh: MeshBVH
  readonly inverse: Matrix4
  readonly matrix: Matrix4
}

const TIE_MM = 1e-7
const LEVEL_PX = 12
const DESKTOP_EDGE_PX = 7
export const ASSEMBLY_KINDS: readonly PickKind[] = ['face', 'edge', 'vertex']

export class AssemblyPickIndex {
  private readonly entries: Entry[] = []
  /** World-space polyline points (x y z each) of all drawn edges, and per edge: its entry, its index in the part, first point, point count. */
  private edgePoints: Float32Array
  private readonly edges: { entry: number; index: number; first: number; count: number }[] = []
  private vertexPoints: Float32Array
  private readonly vertices: { entry: number; index: number }[] = []
  private cacheKey = ''
  private edgeScreen = new Float64Array(0)
  private vertexScreen = new Float64Array(0)

  constructor(outputs: AssemblyOutputs, built: Pick<AssemblyScene, 'objects'>) {
    const ep: number[] = []
    const vp: number[] = []
    const world = new Vector3()
    for (const inst of outputs.instances) {
      const obj = built.objects.get(inst.id)
      const bvh = obj?.userData['bvh'] as MeshBVH | undefined
      const hash = inst.part
      const part = hash === null ? undefined : outputs.parts[hash]
      const mesh = part?.mesh
      if (!obj || !bvh || hash === null || !part || !mesh) continue
      const ei = this.entries.length
      this.entries.push({ instance: inst.id, hash, part, bvh, matrix: obj.matrix.clone(), inverse: obj.matrix.clone().invert() })
      const m = obj.matrix
      for (let e = 0; e < mesh.edges.length; e++) {
        const poly = mesh.edges[e] as Float32Array
        this.edges.push({ entry: ei, index: e, first: ep.length / 3, count: Math.floor(poly.length / 3) })
        for (let k = 0; k + 2 < poly.length; k += 3) {
          world.set(poly[k] as number, poly[k + 1] as number, poly[k + 2] as number).applyMatrix4(m)
          ep.push(world.x, world.y, world.z)
        }
      }
      for (let v = 0; v * 3 + 2 < mesh.vertices.length; v++) {
        this.vertices.push({ entry: ei, index: v })
        world.set(mesh.vertices[3 * v] as number, mesh.vertices[3 * v + 1] as number, mesh.vertices[3 * v + 2] as number).applyMatrix4(m)
        vp.push(world.x, world.y, world.z)
      }
    }
    this.edgePoints = Float32Array.from(ep)
    this.vertexPoints = Float32Array.from(vp)
  }

  /** Ids of the instances that can be hit, in the Assembly's order. */
  get instanceIds(): string[] {
    return this.entries.map((e) => e.instance)
  }

  private project(view: ViewCamera): void {
    const s = view.state()
    const key = [...s.target, s.scale, ...s.back, ...s.up, s.roll, s.width, s.height].join(',')
    if (key === this.cacheKey) return
    this.cacheKey = key
    const { right, up, back } = view.axes()
    const { target, scale } = view
    const hw = view.width / 2
    const hh = view.height / 2
    const run = (src: Float32Array, out: Float64Array): void => {
      for (let i = 0; i + 2 < src.length; i += 3) {
        const dx = (src[i] as number) - target.x
        const dy = (src[i + 1] as number) - target.y
        const dz = (src[i + 2] as number) - target.z
        out[i] = hw + (dx * right.x + dy * right.y + dz * right.z) / scale
        out[i + 1] = hh - (dx * up.x + dy * up.y + dz * up.z) / scale
        out[i + 2] = dx * back.x + dy * back.y + dz * back.z
      }
    }
    if (this.edgeScreen.length !== this.edgePoints.length) this.edgeScreen = new Float64Array(this.edgePoints.length)
    if (this.vertexScreen.length !== this.vertexPoints.length) this.vertexScreen = new Float64Array(this.vertexPoints.length)
    run(this.edgePoints, this.edgeScreen)
    run(this.vertexPoints, this.vertexScreen)
  }

  /** What is at (x, y), CSS pixels from the view's top left, for this kind of pointer, or null. */
  pick(view: ViewCamera, x: number, y: number, pointer: PickPointer, filter?: PickFilter): ElementPick | null {
    const allowed = new Set<PickKind>(filter ?? ASSEMBLY_KINDS)
    this.project(view)
    const tol = TOLERANCE[pointer]
    const { back } = view.axes()
    const ray = viewRay(view, x, y)

    // the nearest face under the pointer: what hides edges and vertices behind it
    let face: { entry: Entry; index: number; point: Vector3; z: number } | null = null
    let faceDistance = Infinity
    for (const entry of this.entries) {
      const hits = entry.bvh.raycast(ray.clone().applyMatrix4(entry.inverse), DoubleSide)
      if (hits.length === 0) continue
      let near = Infinity
      for (const h of hits) if (h.distance < near) near = h.distance
      let best: (typeof hits)[number] | null = null
      let bestFace = Infinity
      for (const h of hits) {
        if (h.distance > near + TIE_MM * (1 + near)) continue
        const f = entry.part.mesh?.triangleFace[h.faceIndex ?? -1] ?? -1
        const rank = f < 0 ? Infinity : f
        if (best === null || rank < bestFace) {
          best = h
          bestFace = rank
        }
      }
      if (best && near < faceDistance - TIE_MM * (1 + near)) {
        faceDistance = near
        const point = best.point.clone().applyMatrix4(entry.matrix)
        face = { entry, index: bestFace === Infinity ? -1 : bestFace, point, z: point.clone().sub(view.target).dot(back) }
      }
    }
    const faceZ = face ? face.z : -Infinity
    const hiddenSlack = view.scale * Math.max(LEVEL_PX, (LEVEL_PX / DESKTOP_EDGE_PX) * tol.edge)
    const visible = (z: number): boolean => z >= faceZ - hiddenSlack
    const hit = (kind: 'face' | 'edge' | 'vertex', entry: Entry, index: number, point: Vec3, distancePx: number | null): ElementPick => ({
      kind,
      part: entry.hash,
      instance: entry.instance,
      index,
      name: elementName(entry.part, kind, index) ?? null,
      point,
      distancePx,
    })

    if (allowed.has('vertex')) {
      let best: { d: number; z: number; v: number } | null = null
      for (let v = 0; v < this.vertices.length; v++) {
        const d = Math.hypot((this.vertexScreen[v * 3] as number) - x, (this.vertexScreen[v * 3 + 1] as number) - y)
        if (d > tol.vertex) continue
        const z = this.vertexScreen[v * 3 + 2] as number
        if (!visible(z)) continue
        if (best === null || d < best.d - 1e-6 || (Math.abs(d - best.d) <= 1e-6 && z > best.z + TIE_MM)) best = { d, z, v }
      }
      if (best) {
        const rec = this.vertices[best.v] as { entry: number; index: number }
        const p = this.vertexPoints
        return hit('vertex', this.entries[rec.entry] as Entry, rec.index, [p[best.v * 3] as number, p[best.v * 3 + 1] as number, p[best.v * 3 + 2] as number], best.d)
      }
    }

    if (allowed.has('edge')) {
      let best: { d: number; z: number; e: number; at: number; t: number } | null = null
      for (let e = 0; e < this.edges.length; e++) {
        const { first, count } = this.edges[e] as { first: number; count: number }
        for (let k = 0; k + 1 < count; k++) {
          const i = (first + k) * 3
          const ax = this.edgeScreen[i] as number
          const ay = this.edgeScreen[i + 1] as number
          const abx = (this.edgeScreen[i + 3] as number) - ax
          const aby = (this.edgeScreen[i + 4] as number) - ay
          const l2 = abx * abx + aby * aby || 1e-12
          let t = ((x - ax) * abx + (y - ay) * aby) / l2
          t = t < 0 ? 0 : t > 1 ? 1 : t
          const d = Math.hypot(ax + t * abx - x, ay + t * aby - y)
          if (d > tol.edge) continue
          const za = this.edgeScreen[i + 2] as number
          const z = za + t * ((this.edgeScreen[i + 5] as number) - za)
          if (!visible(z)) continue
          if (best === null || d < best.d - 1e-6 || (Math.abs(d - best.d) <= 1e-6 && z > best.z + TIE_MM)) best = { d, z, e, at: i, t }
        }
      }
      if (best) {
        const rec = this.edges[best.e] as { entry: number; index: number }
        const p = this.edgePoints
        const a = best.at
        const point: Vec3 = [
          (p[a] as number) + best.t * ((p[a + 3] as number) - (p[a] as number)),
          (p[a + 1] as number) + best.t * ((p[a + 4] as number) - (p[a + 1] as number)),
          (p[a + 2] as number) + best.t * ((p[a + 5] as number) - (p[a + 2] as number)),
        ]
        return hit('edge', this.entries[rec.entry] as Entry, rec.index, point, best.d)
      }
    }

    if (face && allowed.has('face') && face.index >= 0) return hit('face', face.entry, face.index, [face.point.x, face.point.y, face.point.z], null)
    return null
  }
}
