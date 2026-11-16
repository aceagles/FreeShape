import type { HandleHandler, PenPoint } from '../input/pointer'
import type { SelectionStore } from '../selection/store'
import type { AssemblyOutputs, InstanceEntry, Part } from '../wire'
import type { ViewCamera } from '../viewport/camera'
import { selectedInstance } from './select'

/**
 * The move gizmo of an Assembly instance (card I8.2): three axis arrows and three rotation rings, each a Drag handle, at
 * the middle of the instance's part (its origin can lie outside the part) and lined up with its own axes, as the desktop's triad is (freeshape/ui/gizmo.py). Dragging a
 * handle sends move_begin, then move_to for the latest position, then move_end; the view redraws from the outputs each
 * reply brings. A fixed or hidden instance shows no gizmo.
 *
 * This file holds the layout on screen, the hit test and the drag arithmetic, and the controller that the pointer module
 * hands the press to. Nothing here draws: `AssemblyViewport` draws the shape this reports.
 */

type V3 = readonly [number, number, number]
export interface Pt {
  x: number
  y: number
}

/** Length of an arrow on screen, CSS pixels. */
export const ARROW_PX = 96
/** Where the translate Drag handle sits on its arrow (fraction of the arrow). */
export const HANDLE_AT = 0.56
/** Radius of the translate Drag handle (44 px across, as the mock's) and of the rotation ring handle. */
export const HANDLE_R = 20
export const RING_R = 15
/** How far past the arrow's tip the rotation ring sits, px. */
export const RING_GAP = 30
/** How close a press must be to a handle (px from its middle, or from the arrow's shaft) to take it. */
export const HIT_TOUCH = 24
export const HIT_MOUSE = 16
/** An axis that points this much along the view (foreshortened below this fraction of its length) has no handle. */
export const MIN_FORESHORTEN = 0.18

export const AXIS_COLOURS = ['#d63c34', '#40a048', '#3466d6'] as const
export const AXIS_NAMES = ['X', 'Y', 'Z'] as const

export type HandleKind = 'move' | 'turn'
export interface HandleRef {
  kind: HandleKind
  axis: 0 | 1 | 2
}

/** One axis of the gizmo as drawn. Positions are CSS pixels from the view's top left. */
export interface AxisShape {
  axis: 0 | 1 | 2
  colour: string
  /** The arrow runs from the gizmo's origin to `tip`; null when the axis points along the view (no handles then). */
  tip: Pt | null
  /** Middle of the translate Drag handle, and of the rotation ring handle. */
  move: Pt | null
  turn: Pt | null
  /** Unit screen direction of the arrow. */
  dir: Pt | null
}

export interface Readout {
  handle: HandleRef
  /** Signed total of the last drag: mm along the axis or degrees about it. */
  value: number
  /** Where the label sits. */
  at: Pt
}

export interface GizmoShape {
  instance: string
  name: string
  origin: Pt
  axes: AxisShape[]
  /** The last drag's value, tappable to type a number; null before a drag. */
  readout: Readout | null
  /** A drag is in progress on this handle. */
  active: HandleRef | null
}

/** What sends a drag to the server: `AssemblyEdits.drag`. `move` keeps only the newest transform while one is in flight. */
export interface DragSender {
  /** Edits are not allowed here (a read-only Document). */
  readonly disabled: boolean
  begin(instance: string): void
  /** `T`: the 4 x 4 world motion applied to the placements `begin` remembered. */
  move(T: number[][]): void
  end(): Promise<void>
}

// ---------------------------------------------------------------------------------------------------- arithmetic

