import { BufferAttribute, BufferGeometry, DataTexture, DoubleSide, Group, Mesh, MeshBasicMaterial, Points, PointsMaterial, RGBAFormat, UnsignedByteType } from 'three'
import type { Material, Vector3 } from 'three'
import { LineMaterial } from 'three/addons/lines/LineMaterial.js'
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js'
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js'
import { color, sceneColor } from '../theme/tokens'
import type { Part } from '../wire'
import { rgb } from './colors'
import type { DrawnRegion } from './sketchRegions'
import { regionKey } from './sketchRegions'
import { setLook, shadedMaterial } from './shading'
import type { Look } from './shading'

/**
 * What the selection and the Aim look like in the view (card I4.2). The drawn model is never touched: each look is a
 * small overlay of its own (a face's triangles, a thick line, a dot, a tinted plane) that is rebuilt whenever the
 * selection or the Aim changes, and dropped again afterwards.
 *
 * Two looks. SELECTED is the selection colour (amber, #ffc740) with a dark brown outline: a face gets an outlined
 * boundary, an edge a dark casing under a thick amber line, a vertex a dark ring round an amber dot. AIMED (the item under the
 * pen before it lifts) is the hover colour (orange) with no dark casing: an orange face with an orange outline, a single thick
 * orange line, a bigger orange dot. The dark casing (and the golden fill) tell the two apart when the colours are close; an
 * item that is both selected and Aimed draws as Aimed.
 */

/** What the overlay needs to know about an item (the selection store's items have these fields). */
export interface LookItem {
  readonly kind: 'face' | 'edge' | 'vertex' | 'part' | 'plane' | 'axis' | 'region' | 'connector' | 'canvas'
  readonly part: string | null
  /** 0-based index of a face, edge or vertex; -1 otherwise. */
  readonly index: number
  /** Feature id of a plane or axis (Top, Front, Right for the default planes). */
  readonly key?: string | null
}

export const lookKey = (i: LookItem): string => `${i.kind}|${i.part ?? ''}|${i.index}|${i.key ?? ''}`

/** The drawn things an overlay is made from. */
export interface LookSources {
  parts: ReadonlyMap<string, { part: Part; geometry: BufferGeometry }>
  planes: ReadonlyMap<string, readonly Vector3[]>
  axes: ReadonlyMap<string, readonly [Vector3, Vector3]>
  /** The regions of the sketches on show, by `regionKey` (a Part Studio's scene only). */
  regions?: ReadonlyMap<string, DrawnRegion>
  /** The corners of the canvases drawn, by feature id (card I12.5). */
  canvases?: ReadonlyMap<string, readonly Vector3[]>
}

/** Line widths and dot sizes, in CSS pixels. Chosen to read on a tablet from arm's length with a pen tip in the way. */
export const LOOK_SIZE = {
  edgeCore: 5,
  edgeCasing: 9,
  edgeHover: 7.5,
  faceOutline: 3,
  vertexCore: 14,
  vertexCasing: 21,
  vertexHover: 18,
  planeOutline: 3.5,
} as const

const CASING: readonly number[] = [0.35, 0.2, 0.0]

let disc: DataTexture | null = null
/** A filled circle with a soft one-pixel rim, made in code so no canvas is needed (point sprites are squares otherwise). */
function discTexture(): DataTexture {
  if (disc) return disc
  const n = 32
  const data = new Uint8Array(n * n * 4)
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const d = Math.hypot(x + 0.5 - n / 2, y + 0.5 - n / 2)
      const i = (y * n + x) * 4
      data.set([255, 255, 255, d <= n / 2 - 0.5 ? 255 : 0], i)
    }
  }
  disc = new DataTexture(data, n, n, RGBAFormat, UnsignedByteType)
  disc.needsUpdate = true
  return disc
}

/** The overlay of a built scene: set what is selected and Aimed, keep the line materials sized to the view. */
export class LookOverlay {
  readonly root = new Group()
  private width = 1
  private height = 1
  private readonly lines: LineMaterial[] = []
  private readonly materials: Material[] = []

  constructor(private readonly sources: LookSources) {
    this.root.name = 'looks'
  }

  /** Redraw the overlay for these selected items and the Aimed one. Returns the part ids whose whole-part look is on, by look. */
  set(selected: readonly LookItem[], aimed: LookItem | null): { select: Set<string>; hover: string | null } {
    this.clear()
    const aimedKey = aimed ? lookKey(aimed) : null
    const parts = { select: new Set<string>(), hover: null as string | null }
    for (const item of selected) {
      if (lookKey(item) === aimedKey) continue
      this.add(item, 'select', parts)
    }
    if (aimed) this.add(aimed, 'hover', parts)
    return parts
  }

  update(width: number, height: number): void {
    this.width = width
    this.height = height
    for (const m of this.lines) m.resolution.set(width, height)
  }

  dispose(): void {
    this.clear()
  }

