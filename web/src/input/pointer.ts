import type { Vector3 } from 'three'
import type { ViewCamera } from '../viewport/camera'

/** What the pointer module needs from the viewport it steers. */
export interface NavHost {
  readonly view: ViewCamera
  /** The model point under a view position (CSS pixels from the canvas top left), or null when nothing is there. */
  pickPoint(x: number, y: number): Vector3 | null
  /** Middle of the model: the orbit pivot when a touch misses it. */
  modelCentre(): Vector3
  /** Fit the model to the view. */
  fit(): void
  requestRender(): void
}

/** A pen sample in view coordinates (CSS pixels from the canvas top left). */
export interface PenPoint {
  x: number
  y: number
  pressure: number
  pointerId: number
}

/**
 * What a later card does with the pen: it Aims while the pen is down and Commits on release. The pointer module
 * hands these over and never lets the pen navigate. Set with `PointerModule.setPenHandler`.
 */
export interface PenHandler {
  aim(p: PenPoint): void
  commit(p: PenPoint): void
  cancel(): void
}

/**
 * What the pointer module tells the selection about a finger tap. `tap` is a Commit on the item under the tap, at
 * once. `undoTap` takes the last tap back: it is called when the next tap turns out to be the second of a double tap
 * (which fits the view and selects nothing).
 */
export interface TapHandler {
  tap(x: number, y: number): void
  undoTap(): void
}

/** A two-finger twist smaller than this (radians, 4.5 degrees) rolls nothing, so a pinch does not turn the view. */
export const TWIST_DEAD_ZONE = (4.5 * Math.PI) / 180
/** A tap is a touch that lasts less than this (ms) and moves no more than TAP_SLOP pixels: the one finger gesture that selects. */
const TAP_TIME = 250
const TAP_SLOP = 10
/** Two taps within this time (ms) and distance (px) make a double tap. */
const DOUBLE_TAP_TIME = 400
const DOUBLE_TAP_DISTANCE = 40
/** Wheel zoom: factor per pixel of wheel travel. */
const WHEEL_RATE = 0.0015
const LINE_PIXELS = 16

interface Touch {
  x: number
  y: number
}

type Mode =
  | { kind: 'idle' }
  | { kind: 'orbit'; id: number; pivot: Vector3 }
  | { kind: 'pan'; id: number }
  | { kind: 'two'; ids: [number, number]; mid: Touch; spacing: number; angle: number; twist: number; rolled: number }
  /** After a two-finger gesture, the finger left down does nothing until every finger is up. */
  | { kind: 'settling' }

const twistRoll = (twist: number): number => Math.sign(twist) * Math.max(0, Math.abs(twist) - TWIST_DEAD_ZONE)
const wrap = (a: number): number => (a > Math.PI ? a - 2 * Math.PI : a < -Math.PI ? a + 2 * Math.PI : a)

/**
 * The one place that listens to Pointer Events on the view. It tells pen, finger and mouse apart by pointerType.
 * Fingers navigate (one orbits, two pan, zoom and roll, a double tap fits); the mouse navigates Onshape style
 * (right drag orbits, middle drag pans, the wheel zooms at the cursor); the pen never navigates and goes to the
 * pen handler. Every pointer down is captured, because WebKit captures a finger implicitly but not the pen.
 * Nothing else adds pointer, touch or mouse listeners to the canvas.
 */
export class PointerModule {
  private readonly touches = new Map<number, Touch>()
  private mode: Mode = { kind: 'idle' }
  private pen: PenHandler | null = null
  private mousePick: PenHandler | null = null
  private tapHandler: TapHandler | null = null
  private penId: number | null = null
  private mouseId: number | null = null
  /** The mouse's left button, held: it Aims and Commits as the pen does. */
  private mouseSelectId: number | null = null
  private last: Touch = { x: 0, y: 0 }
  /** The single touch being watched for a tap, and the last completed tap. */
  private tapping: { id: number; x: number; y: number; t: number } | null = null
  private lastTap: { x: number; y: number; t: number } | null = null
  private cancelled = false

  constructor(
    private readonly el: HTMLElement,
    private readonly host: NavHost,
  ) {
    el.addEventListener('pointerdown', this.onDown)
    el.addEventListener('pointermove', this.onMove)
    el.addEventListener('pointerup', this.onUp)
    el.addEventListener('pointercancel', this.onCancel)
    el.addEventListener('pointerleave', this.onLeave)
    el.addEventListener('wheel', this.onWheel, { passive: false })
    el.addEventListener('contextmenu', this.onContextMenu)
  }