const dot = (a: V3, b: V3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const norm = (a: V3): V3 => {
  const n = Math.hypot(a[0], a[1], a[2]) || 1
  return [a[0] / n, a[1] / n, a[2] / n]
}

/**
 * The anchor and the three axes (columns of the rotation part) of a placement. The anchor is `local` (a point in the
 * part's own coordinates; the part's origin by default) carried by the placement.
 */
export function poseOf(T: readonly (readonly number[])[], local: V3 = [0, 0, 0]): { o: V3; axes: [V3, V3, V3] } {
  const c = (k: number): V3 => [T[0]![k]!, T[1]![k]!, T[2]![k]!]
  const [x, y, z] = axes3(T)
  return { o: [c(3)[0] + x[0] * local[0] + y[0] * local[1] + z[0] * local[2], c(3)[1] + x[1] * local[0] + y[1] * local[1] + z[1] * local[2], c(3)[2] + x[2] * local[0] + y[2] * local[1] + z[2] * local[2]], axes: [x, y, z] }
}
const axes3 = (T: readonly (readonly number[])[]): [V3, V3, V3] => {
  const c = (k: number): V3 => [T[0]![k]!, T[1]![k]!, T[2]![k]!]
  return [c(0), c(1), c(2)]
}

/** The middle of a part's bounding box in its own coordinates: where its gizmo sits (the part's origin can lie far outside the part). */
export function centreOf(part: Part | undefined): V3 {
  const p = part?.mesh?.positions
  if (!p || p.length < 3) return [0, 0, 0]
  const lo = [Infinity, Infinity, Infinity]
  const hi = [-Infinity, -Infinity, -Infinity]
  for (let i = 0; i + 2 < p.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      const v = p[i + k]!
      if (v < lo[k]!) lo[k] = v
      if (v > hi[k]!) hi[k] = v
    }
  }
  return [(lo[0]! + hi[0]!) / 2, (lo[1]! + hi[1]!) / 2, (lo[2]! + hi[2]!) / 2]
}

/** The 4 x 4 motion that slides by `distance` mm along the unit vector `d`. */
export function translationMotion(d: V3, distance: number): number[][] {
  return [
    [1, 0, 0, d[0] * distance],
    [0, 1, 0, d[1] * distance],
    [0, 0, 1, d[2] * distance],
    [0, 0, 0, 1],
  ]
}

/** The 4 x 4 motion that turns by `radians` about the unit vector `a` through the point `about` (Rodrigues). */
export function rotationMotion(a: V3, radians: number, about: V3): number[][] {
  const [x, y, z] = a
  const c = Math.cos(radians)
  const s = Math.sin(radians)
  const t = 1 - c
  const R = [
    [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
    [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
    [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
  ]
  const p = (i: number): number => about[i]! - (R[i]![0]! * about[0] + R[i]![1]! * about[1] + R[i]![2]! * about[2])
  return [
    [R[0]![0]!, R[0]![1]!, R[0]![2]!, p(0)],
    [R[1]![0]!, R[1]![1]!, R[1]![2]!, p(1)],
    [R[2]![0]!, R[2]![1]!, R[2]![2]!, p(2)],
    [0, 0, 0, 1],
  ]
}

/** Where the gizmo of an instance sits in the view, and what it is made of. */
export function layoutOf(view: ViewCamera, T: readonly (readonly number[])[], local: V3 = [0, 0, 0]): { origin: Pt; axes: Omit<AxisShape, 'colour'>[] } {
  const { o, axes } = poseOf(T, local)
  const origin = view.project(o)
  const out = axes.map((a, k) => {
    const s = view.project([o[0] + a[0], o[1] + a[1], o[2] + a[2]])
    const dx = s.x - origin.x
    const dy = s.y - origin.y
    const len = Math.hypot(dx, dy)
    // px per mm of an axis lying across the view is 1 / scale
    if (len * view.scale < MIN_FORESHORTEN) return { axis: k as 0 | 1 | 2, tip: null, move: null, turn: null, dir: null }
    const dir = { x: dx / len, y: dy / len }
    const at = (d: number): Pt => ({ x: origin.x + dir.x * d, y: origin.y + dir.y * d })
    return { axis: k as 0 | 1 | 2, tip: at(ARROW_PX), move: at(ARROW_PX * HANDLE_AT), turn: at(ARROW_PX + RING_GAP), dir }
  })
  return { origin, axes: out }
}

const near = (p: Pt, q: Pt): number => Math.hypot(p.x - q.x, p.y - q.y)
function segmentDistance(p: Pt, a: Pt, b: Pt): number {
  const abx = b.x - a.x
  const aby = b.y - a.y
  const l2 = abx * abx + aby * aby
  const t = l2 < 1e-9 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * abx + (p.y - a.y) * aby) / l2))
  return Math.hypot(p.x - (a.x + abx * t), p.y - (a.y + aby * t))
}

/** The handle under a view position, or null. Each handle scores how far inside its reach the press is; the best score wins. */
export function hitHandle(shape: Pick<GizmoShape, 'origin' | 'axes'>, p: Pt, pointer: 'pen' | 'mouse' | 'touch'): HandleRef | null {
  const reach = pointer === 'mouse' ? HIT_MOUSE : HIT_TOUCH
  let best: { ref: HandleRef; score: number } | null = null
  const offer = (ref: HandleRef, score: number): void => {
    if (score <= 0 && (!best || score < best.score)) best = { ref, score }
  }
  for (const a of shape.axes) {
    if (!a.tip || !a.move || !a.turn) continue
    offer({ kind: 'turn', axis: a.axis }, near(p, a.turn) - Math.max(RING_R, reach))
    offer({ kind: 'move', axis: a.axis }, near(p, a.move) - Math.max(HANDLE_R, reach - 4))
    offer({ kind: 'move', axis: a.axis }, segmentDistance(p, shape.origin, a.tip) - (reach - 8))
  }
  return best ? (best as { ref: HandleRef; score: number }).ref : null
}