  private clear(): void {
    for (const child of [...this.root.children]) {
      const g = (child as Mesh).geometry as BufferGeometry | undefined
      if (g) {
        // a face's triangles share the part's vertex buffers: dropping those would make the part upload them again
        if (child.userData['sharesBuffers']) {
          g.deleteAttribute('position')
          g.deleteAttribute('normal')
        }
        g.dispose()
      }
      this.root.remove(child)
    }
    for (const m of this.materials) m.dispose()
    this.materials.length = 0
    this.lines.length = 0
  }

  private add(item: LookItem, look: Exclude<Look, null>, parts: { select: Set<string>; hover: string | null }): void {
    switch (item.kind) {
      case 'part':
        if (item.part !== null && this.sources.parts.has(item.part)) {
          if (look === 'select') parts.select.add(item.part)
          else parts.hover = item.part
        }
        return
      case 'face':
        return this.face(item, look)
      case 'edge':
        return this.edge(item, look)
      case 'vertex':
        return this.vertex(item, look)
      case 'plane':
        return this.plane(item, look)
      case 'axis':
        return this.axis(item, look)
      case 'region':
        return this.region(item, look)
      case 'canvas':
        return this.quad(item.key == null ? undefined : this.sources.canvases?.get(item.key), look, 0.4)
    }
  }

  private colour(look: Exclude<Look, null>) {
    return rgb(look === 'select' ? color.selection : sceneColor.hover)
  }

  /**
   * The same colour for a three.js built-in material (points, flat meshes). Those encode their output to sRGB, which
   * would lighten a colour that is already an sRGB value (the shaded and wide-line materials write theirs unchanged), so
   * it goes in as linear.
   */
  private builtIn(c: ReturnType<typeof rgb>) {
    return c.clone().convertSRGBToLinear()
  }

  private wide(points: ArrayLike<number>, colour: ReturnType<typeof rgb>, width: number, order: number): void {
    if (points.length < 6) return
    const geo = new LineSegmentsGeometry()
    geo.setPositions(points as number[] | Float32Array)
    const m = new LineMaterial({ linewidth: width })
    // a wide line is a strip of triangles: pulled toward the viewer, the whole width shows and the model's faces beside it do not cut it in half
    m.polygonOffset = true
    m.polygonOffsetFactor = -3 - order
    m.polygonOffsetUnits = -3 - order
    m.color.copy(colour)
    m.resolution.set(this.width, this.height)
    const l = new LineSegments2(geo, m)
    l.renderOrder = order
    this.materials.push(m)
    this.lines.push(m)
    this.root.add(l)
  }

  private dots(at: readonly number[], colour: ReturnType<typeof rgb>, size: number, order: number): void {
    const g = new BufferGeometry()
    g.setAttribute('position', new BufferAttribute(new Float32Array(at), 3))
    // drawn over the model: a vertex on the silhouette would be half hidden by the faces round it otherwise
    const m = new PointsMaterial({ color: this.builtIn(colour), size, sizeAttenuation: false, map: discTexture(), alphaTest: 0.5, depthTest: false })
    const p = new Points(g, m)
    p.renderOrder = order
    this.materials.push(m)
    this.root.add(p)
  }

  private face(item: LookItem, look: Exclude<Look, null>): void {
    const src = item.part === null ? undefined : this.sources.parts.get(item.part)
    const mesh = src?.part.mesh
    const range = mesh?.faceRanges[item.index]
    if (!src || !mesh || !range) return
    const [a, b] = range
    const geo = new BufferGeometry()
    geo.setAttribute('position', src.geometry.getAttribute('position'))
    geo.setAttribute('normal', src.geometry.getAttribute('normal'))
    const tri = mesh.indices.subarray(3 * a, 3 * b)
    geo.setIndex(new BufferAttribute(tri, 1))
    const base = src.part.faceColors?.get(item.index) ?? (src.part.color.length >= 3 ? src.part.color : sceneColor.part)
    const m = shadedMaterial(rgb(base))
    setLook(m, look)
    m.polygonOffsetFactor = 0
    m.polygonOffsetUnits = 0
    const o = new Mesh(geo, m)
    o.userData['sharesBuffers'] = true
    o.renderOrder = 0.5
    this.materials.push(m)
    this.root.add(o)
    this.wide(boundary(tri, mesh.positions), look === 'select' ? rgb(CASING) : this.colour(look), LOOK_SIZE.faceOutline, 5)
  }

  private edge(item: LookItem, look: Exclude<Look, null>): void {
    const src = item.part === null ? undefined : this.sources.parts.get(item.part)
    const poly = src?.part.mesh?.edges[item.index]
    if (!poly) return
    const pairs = polylinePairs(poly)
    if (look === 'select') {
      this.wide(pairs, rgb(CASING), LOOK_SIZE.edgeCasing, 5)
      this.wide(pairs, this.colour(look), LOOK_SIZE.edgeCore, 6)
    } else this.wide(pairs, this.colour(look), LOOK_SIZE.edgeHover, 6)
  }

