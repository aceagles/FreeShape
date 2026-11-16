import { BufferAttribute, BufferGeometry, Color, Group, Matrix4, Mesh } from 'three'
import type { Material, ShaderMaterial } from 'three'
import { LineMaterial } from 'three/addons/lines/LineMaterial.js'
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js'
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js'
import { MeshBVH } from 'three-mesh-bvh'
import { sceneColor, sceneDraw } from '../theme/tokens'
import type { AssemblyOutputs, InstanceEntry, Part, PartMesh } from '../wire'
import { rgb } from '../viewport/colors'
import type { Bounds } from '../viewport/camera'
import { MIN_HALF } from '../viewport/camera'
import { LookOverlay } from '../viewport/looks'
import type { LookItem } from '../viewport/looks'
import { setLook, shadedMaterial } from '../viewport/shading'

/** What the test hook reports about the Assembly on screen. */
export interface AssemblyStats {
  /** Instances the Assembly has. */
  instances: number
  /** Instances drawn: not hidden by the eye and with a mesh. */
  drawn: number
  /** Instances the eye hides (not drawn, no object in the scene). */
  hidden: number
  /** Distinct geometries uploaded: one per part, however many instances show it. */
  geometries: number
  /** Ids of the drawn instances, in the Assembly's order. */
  drawnIds: string[]
  /** Triangles over every drawn instance. */
  triangles: number
}

export const EMPTY_ASSEMBLY_STATS: AssemblyStats = { instances: 0, drawn: 0, hidden: 0, geometries: 0, drawnIds: [], triangles: 0 }

export interface AssemblyScene {
  root: Group
  stats: AssemblyStats
  /** The drawn object of each drawn instance, by instance id. */
  objects: ReadonlyMap<string, Mesh>
  hasModel: boolean
  bounds: Bounds
  /** Draw these instances' items as selected and this one as Aimed (see viewport/looks.ts; `part` of a look item is the instance id). Returns the ids of instances drawn whole-selected. */
  setLook(selected: readonly LookItem[], aimed: LookItem | null): Set<string>
  update(width: number, height: number): void
  dispose(): void
}

/** An instance's 4 x 4 placement (row-major rows) as a three.js matrix. */
export function placement(inst: Pick<InstanceEntry, 'T'>): Matrix4 {
  const t = inst.T
  const r = (i: number): number[] => t[i] as number[]
  return new Matrix4().set(
    r(0)[0]!, r(0)[1]!, r(0)[2]!, r(0)[3]!,
    r(1)[0]!, r(1)[1]!, r(1)[2]!, r(1)[3]!,
    r(2)[0]!, r(2)[1]!, r(2)[2]!, r(2)[3]!,
    r(3)[0]!, r(3)[1]!, r(3)[2]!, r(3)[3]!,
  )
}

function geometryOf(mesh: PartMesh): BufferGeometry {
  const g = new BufferGeometry()
  g.setAttribute('position', new BufferAttribute(mesh.positions, 3))
  g.setAttribute('normal', new BufferAttribute(mesh.normals, 3))
  g.setIndex(new BufferAttribute(mesh.indices, 1))
  return g
}

function segments(edges: readonly Float32Array[]): Float32Array {
  let n = 0
  for (const e of edges) n += Math.max(e.length / 3 - 1, 0)
  const out = new Float32Array(n * 6)
  let o = 0
  for (const e of edges) {
    for (let i = 0; i + 5 < e.length; i += 3) {
      out.set(e.subarray(i, i + 6), o)
      o += 6
    }
  }
  return out
}

const HIGHLIGHT_EDGE: readonly [number, number, number] = [0.5, 0.3, 0.0]
const HIGHLIGHT_EDGE_WIDTH = sceneDraw.edgeWidth * 1.5

