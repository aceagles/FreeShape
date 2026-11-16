import {
  BufferAttribute,
  Color,
  BufferGeometry,
  DoubleSide,
  Group,
  LineBasicMaterial,
  LineLoop,
  LineDashedMaterial,
  Line,
  Mesh,
  MeshBasicMaterial,
  PointsMaterial,
  Points,
  Vector3,
} from 'three'
import type { Material, ShaderMaterial } from 'three'
import { LineMaterial } from 'three/addons/lines/LineMaterial.js'
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js'
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js'
import { MeshBVH } from 'three-mesh-bvh'
import { sceneColor, sceneDraw } from '../theme/tokens'
import type { Part, PartMesh, StudioOutputs, Vec3 } from '../wire'
import { edgeColor, rgb } from './colors'
import type { Bounds, Triple } from './camera'
import { MIN_HALF } from './camera'
import { setHighlight, shadedMaterial } from './shading'

export interface SceneStats {
  /** Meshes drawn for the studio's own parts (hidden parts are not drawn). */
  parts: number
  /** The same count under its plain name: parts the studio has that are drawn (not hidden by the eye). */
  visibleParts: number
  /** Parts the studio has that the eye hides: not drawn, no mesh and no BVH in the scene. */
  hiddenParts: number
  /** Ids of the drawn parts, in the studio's order. */
  visiblePartIds: string[]
  /** Colour each drawn part is built with (its base colour, r g b in 0..1; a face override is not in it). */
  partColors: Record<string, number[]>
  /** Half-size of the default planes, mm. */
  planeHalf: number
  /** Reference parts drawn translucent. */
  context: number
  /** Faces of the drawn parts. */
  faces: number
  /** Edge polylines of the drawn parts. */
  edges: number
  triangles: number
  /** Planes drawn: the three default ones plus the studio's own. */
  planes: number
  axes: number
}

/** What the hook says before any scene: nothing drawn. */
export const EMPTY_STATS: SceneStats = {
  parts: 0,
  visibleParts: 0,
  hiddenParts: 0,
  visiblePartIds: [],
  partColors: {},
  planeHalf: 0,
  context: 0,
  faces: 0,
  edges: 0,
  triangles: 0,
  planes: 0,
  axes: 0,
}

export interface SceneOptions {
  /** Half-size the default planes keep when nothing is drawn (something was a moment ago). */
  keepPlaneHalf?: number | null
  /** Part to draw highlighted. */
  selectedPart?: string | null
}

export interface BuiltScene {
  root: Group
  stats: SceneStats
  /** Draw this part highlighted (null: none). Returns whether it is drawn. Slice 4 picks faces with the same tint. */
  select(partId: string | null): boolean
  /** Something is drawn: the camera and the planes have a model to follow. */
  hasModel: boolean
  /** Box round the drawn parts, never tighter than MIN_HALF; a default box when nothing is drawn. */
  bounds: Bounds
  /** The model and the default planes: what a fit frames (see fitBoundsFor). */
  fitBounds: Bounds
  /** Keep line widths and the origin triad right for the viewport size and zoom (mm per pixel). */
  update(width: number, height: number, scale: number): void
  dispose(): void
}

type Rgb = readonly [number, number, number]

/** Triangles of a mesh coloured by the part's colour, or by a face's override. Contiguous runs share a group. */
function faceGroups(part: Part, mesh: PartMesh): { start: number; count: number; color: Rgb }[] {
  const base = colorOf(part)
  const overrides = part.faceColors
  const triangles = mesh.indices.length / 3
  if (!overrides || overrides.size === 0) return [{ start: 0, count: triangles * 3, color: base }]
  const out: { start: number; count: number; color: Rgb }[] = []
  let current: Rgb | null = null
  for (let t = 0; t < triangles; t++) {
    const face = mesh.triangleFace[t] ?? -1
    const c: Rgb = (face >= 0 ? overrides.get(face) : undefined) ?? base
    const last = out[out.length - 1]
    if (last && current && c[0] === current[0] && c[1] === current[1] && c[2] === current[2]) last.count += 3
    else {
      out.push({ start: t * 3, count: 3, color: c })
      current = c
    }
  }
  return out
}

function colorOf(part: Part): Rgb {
  const c = part.color
  return c.length >= 3 ? [c[0] ?? 0, c[1] ?? 0, c[2] ?? 0] : sceneColor.part
}