  setPenHandler(handler: PenHandler | null): void {
    this.pen = handler
  }

  /** The mouse Aims by hovering (and while the left button is down), and Commits when the left button is released. */
  setMouseHandler(handler: PenHandler | null): void {
    this.mousePick = handler
  }

  /** A finger tap Commits through this handler. */
  setTapHandler(handler: TapHandler | null): void {
    this.tapHandler = handler
  }

  dispose(): void {
    const el = this.el
    el.removeEventListener('pointerdown', this.onDown)
    el.removeEventListener('pointermove', this.onMove)
    el.removeEventListener('pointerup', this.onUp)
    el.removeEventListener('pointercancel', this.onCancel)
    el.removeEventListener('pointerleave', this.onLeave)
    el.removeEventListener('wheel', this.onWheel)
    el.removeEventListener('contextmenu', this.onContextMenu)
  }

  private local(e: PointerEvent | WheelEvent): Touch {
    const r = this.el.getBoundingClientRect()
    return { x: e.clientX - r.left, y: e.clientY - r.top }
  }

  private penPoint(e: PointerEvent): PenPoint {
    const p = this.local(e)
    return { x: p.x, y: p.y, pressure: e.pressure, pointerId: e.pointerId }
  }

  private capture(id: number): void {
    // A synthetic event with an unknown id makes this throw; navigation must not depend on it.
    try {
      this.el.setPointerCapture(id)
    } catch {
      /* not an active pointer */
    }
  }

  private readonly onDown = (e: PointerEvent): void => {
    e.preventDefault()
    this.capture(e.pointerId)
    const p = this.local(e)
    if (e.pointerType === 'pen') {
      if (this.penId !== null) return
      this.penId = e.pointerId
      this.pen?.aim(this.penPoint(e))
      return
    }
    if (e.pointerType === 'mouse') {
      if (this.mouseId !== null || this.mouseSelectId !== null) return
      if (e.button === 0) {
        this.mouseSelectId = e.pointerId
        this.mousePick?.aim(this.penPoint(e))
        return
      }
      if (e.button === 2) this.mode = { kind: 'orbit', id: e.pointerId, pivot: this.pivotAt(p) }
      else if (e.button === 1) this.mode = { kind: 'pan', id: e.pointerId }
      else return
      this.mouseId = e.pointerId
      this.last = p
      return
    }
    this.touches.set(e.pointerId, p)
    if (this.touches.size === 1) {
      this.cancelled = false
      this.mode = { kind: 'orbit', id: e.pointerId, pivot: this.pivotAt(p) }
      this.last = p
      this.tapping = { id: e.pointerId, x: p.x, y: p.y, t: e.timeStamp }
    } else if (this.touches.size === 2 && this.mode.kind === 'orbit') {
      this.tapping = null
      this.startTwo()
    } else {
      this.tapping = null
    }
  }

  private pivotAt(p: Touch): Vector3 {
    return this.host.pickPoint(p.x, p.y) ?? this.host.modelCentre()
  }

  private startTwo(): void {
    const ids = [...this.touches.keys()].slice(0, 2) as [number, number]
    const [a, b] = ids.map((id) => this.touches.get(id)!) as [Touch, Touch]
    this.mode = {
      kind: 'two',
      ids,
      mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
      spacing: Math.max(Math.hypot(b.x - a.x, b.y - a.y), 1),
      angle: Math.atan2(b.y - a.y, b.x - a.x),
      twist: 0,
      rolled: 0,
    }
  }

  private readonly onMove = (e: PointerEvent): void => {
    if (e.pointerType === 'pen') {
      if (this.penId === e.pointerId) this.pen?.aim(this.penPoint(e))
      return
    }
    const p = this.local(e)
    const m = this.mode
    if (e.pointerType === 'mouse') {
      if (this.mouseSelectId === e.pointerId) this.mousePick?.aim(this.penPoint(e))
      else if (this.mouseId === null && this.mouseSelectId === null) this.mousePick?.aim(this.penPoint(e)) // hover
      else if (this.mouseId === e.pointerId) this.drag(m, p)
      return
    }
    if (!this.touches.has(e.pointerId)) return
    this.touches.set(e.pointerId, p)
    if (this.tapping && this.tapping.id === e.pointerId && Math.hypot(p.x - this.tapping.x, p.y - this.tapping.y) > TAP_SLOP)
      this.tapping = null
    if (m.kind === 'orbit') this.drag(m, p)
    else if (m.kind === 'two') this.twoFingers(m)
  }