function boundsOfBoxes(boxes: readonly (readonly [number, number, number])[]): Bounds {
  const lo: [number, number, number] = [Infinity, Infinity, Infinity]
  const hi: [number, number, number] = [-Infinity, -Infinity, -Infinity]
  for (const p of boxes) {
    for (let k = 0; k < 3; k++) {
      lo[k] = Math.min(lo[k]!, p[k]!)
      hi[k] = Math.max(hi[k]!, p[k]!)
    }
  }
  if (!lo.every(Number.isFinite)) {
    const h = sceneDraw.planeHalf
    return { lo: [-h, -h, -h], hi: [h, h, h] }
  }
  const half = lo.map((v, k) => (hi[k]! - v) / 2)
  const centre = lo.map((v, k) => (v + hi[k]!) / 2)
  const h = Math.max(...half) < MIN_HALF ? [MIN_HALF, MIN_HALF, MIN_HALF] : half
  return {
    lo: [centre[0]! - h[0]!, centre[1]! - h[1]!, centre[2]! - h[2]!],
    hi: [centre[0]! + h[0]!, centre[1]! + h[1]!, centre[2]! + h[2]!],
  }
}

/** The 8 corners of a part mesh's own bounding box. */
function localCorners(mesh: PartMesh): [number, number, number][] {
  const p = mesh.positions
  const lo = [Infinity, Infinity, Infinity]
  const hi = [-Infinity, -Infinity, -Infinity]
  for (let i = 0; i + 2 < p.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      const v = p[i + k] as number
      if (v < lo[k]!) lo[k] = v
      if (v > hi[k]!) hi[k] = v
    }
  }
  const out: [number, number, number][] = []
  for (const x of [lo[0]!, hi[0]!]) for (const y of [lo[1]!, hi[1]!]) for (const z of [lo[2]!, hi[2]!]) out.push([x, y, z])
  return out
}

/**
 * Build the three.js scene of an Assembly: for each distinct part ONE geometry (with its BVH and edge segments), and for
 * each instance that is not hidden a Mesh and an edge-line object that share it and carry the instance's placement as their
 * matrix. Hidden instances have no object. Typed-array views of the response are used in place.
 */
