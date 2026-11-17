import { Group, Points, PointsMaterial, BufferGeometry, Vector3 } from 'three'
import type { Material } from 'three'
import { LineMaterial } from 'three/addons/lines/LineMaterial.js'
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js'
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js'
import { sceneColor } from '../theme/tokens'
import type { AssemblyOutputs, Frame, StudioOutputs, Vec3 } from '../wire'
import { rgb } from './colors'
import type { ViewCamera } from './camera'
import type { PickPointer } from './pick'

/**
 * Mate connectors on screen (card I10.3): each one a small triad (a Z axis, an X axis, a Y axis and an origin dot) that keeps
 * its size in CSS pixels whatever the zoom, drawn on top of the model as the desktop's `Viewport.triads` does (34 px by
 * default, the Z arm 1.25 times and the X and Y arms 0.8 times that, the dot 9 px, the axes' own colours; a colour mixes into
 * them half and half and colours the dot). Three kinds of triad come from three places:
 *   - a Part Studio's own mate connectors, with the desktop's default look (`studioTriads`);
 *   - an Assembly instance's connectors, teal as the desktop's `_own_connector_triads` draws them (`assemblyTriads`);
 *   - the two ends of every mate, with the default look (`assemblyTriads`).
 * Only a connector can be picked (`pickTriad`); a mate end is not a connector, it names a face or an edge.
 * Nothing here listens to the pointer: the pick indexes of the two views call `pickTriad`.
 */
export type Rgb = readonly [number, number, number]

export interface TriadSpec {
  /** "connector": a mate connector, which a pick can take; "mate": the end of a mate, which is only drawn. */
  readonly kind: 'connector' | 'mate'
  /** A Part Studio's connector feature id; an Assembly instance's connector id; a mate's id for a mate end. */
  readonly id: string
  /** The Assembly instance a connector belongs to (an instance's connector, or the instance a mate end is on); absent in a Part Studio. */
  readonly instance?: string
  /** "a" or "b" of a mate end. */
  readonly end?: 'a' | 'b'
  /** Where it is, in the view's world (mm), and its three unit axes. */
  readonly o: Vec3
  readonly x: Vec3
  readonly y: Vec3
  readonly z: Vec3
  /** Mixed into the axis colours and colours the dot; null keeps the desktop's defaults. */
  readonly color: Rgb | null
  readonly sizePx: number
  readonly width: number
  /** For an instance's connector: its frame in the part's own coordinates, to place it again when the instance moves. */
  readonly local?: Frame
}

/** The desktop's defaults (`Viewport.triads`): 34 px, 2.6 px wide lines. */
export const TRIAD_PX = 34
export const TRIAD_WIDTH = 2.6
/** `_own_connector_triads`: the connectors a part brings are teal. */
export const OWN_CONNECTOR: Rgb = [0.15, 0.7, 0.75]
/** The dot of a triad with no colour of its own. */
const DOT: Rgb = [0.98, 0.62, 0.15]
const DOT_PX = 9
const ARM_Z = 1.25
const ARM_XY = 0.8

/** How far from a triad a pick still takes it: the desktop's 12 px for the mouse, the pointer's reach for a pen or a finger. */
export const CONNECTOR_REACH: Readonly<Record<PickPointer, number>> = { pen: 14, touch: 22, mouse: 12 }

const unit = (v: Vec3): Vec3 => {
  const l = Math.hypot(v[0], v[1], v[2]) || 1
  return [v[0] / l, v[1] / l, v[2] / l]
}
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]

/** The triad of a frame: unit z and x, and y = z x x. */
export function axesOf(fr: Frame): { o: Vec3; x: Vec3; y: Vec3; z: Vec3 } {
  const z = unit(fr.z)
  const x = unit(fr.x)
  return { o: [fr.o[0], fr.o[1], fr.o[2]], x, y: cross(z, x), z }
}

/** `fr` (in a part's own coordinates) placed by `T`, a row-major 4 x 4. */
export function placeFrame(fr: Frame, T: readonly (readonly number[])[]): Frame {
  const at = (r: number, c: number): number => (T[r] as readonly number[])[c] as number
  const point = (v: Vec3): Vec3 => [0, 1, 2].map((r) => at(r, 0) * v[0] + at(r, 1) * v[1] + at(r, 2) * v[2] + at(r, 3)) as unknown as Vec3
  const dir = (v: Vec3): Vec3 => [0, 1, 2].map((r) => at(r, 0) * v[0] + at(r, 1) * v[1] + at(r, 2) * v[2]) as unknown as Vec3
  return { o: point(fr.o), z: dir(fr.z), x: dir(fr.x) }
}