function partGroup(part: Part, mesh: PartMesh, translucent: boolean, materials: Material[]): Mesh {
  const g = new BufferGeometry()
  g.setAttribute('position', new BufferAttribute(mesh.positions, 3))
  g.setAttribute('normal', new BufferAttribute(mesh.normals, 3))
  g.setIndex(new BufferAttribute(mesh.indices, 1))
  const cache = new Map<string, Material>()
  const matFor = (c: Rgb): number => {
    const key = c.join(',')
    let m = cache.get(key)
    if (!m) {
      m = shadedMaterial(rgb(c), { alpha: translucent ? sceneDraw.contextAlpha : 1 })
      cache.set(key, m)
      materials.push(m)
    }
    return [...cache.keys()].indexOf(key)
  }
  const groups = faceGroups(part, mesh)
  for (const grp of groups) g.addGroup(grp.start, grp.count, matFor(grp.color))
  const list = [...cache.values()]
  const mesh3 = new Mesh(g, list.length === 1 ? list[0] : list)
  // Built once so picking (a later card) can ray-cast it; a part has a few thousand triangles at most.
  mesh3.userData['bvh'] = new MeshBVH(g)
  mesh3.userData['partId'] = part.id
  mesh3.name = `part:${part.id}`
  mesh3.renderOrder = translucent ? 2 : 0
  return mesh3
}

/** Pairs of points of every polyline, as LineSegments2 wants them. */
function segmentPositions(edges: readonly Float32Array[]): Float32Array {
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

function edgeLines(part: Part, mesh: PartMesh, materials: Material[], lineMats: LineMaterial[]): LineSegments2 | null {
  if (mesh.edges.length === 0) return null
  const geo = new LineSegmentsGeometry()
  geo.setPositions(segmentPositions(mesh.edges))
  const mat = new LineMaterial({ linewidth: sceneDraw.edgeWidth })
  mat.color.copy(edgeColor())
  materials.push(mat)
  lineMats.push(mat)
  const lines = new LineSegments2(geo, mat)
  lines.name = `edges:${part.id}`
  lines.userData['partId'] = part.id
  lines.userData['edgeCount'] = mesh.edges.length
  lines.renderOrder = 1
  return lines
}

/** Edge colour and width of a highlighted part: a dark amber, a little wider, so the outline reads on the tint. */
const HIGHLIGHT_EDGE: Rgb = [0.5, 0.3, 0.0]
const HIGHLIGHT_EDGE_WIDTH = sceneDraw.edgeWidth * 1.5

const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]

function planeObjects(o: Vec3, x: Vec3, n: Vec3, half: number, materials: Material[]): Group {
  const xv = new Vector3(...x).normalize()
  const y = cross(n, [xv.x, xv.y, xv.z])
  const yv = new Vector3(...y).normalize()
  const ov = new Vector3(...o)
  const corners = [
    ov.clone().addScaledVector(xv, -half).addScaledVector(yv, -half),
    ov.clone().addScaledVector(xv, half).addScaledVector(yv, -half),
    ov.clone().addScaledVector(xv, half).addScaledVector(yv, half),
    ov.clone().addScaledVector(xv, -half).addScaledVector(yv, half),
  ]
  const pos = new Float32Array(corners.flatMap((c) => [c.x, c.y, c.z]))
  const fill = new BufferGeometry()
  fill.setAttribute('position', new BufferAttribute(pos, 3))
  fill.setIndex([0, 1, 2, 0, 2, 3])
  const fm = new MeshBasicMaterial({
    color: rgb(sceneColor.plane),
    transparent: true,
    opacity: sceneDraw.planeAlpha,
    depthWrite: false,
    side: DoubleSide,
  })
  const om = new LineBasicMaterial({ color: rgb(sceneColor.planeOutline), transparent: true, opacity: sceneDraw.planeOutlineAlpha, depthWrite: false })
  materials.push(fm, om)
  const outline = new BufferGeometry()
  outline.setAttribute('position', new BufferAttribute(pos.slice(), 3))
  const g = new Group()
  const f = new Mesh(fill, fm)
  const l = new LineLoop(outline, om)
  f.renderOrder = l.renderOrder = 3
  g.add(f, l)
  return g
}

function axisLine(o: Vec3, d: Vec3, span: readonly [number, number] | null, materials: Material[]): Line {
  let t0 = -sceneDraw.planeHalf
  let t1 = sceneDraw.planeHalf
  if (span && span[1] - span[0] > 1e-9) {
    const pad = 0.1 * (span[1] - span[0])
    t0 = span[0] - pad
    t1 = span[1] + pad
  }
  const dv = new Vector3(...d).normalize()
  const a = new Vector3(...o).addScaledVector(dv, t0)
  const b = new Vector3(...o).addScaledVector(dv, t1)
  const g = new BufferGeometry().setFromPoints([a, b])
  const c = rgb(sceneColor.plane).multiplyScalar(0.8)
  const m = new LineDashedMaterial({ color: c, dashSize: 6, gapSize: 3, depthWrite: false })
  materials.push(m)
  const line = new Line(g, m)
  line.computeLineDistances()
  line.renderOrder = 4
  return line
}