/** A drag of one handle, worked out against the placement it began with (so a dropped sample loses nothing). */
export class HandleDrag {
  private readonly axis: V3
  private readonly origin: V3
  private readonly start: Pt
  private readonly screenAxis: Pt
  private readonly screenOrigin: Pt
  private readonly flip: number
  private readonly startAngle: number

  constructor(
    view: ViewCamera,
    T: readonly (readonly number[])[],
    local: V3,
    readonly handle: HandleRef,
    from: Pt,
  ) {
    const pose = poseOf(T, local)
    this.axis = pose.axes[handle.axis]
    this.origin = pose.o
    this.start = from
    this.screenOrigin = view.project(pose.o)
    const tip = view.project([pose.o[0] + this.axis[0], pose.o[1] + this.axis[1], pose.o[2] + this.axis[2]])
    this.screenAxis = { x: tip.x - this.screenOrigin.x, y: tip.y - this.screenOrigin.y }
    // turning counter-clockwise on screen is a positive turn about an axis that points at the viewer; screen y runs down
    this.flip = dot(this.axis, norm(((b) => [b.x, b.y, b.z] as V3)(view.axes().back))) >= 0 ? -1 : 1
    this.startAngle = Math.atan2(from.y - this.screenOrigin.y, from.x - this.screenOrigin.x)
  }

  /** The motion for a pointer at `p`, and the value (mm or degrees) it stands for. */
  at(p: Pt): { T: number[][]; value: number } {
    if (this.handle.kind === 'move') {
      const l2 = this.screenAxis.x ** 2 + this.screenAxis.y ** 2
      const mm = l2 < 1e-9 ? 0 : ((p.x - this.start.x) * this.screenAxis.x + (p.y - this.start.y) * this.screenAxis.y) / l2
      return { T: translationMotion(this.axis, mm), value: mm }
    }
    let da = Math.atan2(p.y - this.screenOrigin.y, p.x - this.screenOrigin.x) - this.startAngle
    da = Math.atan2(Math.sin(da), Math.cos(da)) // the short way round
    const rad = da * this.flip
    return { T: rotationMotion(this.axis, rad, this.origin), value: (rad * 180) / Math.PI }
  }
}

// ----------------------------------------------------------------------------------------------- the controller

/** A typed distance (mm, cm, m, in) or angle (deg, rad); the number and unit are read here, the server gets the motion. */
export function parseTyped(text: string, kind: HandleKind): number | null {
  const m = /^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)\s*([a-z°]*)\s*$/i.exec(text)
  if (!m) return null
  const n = Number(m[1])
  const unit = (m[2] ?? '').toLowerCase()
  if (!Number.isFinite(n)) return null
  if (kind === 'move') {
    const f: Record<string, number> = { '': 1, mm: 1, cm: 10, m: 1000, in: 25.4, '"': 25.4 }
    return unit in f ? n * f[unit]! : null
  }
  const f: Record<string, number> = { '': 1, deg: 1, '°': 1, rad: 180 / Math.PI }
  return unit in f ? n * f[unit]! : null
}

/** A value as the label and the pad show it: at most two decimals, no trailing zeros. */
export const trimmed = (v: number): string => String(Math.round(v * 100) / 100 || 0)
export const readoutText = (kind: HandleKind, v: number): string => (kind === 'move' ? `${trimmed(v)} mm` : `${trimmed(v)}°`)
export const padText = (kind: HandleKind, v: number): string => (kind === 'move' ? `${trimmed(v)} mm` : `${trimmed(v)} deg`)

/** Whether an instance offers the gizmo: free (not fixed), drawn, and holding a part. */
export const movable = (inst: InstanceEntry | undefined): inst is InstanceEntry => !!inst && !inst.fixed && !inst.hidden && inst.part !== null

/**
 * The gizmo of the selected instance: reports its shape for drawing, takes presses from the pointer module and turns a drag into
 * move_begin, move_to and move_end on the sender.
 */