  private drag(m: Mode, p: Touch): void {
    const dx = p.x - this.last.x
    const dy = p.y - this.last.y
    this.last = p
    if (dx === 0 && dy === 0) return
    if (m.kind === 'orbit') this.host.view.orbit(dx, dy, m.pivot)
    else if (m.kind === 'pan') this.host.view.pan(dx, dy)
    else return
    this.host.requestRender()
  }

  private twoFingers(m: Extract<Mode, { kind: 'two' }>): void {
    const a = this.touches.get(m.ids[0])
    const b = this.touches.get(m.ids[1])
    if (!a || !b) return
    const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
    const spacing = Math.max(Math.hypot(b.x - a.x, b.y - a.y), 1)
    const angle = Math.atan2(b.y - a.y, b.x - a.x)
    m.twist += wrap(angle - m.angle)
    const rolled = twistRoll(m.twist)
    this.host.view.gesture(m.mid.x, m.mid.y, mid.x, mid.y, spacing / m.spacing, rolled - m.rolled)
    m.mid = mid
    m.spacing = spacing
    m.angle = angle
    m.rolled = rolled
    this.host.requestRender()
  }

  private readonly onUp = (e: PointerEvent): void => {
    if (e.pointerType === 'pen') {
      if (this.penId !== e.pointerId) return
      this.penId = null
      this.pen?.commit(this.penPoint(e))
      return
    }
    if (e.pointerType === 'mouse') {
      if (this.mouseSelectId === e.pointerId) {
        this.mouseSelectId = null
        this.mousePick?.commit(this.penPoint(e))
        return
      }
      if (this.mouseId !== e.pointerId) return
      this.mouseId = null
      this.mode = { kind: 'idle' }
      return
    }
    if (!this.touches.delete(e.pointerId)) return
    const tap = this.tapping
    this.tapping = null
    if (tap && tap.id === e.pointerId && !this.cancelled && e.timeStamp - tap.t < TAP_TIME) this.tapped(tap.x, tap.y, e.timeStamp)
    this.afterTouchLeft()
  }

  private tapped(x: number, y: number, t: number): void {
    const prev = this.lastTap
    if (prev && t - prev.t < DOUBLE_TAP_TIME && Math.hypot(x - prev.x, y - prev.y) < DOUBLE_TAP_DISTANCE) {
      // The first tap already selected (a tap acts at once, never after a wait). A double tap is a view gesture: take that back.
      this.lastTap = null
      this.tapHandler?.undoTap()
      this.host.fit()
    } else {
      this.lastTap = { x, y, t }
      this.tapHandler?.tap(x, y)
    }
  }

  private afterTouchLeft(): void {
    if (this.touches.size === 0) this.mode = { kind: 'idle' }
    else if (this.mode.kind === 'two' || this.mode.kind === 'orbit') this.mode = { kind: 'settling' }
  }

  private readonly onCancel = (e: PointerEvent): void => {
    if (e.pointerType === 'pen') {
      if (this.penId !== e.pointerId) return
      this.penId = null
      this.pen?.cancel()
      return
    }
    if (e.pointerType === 'mouse') {
      if (this.mouseSelectId === e.pointerId) {
        this.mouseSelectId = null
        this.mousePick?.cancel()
      }
      if (this.mouseId === e.pointerId) {
        this.mouseId = null
        this.mode = { kind: 'idle' }
      }
      return
    }
    if (!this.touches.delete(e.pointerId)) return
    this.cancelled = true
    this.tapping = null
    this.afterTouchLeft()
  }

  /** The mouse leaves the view: nothing is under it any more. */
  private readonly onLeave = (e: PointerEvent): void => {
    if (e.pointerType === 'mouse' && this.mouseSelectId === null && this.mouseId === null) this.mousePick?.cancel()
  }

  private readonly onWheel = (e: WheelEvent): void => {
    e.preventDefault()
    const unit = e.deltaMode === 1 ? LINE_PIXELS : e.deltaMode === 2 ? this.el.clientHeight : 1
    const p = this.local(e)
    this.host.view.zoomAt(Math.exp(-e.deltaY * unit * WHEEL_RATE), p.x, p.y)
    this.host.requestRender()
  }

  private readonly onContextMenu = (e: Event): void => {
    e.preventDefault()
  }
}