  private vertex(item: LookItem, look: Exclude<Look, null>): void {
    const src = item.part === null ? undefined : this.sources.parts.get(item.part)
    const v = src?.part.mesh?.vertices
    if (!v || 3 * item.index + 2 >= v.length) return
    const at = [v[3 * item.index] as number, v[3 * item.index + 1] as number, v[3 * item.index + 2] as number]
    if (look === 'select') {
      this.dots(at, rgb(CASING), LOOK_SIZE.vertexCasing, 5)
      this.dots(at, this.colour(look), LOOK_SIZE.vertexCore, 6)
    } else this.dots(at, this.colour(look), LOOK_SIZE.vertexHover, 6)
  }

  private plane(item: LookItem, look: Exclude<Look, null>): void {
    this.quad(item.key == null ? undefined : this.sources.planes.get(item.key), look)
  }

  /** A plane's or a canvas's four corners as a tinted rectangle with an outline; `tint` scales the fill's opacity (a canvas keeps its picture readable). */
  private quad(c: readonly Vector3[] | undefined, look: Exclude<Look, null>, tint = 1): void {
    if (!c) return
    const pos = new Float32Array(c.flatMap((p) => [p.x, p.y, p.z]))
    const fill = new BufferGeometry()
    fill.setAttribute('position', new BufferAttribute(pos, 3))
    fill.setIndex([0, 1, 2, 0, 2, 3])
    const m = new MeshBasicMaterial({ color: this.builtIn(this.colour(look)), transparent: true, opacity: (look === 'select' ? 0.42 : 0.3) * tint, depthWrite: false, side: DoubleSide })
    const o = new Mesh(fill, m)
    o.renderOrder = 3.5
    this.materials.push(m)
    this.root.add(o)
    const loop: number[] = []
    for (let i = 0; i < 4; i++) {
      const p = c[i] as Vector3
      const q = c[(i + 1) % 4] as Vector3
      loop.push(p.x, p.y, p.z, q.x, q.y, q.z)
    }
    if (look === 'select') this.wide(loop, rgb(CASING), LOOK_SIZE.planeOutline, 5)
    else this.wide(loop, this.colour(look), LOOK_SIZE.planeOutline, 5)
  }

  private region(item: LookItem, look: Exclude<Look, null>): void {
    const r = item.part === null ? undefined : this.sources.regions?.get(regionKey(item.part, item.index))
    if (!r) return
    const fill = new BufferGeometry()
    fill.setAttribute('position', new BufferAttribute(r.positions, 3))
    fill.setIndex(new BufferAttribute(r.indices, 1))
    const m = new MeshBasicMaterial({ color: this.builtIn(this.colour(look)), transparent: true, opacity: look === 'select' ? 0.5 : 0.36, depthWrite: false, side: DoubleSide })
    const o = new Mesh(fill, m)
    o.renderOrder = 3.6
    this.materials.push(m)
    this.root.add(o)
    if (look === 'select') this.wide(r.outline, rgb(CASING), LOOK_SIZE.faceOutline, 5)
    else this.wide(r.outline, this.colour(look), LOOK_SIZE.faceOutline, 5)
  }

  private axis(item: LookItem, look: Exclude<Look, null>): void {
    const e = item.key == null ? undefined : this.sources.axes.get(item.key)
    if (!e) return
    const pts = [e[0].x, e[0].y, e[0].z, e[1].x, e[1].y, e[1].z]
    if (look === 'select') {
      this.wide(pts, rgb(CASING), LOOK_SIZE.edgeCasing, 5)
      this.wide(pts, this.colour(look), LOOK_SIZE.edgeCore, 6)
    } else this.wide(pts, this.colour(look), LOOK_SIZE.edgeHover, 6)
  }
}

/** Consecutive points of a polyline as pairs of points, for LineSegments2. */
function polylinePairs(p: Float32Array): number[] {
  const out: number[] = []
  for (let i = 0; i + 5 < p.length; i += 3) out.push(p[i] as number, p[i + 1] as number, p[i + 2] as number, p[i + 3] as number, p[i + 4] as number, p[i + 5] as number)
  return out
}

/**
 * The outline of a face: the sides used by one of its triangles only, as pairs of points. `tri` holds the face's
 * triangles as vertex indices into `positions`; a face's vertices are its own, so an inner side is used twice.
 */
function boundary(tri: Uint32Array, positions: ArrayLike<number>): number[] {
  const count = new Map<number, number>()
  const key = (a: number, b: number) => (a < b ? a * 4294967296 + b : b * 4294967296 + a)
  for (let t = 0; t + 2 < tri.length; t += 3) {
    for (let k = 0; k < 3; k++) {
      const e = key(tri[t + k] as number, tri[t + ((k + 1) % 3)] as number)
      count.set(e, (count.get(e) ?? 0) + 1)
    }
  }
  const out: number[] = []
  for (let t = 0; t + 2 < tri.length; t += 3) {
    for (let k = 0; k < 3; k++) {
      const a = tri[t + k] as number
      const b = tri[t + ((k + 1) % 3)] as number
      if (count.get(key(a, b)) !== 1) continue
      out.push(positions[3 * a] as number, positions[3 * a + 1] as number, positions[3 * a + 2] as number)
      out.push(positions[3 * b] as number, positions[3 * b + 1] as number, positions[3 * b + 2] as number)
    }
  }
  return out
}