export class GizmoController implements HandleHandler {
  private outputs: AssemblyOutputs | null = null
  private sender: DragSender | null = null
  private drag: { handle: HandleDrag; instance: string } | null = null
  private last: { instance: string; handle: HandleRef; value: number } | null = null
  private shown: GizmoShape | null = null
  private key = ''

  constructor(
    private readonly view: ViewCamera,
    private readonly selection: SelectionStore,
    private readonly onChange: (shape: GizmoShape | null) => void,
  ) {}

  setOutputs(outputs: AssemblyOutputs | null): void {
    this.outputs = outputs
    this.refresh()
  }

  setSender(sender: DragSender | null): void {
    this.sender = sender
    this.refresh()
  }

  /** The selected instance's entry when the gizmo shows for it. */
  private target(): InstanceEntry | null {
    if (!this.outputs || !this.sender || this.sender.disabled) return null
    const id = selectedInstance(this.selection.getState().items)
    const inst = id === null ? undefined : this.outputs.instances.find((i) => i.id === id)
    return movable(inst) ? inst : null
  }

  private anchor(inst: InstanceEntry): V3 {
    return centreOf(inst.part === null ? undefined : this.outputs?.parts[inst.part])
  }

  /** The shape now, or null: none when nothing movable is selected (a fixed instance has no gizmo). */
  shape(): GizmoShape | null {
    const inst = this.target()
    if (!inst) return null
    const { origin, axes } = layoutOf(this.view, inst.T, this.anchor(inst))
    const last = this.last && this.last.instance === inst.id ? this.last : null
    const a = last ? axes[last.handle.axis]! : null
    const spot = a ? (last!.handle.kind === 'move' ? a.move : a.turn) : null
    return {
      instance: inst.id,
      name: inst.name,
      origin,
      axes: axes.map((x) => ({ ...x, colour: AXIS_COLOURS[x.axis] })),
      readout: last && spot ? { handle: last.handle, value: last.value, at: spot } : null,
      active: this.drag ? this.drag.handle.handle : null,
    }
  }

  /** Recompute the shape (after outputs, selection or camera moved) and tell the drawing when it changed. */
  refresh(): void {
    if (this.last && this.last.instance !== selectedInstance(this.selection.getState().items)) this.last = null
    const s = this.shape()
    const key = s ? JSON.stringify(s, (_k, v: unknown) => (typeof v === 'number' ? Math.round(v * 10) / 10 : v)) : ''
    this.shown = s
    if (key === this.key) return
    this.key = key
    this.onChange(s)
  }

  current(): GizmoShape | null {
    return this.shown
  }

  press(p: PenPoint, pointer: 'pen' | 'mouse' | 'touch' = 'touch'): boolean {
    this.refresh()
    const s = this.shown
    const inst = this.target()
    if (!s || !inst || !this.sender) return false
    const ref = hitHandle(s, p, pointer)
    if (!ref) return false
    this.drag = { handle: new HandleDrag(this.view, inst.T, this.anchor(inst), ref, p), instance: inst.id }
    this.sender.begin(inst.id)
    this.refresh()
    return true
  }

  move(p: PenPoint): void {
    const d = this.drag
    if (!d || !this.sender) return
    const r = d.handle.at(p)
    this.last = { instance: d.instance, handle: d.handle.handle, value: r.value }
    this.sender.move(r.T)
    this.refresh()
  }

  release(p: PenPoint): void {
    this.move(p)
    this.finish()
  }

  cancel(): void {
    this.finish()
  }

  private finish(): void {
    if (!this.drag || !this.sender) return
    this.drag = null
    void this.sender.end()
    this.refresh()
  }

  /** A typed total for the last drag's handle: moves by the difference from what the drag showed, in one more undo step. */
  async typed(total: number): Promise<void> {
    const inst = this.target()
    const last = this.last
    if (!inst || !last || last.instance !== inst.id || !this.sender) return
    const pose = poseOf(inst.T, this.anchor(inst))
    const axis = pose.axes[last.handle.axis]
    const diff = total - last.value
    const T = last.handle.kind === 'move' ? translationMotion(axis, diff) : rotationMotion(axis, (diff * Math.PI) / 180, pose.o)
    this.sender.begin(inst.id)
    this.sender.move(T)
    await this.sender.end()
    this.last = { ...last, value: total }
    this.refresh()
  }

  /** The last drag, for the pad to open on its label. */
  readout(): { kind: HandleKind; value: number } | null {
    const l = this.last
    return l && this.shown?.instance === l.instance ? { kind: l.handle.kind, value: l.value } : null
  }
}