/** The three axes and the origin dot, drawn on top of everything, one unit long (scaled to 28 px per frame). */
function triadObject(materials: Material[]): Group {
  const g = new Group()
  g.name = 'origin'
  const axes: [Rgb, Triple][] = [
    [sceneColor.axisX, [1, 0, 0]],
    [sceneColor.axisY, [0, 1, 0]],
    [sceneColor.axisZ, [0, 0, 1]],
  ]
  for (const [c, d] of axes) {
    const m = new LineBasicMaterial({ color: rgb(c), depthTest: false, depthWrite: false })
    materials.push(m)
    const geo = new BufferGeometry().setFromPoints([new Vector3(0, 0, 0), new Vector3(...d)])
    const l = new Line(geo, m)
    l.renderOrder = 6
    g.add(l)
  }
  const dm = new PointsMaterial({ color: rgb(sceneColor.originDot), size: 5, sizeAttenuation: false, depthTest: false })
  materials.push(dm)
  const dot = new Points(new BufferGeometry().setFromPoints([new Vector3(0, 0, 0)]), dm)
  dot.renderOrder = 7
  g.add(dot)
  return g
}

function boundsOf(parts: readonly Part[]): Bounds {
  const lo: [number, number, number] = [Infinity, Infinity, Infinity]
  const hi: [number, number, number] = [-Infinity, -Infinity, -Infinity]
  for (const p of parts) {
    const pos = p.mesh?.positions
    if (!pos) continue
    for (let i = 0; i + 2 < pos.length; i += 3) {
      for (let k = 0; k < 3; k++) {
        const v = pos[i + k] as number
        if (v < (lo[k] as number)) lo[k] = v
        if (v > (hi[k] as number)) hi[k] = v
      }
    }
  }
  if (!lo.every(Number.isFinite)) {
    const h = sceneDraw.planeHalf
    return { lo: [-h, -h, -h], hi: [h, h, h] }
  }
  // Never zoom in tighter than a few millimetres (a single point, a thin sheet).
  const centre = lo.map((v, k) => (v + (hi[k] as number)) / 2)
  const half = lo.map((v, k) => Math.max(((hi[k] as number) - v) / 2, 0))
  const m = Math.max(...half) < MIN_HALF
  const h = m ? [MIN_HALF, MIN_HALF, MIN_HALF] : half
  return {
    lo: [centre[0]! - h[0]!, centre[1]! - h[1]!, centre[2]! - h[2]!],
    hi: [centre[0]! + h[0]!, centre[1]! + h[1]!, centre[2]! + h[2]!],
  }
}

/**
 * Half-size of the default planes. The desktop draws them 150 mm square whatever the model size; fitted into a
 * tablet view that fills the screen and clips at its edges, so here they scale with the model: 0.4 times its longest side (so they stay inside the fitted view),
 * never under sceneDraw.planeMin, and the desktop's size when nothing is drawn. buildScene decides what to measure
 * when nothing is drawn: see `planeHalfOf`.
 */
export function planeHalfFor(b: Bounds): number {
  const longest = Math.max(b.hi[0] - b.lo[0], b.hi[1] - b.lo[1], b.hi[2] - b.lo[2])
  return Math.max(longest * 0.4, sceneDraw.planeMin)
}

/**
 * What the camera fits: the model and the default planes, which sit at the origin and are drawn `half` either side of
 * it, so a model that does not straddle the origin (the Hinge's parts) does not push a plane off the view. When the
 * origin is so far from the model that the union's longest side is over 2.5 times the model's, fitting both would
 * shrink the model too much: the planes then run off the view and the model alone is fitted.
 */
export function fitBoundsFor(model: Bounds, half: number): Bounds {
  const lo = model.lo.map((v) => Math.min(v, -half)) as unknown as Bounds['lo']
  const hi = model.hi.map((v) => Math.max(v, half)) as unknown as Bounds['hi']
  const side = (b: Bounds) => Math.max(b.hi[0] - b.lo[0], b.hi[1] - b.lo[1], b.hi[2] - b.lo[2])
  const union = { lo, hi }
  return side(union) <= 2.5 * side(model) ? union : model
}