export function buildAssemblyScene(outputs: AssemblyOutputs): AssemblyScene {
  const root = new Group()
  root.name = 'assembly'
  const materials: Material[] = []
  const lineMats: LineMaterial[] = []
  const geometries = new Map<string, { geometry: BufferGeometry; bvh: MeshBVH; edges: LineSegmentsGeometry | null; part: Part }>()
  const objects = new Map<string, Mesh>()
  const looks = new Map<string, { shaders: ShaderMaterial[]; line: LineMaterial | null; edge: Color }>()
  const stats: AssemblyStats = { ...EMPTY_ASSEMBLY_STATS, drawnIds: [], instances: outputs.instances.length }
  const corners: [number, number, number][] = []
  const overlays = new Map<string, LookOverlay>()
  const placed = new Map<string, Matrix4>()
  let size: [number, number] = [1, 1]

  for (const inst of outputs.instances) {
    if (inst.hidden) {
      stats.hidden++
      continue
    }
    const part = inst.part === null ? undefined : outputs.parts[inst.part]
    const mesh = part?.mesh
    if (!part || !mesh || inst.part === null) continue
    let shared = geometries.get(inst.part)
    if (!shared) {
      const geometry = geometryOf(mesh)
      // `indirect` keeps the index buffer as the server sent it, so a hit's faceIndex is the triangle's own number
      const bvh = new MeshBVH(geometry, { indirect: true })
      let edges: LineSegmentsGeometry | null = null
      if (mesh.edges.length > 0) {
        edges = new LineSegmentsGeometry()
        edges.setPositions(segments(mesh.edges))
      }
      shared = { geometry, bvh, edges, part }
      geometries.set(inst.part, shared)
    }
    const matrix = placement(inst)
    placed.set(inst.id, matrix)
    const mat = shadedMaterial(rgb(inst.color.length >= 3 ? inst.color : sceneColor.part))
    materials.push(mat)
    const m = new Mesh(shared.geometry, mat)
    m.matrixAutoUpdate = false
    m.matrix.copy(matrix)
    m.userData['bvh'] = shared.bvh
    m.userData['instanceId'] = inst.id
    m.userData['partHash'] = inst.part
    m.name = `instance:${inst.id}`
    root.add(m)
    objects.set(inst.id, m)
    let line: LineMaterial | null = null
    if (shared.edges) {
      line = new LineMaterial({ linewidth: sceneDraw.edgeWidth })
      line.color.copy(rgb(sceneColor.edge))
      materials.push(line)
      lineMats.push(line)
      const lines = new LineSegments2(shared.edges, line)
      lines.matrixAutoUpdate = false
      lines.matrix.copy(matrix)
      lines.renderOrder = 1
      lines.name = `edges:${inst.id}`
      root.add(lines)
    }
    looks.set(inst.id, { shaders: [mat], line, edge: line ? line.color.clone() : new Color() })
    stats.drawn++
    stats.drawnIds.push(inst.id)
    stats.triangles += mesh.indices.length / 3
    for (const c of localCorners(mesh)) {
      const v = [c[0], c[1], c[2], 1]
      const e = matrix.elements
      corners.push([
        e[0]! * v[0]! + e[4]! * v[1]! + e[8]! * v[2]! + e[12]!,
        e[1]! * v[0]! + e[5]! * v[1]! + e[9]! * v[2]! + e[13]!,
        e[2]! * v[0]! + e[6]! * v[1]! + e[10]! * v[2]! + e[14]!,
      ])
    }
  }
  stats.geometries = geometries.size

  const overlayOf = (id: string): LookOverlay | null => {
    let o = overlays.get(id)
    if (o) return o
    const m = objects.get(id)
    const g = m ? geometries.get(m.userData['partHash'] as string) : undefined
    if (!m || !g) return null
    o = new LookOverlay({ parts: new Map([[id, { part: g.part, geometry: g.geometry }]]), planes: new Map(), axes: new Map() })
    o.root.matrixAutoUpdate = false
    o.root.matrix.copy(placed.get(id)!)
    o.update(size[0], size[1])
    overlays.set(id, o)
    root.add(o.root)
    return o
  }

  return {
    root,
    stats,
    objects,
    hasModel: stats.drawn > 0,
    bounds: boundsOfBoxes(corners),
    setLook(selected, aimed) {
      const whole = new Set<string>()
      let hover: string | null = null
      const ids = new Set<string>([...selected.map((i) => i.part ?? ''), ...(aimed?.part ? [aimed.part] : [])])
      for (const id of [...overlays.keys()]) if (!ids.has(id)) overlays.get(id)!.set([], null)
      for (const id of ids) {
        const o = overlayOf(id)
        if (!o) continue
        const on = o.set(
          selected.filter((i) => i.part === id),
          aimed?.part === id ? aimed : null,
        )
        if (on.select.has(id)) whole.add(id)
        if (on.hover === id) hover = id
      }
      for (const [id, look] of looks) {
        const state = id === hover ? 'hover' : whole.has(id) ? 'select' : null
        for (const m of look.shaders) setLook(m, state)
        if (look.line) {
          look.line.color.copy(state ? rgb(HIGHLIGHT_EDGE) : look.edge)
          look.line.linewidth = state ? HIGHLIGHT_EDGE_WIDTH : sceneDraw.edgeWidth
        }
      }
      return whole
    },
    update(width, height) {
      size = [width, height]
      for (const m of lineMats) m.resolution.set(width, height)
      for (const o of overlays.values()) o.update(width, height)
    },
    dispose() {
      for (const o of overlays.values()) o.dispose()
      for (const g of geometries.values()) {
        g.geometry.dispose()
        g.edges?.dispose()
      }
      for (const m of materials) m.dispose()
    },
  }
}