/**
 * The triads of a Part Studio's mate connectors: those of a part the eye hides, and those whose own feature the eye hides, are
 * left out (the desktop's `_connector_triads`).
 */
export function studioTriads(outputs: StudioOutputs): TriadSpec[] {
  const hid = new Set(outputs.features.filter((f) => f.hidden).map((f) => f.id))
  const hiddenParts = new Set(outputs.parts.filter((p) => p.hidden).map((p) => p.id))
  return outputs.connectors
    .filter((c) => !hid.has(c.id) && !hiddenParts.has(c.part))
    .map((c) => ({ kind: 'connector', id: c.id, ...axesOf(c.frame), color: null, sizePx: TRIAD_PX, width: TRIAD_WIDTH }))
}

/** The id of the mate a mate dialog is making or editing (card I10.6): its two ends are drawn while the dialog is open, whether J is on or not. */
export const PREVIEW_MATE = '__mate_dialog__'

/** Which of an Assembly's triads to draw: K draws the instances' connectors, J the ends of the mates. */
export interface AssemblyTriadFlags {
  readonly connectors: boolean
  readonly mates: boolean
}

/** The triads of an Assembly: the connectors each shown instance offers (K) and the two ends of every mate (J). */
export function assemblyTriads(outputs: AssemblyOutputs, flags: AssemblyTriadFlags): TriadSpec[] {
  const out: TriadSpec[] = []
  if (flags.connectors) {
    for (const inst of outputs.instances) {
      if (inst.hidden) continue
      for (const c of inst.connectors) {
        out.push({ kind: 'connector', id: c.id, instance: inst.id, ...axesOf(c.world), color: OWN_CONNECTOR, sizePx: TRIAD_PX, width: TRIAD_WIDTH, local: c.frame })
      }
    }
  }
  if (flags.mates || outputs.mates.some((m) => m.id === PREVIEW_MATE)) {
    const shown = new Set(outputs.instances.filter((i) => !i.hidden).map((i) => i.id))
    for (const m of outputs.mates) {
      if (!flags.mates && m.id !== PREVIEW_MATE) continue
      ;(['a', 'b'] as const).forEach((end) => {
        const fr = end === 'a' ? m.ends[0] : m.ends[1]
        const instance = end === 'a' ? m.a : m.b
        if (fr !== null && shown.has(instance)) out.push({ kind: 'mate', id: m.id, instance, end, ...axesOf(fr), color: null, sizePx: TRIAD_PX, width: TRIAD_WIDTH })
      })
    }
  }
  return out
}

/** The same triad moved to `T` * its local frame (an instance's connector follows the instance while it is dragged). */
export function moved(t: TriadSpec, T: readonly (readonly number[])[]): TriadSpec {
  return t.local === undefined ? t : { ...t, ...axesOf(placeFrame(t.local, T)) }
}

export interface TriadLayer {
  readonly group: Group
  /** Draw the triad `i` of the list given to `buildTriads` where `t` puts it. */
  set(i: number, t: TriadSpec): void
  /** Keep the sizes and the line widths right for the viewport size and the zoom (mm per pixel). */
  update(width: number, height: number, scale: number): void
  dispose(): void
}