const DEFAULT_PLANES: readonly { name: string; o: Vec3; x: Vec3; n: Vec3 }[] = [
  { name: 'Top', o: [0, 0, 0], x: [1, 0, 0], n: [0, 0, 1] },
  { name: 'Front', o: [0, 0, 0], x: [1, 0, 0], n: [0, -1, 0] },
  { name: 'Right', o: [0, 0, 0], x: [0, 1, 0], n: [1, 0, 0] },
]

/**
 * Build the three.js scene of a Part Studio: a shaded mesh and a wide-line object per drawn part, the three default
 * planes, the studio's own planes and axes, and the origin. Typed-array views of the response are used in place.
 * Draw order follows viewport.py _draw_scene: faces, edges, Context bodies, translucent planes, axes, origin.
 */
export function buildScene(outputs: StudioOutputs, opts: SceneOptions = {}): BuiltScene {
  const root = new Group()
  root.name = 'studio'
  const materials: Material[] = []
  const lineMats: LineMaterial[] = []
  const geometries: BufferGeometry[] = []
  const stats: SceneStats = { ...EMPTY_STATS, visiblePartIds: [], partColors: {} }
  /** Per drawn part: what the highlight changes. */
  const looks = new Map<string, { shaders: ShaderMaterial[]; line: LineMaterial | null; edge: Color }>()

  const drawn = outputs.parts.filter((p) => !p.hidden && p.mesh)
  for (const [list, translucent] of [
    [drawn, false],
    [outputs.context.filter((p) => !p.hidden && p.mesh), true],
  ] as const) {
    for (const part of list) {
      const mesh = part.mesh as PartMesh
      const m = partGroup(part, mesh, translucent, materials)
      geometries.push(m.geometry)
      root.add(m)
      const lines = edgeLines(part, mesh, materials, lineMats)
      if (lines) {
        geometries.push(lines.geometry)
        root.add(lines)
      }
      if (translucent) stats.context++
      else {
        const list = Array.isArray(m.material) ? m.material : [m.material]
        const line = lines ? (lines.material as LineMaterial) : null
        looks.set(part.id, { shaders: list as ShaderMaterial[], line, edge: line ? line.color.clone() : new Color() })
        stats.parts++
        stats.visibleParts++
        stats.visiblePartIds.push(part.id)
        stats.partColors[part.id] = [...colorOf(part)]
        stats.faces += mesh.faceRanges.length
        stats.edges += mesh.edges.length
        stats.triangles += mesh.indices.length / 3
      }
    }
  }

  stats.hiddenParts = outputs.parts.filter((p) => p.hidden).length
  const bounds = boundsOf(drawn)
  const hasModel = drawn.length > 0
  // an emptied studio (every part gone, or hidden by the eye) keeps the planes' last size: the camera is still
  // zoomed for the model that was there. A studio that opens empty gets the default box and the desktop's size.
  const half = hasModel || opts.keepPlaneHalf == null ? planeHalfFor(bounds) : opts.keepPlaneHalf
  stats.planeHalf = half
  const planes = new Group()
  planes.name = 'planes'
  for (const p of DEFAULT_PLANES) planes.add(planeObjects(p.o, p.x, p.n, half, materials))
  for (const p of Object.values(outputs.planes)) planes.add(planeObjects(p.o, p.x, p.n, half, materials))
  stats.planes = planes.children.length
  root.add(planes)
  for (const a of Object.values(outputs.axes)) {
    const l = axisLine(a.o, a.d, a.span, materials)
    geometries.push(l.geometry)
    root.add(l)
    stats.axes++
  }
  const triad = triadObject(materials)
  root.add(triad)

  const select = (partId: string | null): boolean => {
    let found = false
    for (const [id, look] of looks) {
      const on = id === partId
      found ||= on
      for (const m of look.shaders) setHighlight(m, on)
      if (look.line) {
        look.line.color.copy(on ? rgb(HIGHLIGHT_EDGE) : look.edge)
        look.line.linewidth = on ? HIGHLIGHT_EDGE_WIDTH : sceneDraw.edgeWidth
      }
    }
    return found
  }
  select(opts.selectedPart ?? null)

  return {
    root,
    stats,
    select,
    hasModel,
    bounds,
    fitBounds: hasModel ? fitBoundsFor(bounds, half) : bounds,
    update(width, height, scale) {
      for (const m of lineMats) m.resolution.set(width, height)
      triad.scale.setScalar(scale * sceneDraw.triad)
    },
    dispose() {
      for (const g of geometries) g.dispose()
      root.traverse((o) => {
        if (o instanceof Mesh || o instanceof Line || o instanceof Points) o.geometry.dispose()
      })
      for (const m of materials) m.dispose()
    },
  }
}