/** Triad objects for `specs`, in a group named "connectors": one child per triad, in the list's order. */
export function buildTriads(specs: readonly TriadSpec[]): TriadLayer {
  const group = new Group()
  group.name = 'connectors'
  const materials: Material[] = []
  const lineMats: LineMaterial[] = []
  const geometries: BufferGeometry[] = []
  const items: { node: Group; spec: TriadSpec }[] = []
  let scale = 1
  for (const spec of specs) {
    const node = new Group()
    node.name = spec.kind === 'connector' ? `connector:${spec.instance ?? ''}:${spec.id}` : `mate-end:${spec.id}:${spec.end ?? ''}`
    node.matrixAutoUpdate = false
    const arms: [Vec3, Rgb][] = [
      [[ARM_XY, 0, 0], sceneColor.axisX],
      [[0, ARM_XY, 0], sceneColor.axisY],
      [[0, 0, ARM_Z], sceneColor.axisZ],
    ]
    const positions: number[] = []
    const colors: number[] = []
    for (const [tip, base] of arms) {
      positions.push(0, 0, 0, ...tip)
      const c = spec.color === null ? base : ([0, 1, 2].map((k) => 0.5 * (base[k] as number) + 0.5 * (spec.color![k] as number)) as unknown as Rgb)
      colors.push(...c, ...c)
    }
    const geo = new LineSegmentsGeometry()
    geo.setPositions(positions)
    geo.setColors(colors)
    const mat = new LineMaterial({ linewidth: spec.width, vertexColors: true, depthTest: false, depthWrite: false })
    const lines = new LineSegments2(geo, mat)
    lines.frustumCulled = false
    lines.renderOrder = 8
    const dm = new PointsMaterial({ color: rgb(spec.color ?? DOT), size: DOT_PX, sizeAttenuation: false, depthTest: false })
    const dotGeo = new BufferGeometry().setFromPoints([new Vector3(0, 0, 0)])
    const dot = new Points(dotGeo, dm)
    dot.frustumCulled = false
    dot.renderOrder = 9
    node.add(lines, dot)
    group.add(node)
    materials.push(mat, dm)
    lineMats.push(mat)
    geometries.push(geo, dotGeo)
    items.push({ node, spec })
  }
  const place = (i: number): void => {
    const it = items[i] as { node: Group; spec: TriadSpec }
    const s = scale * it.spec.sizePx
    const { o, x, y, z } = it.spec
    it.node.matrix.makeBasis(new Vector3(...x).multiplyScalar(s), new Vector3(...y).multiplyScalar(s), new Vector3(...z).multiplyScalar(s))
    it.node.matrix.setPosition(o[0], o[1], o[2])
    it.node.matrixWorldNeedsUpdate = true
  }
  return {
    group,
    set(i, t) {
      const it = items[i]
      if (!it) return
      it.spec = t
      place(i)
    },
    update(width, height, mmPerPx) {
      scale = mmPerPx
      for (const m of lineMats) m.resolution.set(width, height)
      for (let i = 0; i < items.length; i++) place(i)
    },
    dispose() {
      for (const g of geometries) g.dispose()
      for (const m of materials) m.dispose()
    },
  }
}

/**
 * The connector triad nearest the pointer, within the pointer's reach, or null. The distance is to the triad's three arms (a finger
 * reaches a triad by any of them), measured on screen. Ahead of every face: the desktop's `pick` tries triads first.
 */
export function pickTriad(view: ViewCamera, specs: readonly TriadSpec[], x: number, y: number, pointer: PickPointer): { spec: TriadSpec; distancePx: number } | null {
  const reach = CONNECTOR_REACH[pointer]
  const { right, up } = view.axes()
  const { target, scale } = view
  const hw = view.width / 2
  const hh = view.height / 2
  const screen = (p: Vec3): [number, number] => {
    const dx = p[0] - target.x
    const dy = p[1] - target.y
    const dz = p[2] - target.z
    return [hw + (dx * right.x + dy * right.y + dz * right.z) / scale, hh - (dx * up.x + dy * up.y + dz * up.z) / scale]
  }
  let best: { spec: TriadSpec; distancePx: number } | null = null
  for (const t of specs) {
    if (t.kind !== 'connector') continue
    const a = screen(t.o)
    let d = Infinity
    for (const [axis, arm] of [
      [t.x, ARM_XY],
      [t.y, ARM_XY],
      [t.z, ARM_Z],
    ] as const) {
      const len = arm * t.sizePx * scale
      const b = screen([t.o[0] + axis[0] * len, t.o[1] + axis[1] * len, t.o[2] + axis[2] * len])
      const abx = b[0] - a[0]
      const aby = b[1] - a[1]
      const l2 = abx * abx + aby * aby
      let u = l2 < 1e-12 ? 0 : ((x - a[0]) * abx + (y - a[1]) * aby) / l2
      u = u < 0 ? 0 : u > 1 ? 1 : u
      d = Math.min(d, Math.hypot(a[0] + u * abx - x, a[1] + u * aby - y))
    }
    if (d <= reach && (best === null || d < best.distancePx - 1e-6)) best = { spec: t, distancePx: d }
  }
  return best
}
