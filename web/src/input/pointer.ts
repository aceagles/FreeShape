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
  /** Shift was held (read from the pointer event; Desktop mode: a click or box adds, a manipulator drag does not snap). */
  shift?: boolean
  /** Ctrl (or Cmd) was held. */
  ctrl?: boolean
}

/** A selection box being dragged with the mouse (Desktop mode): from the press (`x0`, `y0`) to the pointer now, CSS pixels from the view's top left. */
export interface BoxDrag {
  x0: number
  y0: number
  x1: number
  y1: number
}

/** A left drag in Desktop mode turns into a selection box once it has moved more than this (|dx| + |dy|, pixels; the desktop's `_moved`). */
export const BOX_SLOP = 3

/**
 * What a later card does with the pen: it Aims while the pen is down and Commits on release. The pointer module
 * hands these over and never lets the pen navigate. Set with `PointerModule.setPenHandler`.
 */
export interface PenHandler {
  /** The mouse's left button went down (before its first `aim`, which a hover also sends). */
  press?(p: PenPoint): void
  aim(p: PenPoint): void
  commit(p: PenPoint): void
  cancel(): void
  /** The mouse's right button was pressed and released without moving (a right click, not an orbit); only the mouse handler is asked. */
  secondary?(p: PenPoint): void
  /**
   * Desktop mode, the mouse only: the left button went down and has moved past BOX_SLOP. Return true to draw a selection
   * box (the module then sends `boxMove` for every move and `boxEnd` or `boxCancel`, and no more `aim` or `commit` for this
   * press); false, or no method, keeps the press an Aim that Commits on release, as before.
   */
  boxStart?(p: PenPoint): boolean
  boxMove?(box: BoxDrag): void
  /** The button was released: select what the box holds; `additive` when Shift or Ctrl was held at the release. */
  boxEnd?(box: BoxDrag, additive: boolean): void
  boxCancel?(): void
  /**
   * Desktop mode, the mouse only (I9.14, the desktop's `mouseDoubleClickEvent`): the left button went down a second time close
   * to the first click. The second press then neither Aims, Commits nor starts a box. Only a handler with this method gets it.
   */
  doubleClick?(p: PenPoint): void
}

/**
 * What the pointer module tells the selection about a finger tap. `tap` is a Commit on the item under the tap, at
 * once. `undoTap` takes the last tap back: it is called when the next tap turns out to be the second of a double tap
 * (which fits the view and selects nothing).
 */
export interface TapHandler {
  tap(x: number, y: number): void
  undoTap(): void
  /** Called after `undoTap` for a double tap; return true when the handler used it, and the view does not fit. */
  doubleTap?(x: number, y: number): boolean
}

/**
 * A Drag handle drawn over the view (a drag arrow, an Assembly gizmo). It gets the press before selection and
 * navigation: `press` returns true when the press lands on a handle, and the pointer is then the handle's until it
 * lifts (`move`, then `release`, or `cancel`). Set with `PointerModule.setHandleHandler`.
 */
export interface HandleHandler {
  /** `pointer` is the kind of pointer that pressed; a handler may size its hit area by it. */
  press(p: PenPoint, pointer?: 'pen' | 'mouse' | 'touch'): boolean
  move(p: PenPoint): void
  release(p: PenPoint): void
  cancel(): void
}

/**
 * A press that stays within HOLD_SLOP pixels for HOLD_TIME ms is a hold (the sketch quick menu, I7.8). `open` is asked when
 * the time is up and returns true when the handler takes the press: the pointer is then the handler's until it lifts
 * (`move`, then `release`, or `cancel`), and its release places and selects nothing. While `isOpen` is true a press anywhere
 * on the view is for `dismiss` alone. Pen and finger hold; the mouse does not. Set with `PointerModule.setHoldHandler`.
 */
export interface HoldHandler {
  open(x: number, y: number, pointer: 'pen' | 'touch'): boolean
  move(p: PenPoint): void
  release(p: PenPoint): void
  cancel(): void
  isOpen(): boolean
  dismiss(): void
}

export const HOLD_TIME = 400
export const HOLD_SLOP = 10

/** A two-finger twist smaller than this (radians, 4.5 degrees) rolls nothing, so a pinch does not turn the view. */
export const TWIST_DEAD_ZONE = (4.5 * Math.PI) / 180
/** A tap is a touch that lasts less than this (ms) and moves no more than TAP_SLOP pixels: the one finger gesture that selects. */
const TAP_TIME = 250
const TAP_SLOP = 10
/** Two taps within this time (ms) and distance (px) make a double tap. */
const DOUBLE_TAP_TIME = 400
const DOUBLE_TAP_DISTANCE = 40
/** A mouse double click: the second press within this many ms and pixels of the first click's release (Qt's 400 ms; its distance is a few pixels). */
const DOUBLE_CLICK_TIME = 400
const DOUBLE_CLICK_DISTANCE = 6
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

const twistRoll = (twist: number, dead: number): number => Math.sign(twist) * Math.max(0, Math.abs(twist) - dead)
const wrap = (a: number): number => (a > Math.PI ? a - 2 * Math.PI : a < -Math.PI ? a + 2 * Math.PI : a)

/**
 * The one place that listens to Pointer Events on the view. It tells pen, finger and mouse apart by pointerType.
 * Fingers navigate (one orbits, two pan, zoom and roll, a double tap fits); the mouse navigates Onshape style
 * (right drag orbits, middle drag pans, the wheel zooms at the cursor); the pen never navigates and goes to the
 * pen handler. Every pointer down is captured, because WebKit captures a finger implicitly but not the pen.
 * Nothing else adds pointer, touch or mouse listeners to the canvas. The browser's own long-press (selection, callout,
 * context menu) is kept off by `src/input/surface.ts` and the touch-action rules in shell.css.
 */
export class PointerModule {
  private readonly touches = new Map<number, Touch>()
  private mode: Mode = { kind: 'idle' }
  private pen: PenHandler | null = null
  private mousePick: PenHandler | null = null
  private tapHandler: TapHandler | null = null
  private handle: HandleHandler | null = null
  /** The pointer that holds a Drag handle. */
  private handleId: number | null = null
  private penId: number | null = null
  private mouseId: number | null = null
  /** The mouse's left button, held: it Aims and Commits as the pen does. */
  private mouseSelectId: number | null = null
  /** Where the right button went down: a release this close to it is a right click. */
  private rightDown: { x: number; y: number } | null = null
  private last: Touch = { x: 0, y: 0 }
  /** The single touch being watched for a tap, and the last completed tap. */
  private tapping: { id: number; x: number; y: number; t: number } | null = null
  private lastTap: { x: number; y: number; t: number } | null = null
  private cancelled = false
  /** A planar view (a sketch in 2D): one finger and the right mouse button pan instead of orbit, and a twist turns the view at once. */
  private planar = false
  private hold: HoldHandler | null = null
  /** The press being timed for a hold, and the pointer a hold took (a ring is open under it). */
  private holding: { id: number; x: number; y: number; kind: 'pen' | 'touch'; timer: ReturnType<typeof setTimeout> } | null = null
  private held: number | null = null
  /** Presses that closed an open hold: they do nothing else. */
  private readonly swallowed = new Set<number>()
  /** Desktop mode (I9.9): a left drag of the mouse may draw a selection box. Pen, finger and Tablet mode are unchanged by it. */
  private desktopMode = false
  /** The mouse's left press in Desktop mode: where it went down, whether it became a box, or was refused one. */
  private leftPress: { x: number; y: number; box: boolean; refused: boolean; double?: boolean } | null = null
  /** The last plain left click of the mouse in Desktop mode (where and when it was released), for a double click. */
  private lastClick: { x: number; y: number; t: number } | null = null
  private cursorAt: ((p: { x: number; y: number } | null) => void) | null = null

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
  }

  setPenHandler(handler: PenHandler | null): void {
    this.pen = handler
  }

  /** The mouse Aims by hovering (and while the left button is down), and Commits when the left button is released. */
  setMouseHandler(handler: PenHandler | null): void {
    this.mousePick = handler
    this.lastClick = null
  }

  /** A Drag handle takes a press (pen, finger or mouse left button) before selection and navigation. */
  setHandleHandler(handler: HandleHandler | null): void {
    this.handle = handler
  }

  /** Desktop mode: a mouse left drag can draw a selection box, and the mouse handler reads `desktop` to toggle on a click. */
  setDesktop(on: boolean): void {
    if (on === this.desktopMode) return
    this.desktopMode = on
    if (!on && this.leftPress?.box) this.mousePick?.boxCancel?.()
    this.leftPress = null
  }

  get desktop(): boolean {
    return this.desktopMode
  }

  /** Told where the mouse is over the view (CSS pixels from its top left) on every move, and null when it leaves: the count badge follows it. */
  setCursorHandler(handler: ((p: { x: number; y: number } | null) => void) | null): void {
    this.cursorAt = handler
  }

  /**
   * Planar navigation, for a view that must stay square to a plane (a sketch in its 2D mode): one finger (or the right
   * mouse button) pans instead of orbiting, and a two-finger twist turns the view from the first degree, with no dead zone.
   */
  setPlanar(on: boolean): void {
    this.planar = on
  }

  /** A press held still opens something (the sketch quick menu). */
  setHoldHandler(handler: HoldHandler | null): void {
    this.clearHold()
    this.hold = handler
  }

  private clearHold(): void {
    if (this.holding) clearTimeout(this.holding.timer)
    this.holding = null
  }

  private startHold(e: PointerEvent, kind: 'pen' | 'touch', p: Touch): void {
    this.clearHold()
    if (!this.hold) return
    const id = e.pointerId
    this.holding = { id, x: p.x, y: p.y, kind, timer: setTimeout(() => this.holdElapsed(id), HOLD_TIME) }
  }

  private holdElapsed(id: number): void {
    const h = this.holding
    if (!h || h.id !== id) return
    this.holding = null
    if (!this.hold?.open(h.x, h.y, h.kind)) return
    this.held = id
    if (h.kind === 'pen') this.pen?.cancel()
    else {
      this.tapping = null
      this.mode = { kind: 'settling' }
    }
  }

  /** A finger tap Commits through this handler. */
  setTapHandler(handler: TapHandler | null): void {
    this.tapHandler = handler
  }

  dispose(): void {
    this.clearHold()
    const el = this.el
    el.removeEventListener('pointerdown', this.onDown)
    el.removeEventListener('pointermove', this.onMove)
    el.removeEventListener('pointerup', this.onUp)
    el.removeEventListener('pointercancel', this.onCancel)
    el.removeEventListener('pointerleave', this.onLeave)
    el.removeEventListener('wheel', this.onWheel)
  }

  private local(e: PointerEvent | WheelEvent): Touch {
    const r = this.el.getBoundingClientRect()
    return { x: e.clientX - r.left, y: e.clientY - r.top }
  }

  private penPoint(e: PointerEvent): PenPoint {
    const p = this.local(e)
    const pt: PenPoint = { x: p.x, y: p.y, pressure: e.pressure, pointerId: e.pointerId }
    // the mouse carries the keys held with it (Desktop mode); pen and finger points are as they always were
    if (e.pointerType === 'mouse') {
      pt.shift = e.shiftKey
      pt.ctrl = e.ctrlKey || e.metaKey
    }
    return pt
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
    // a press on the view while a hold's ring is open closes it and does nothing else
    if (this.hold?.isOpen() && this.held === null && (e.pointerType !== 'mouse' || e.button === 0)) {
      this.hold.dismiss()
      this.swallowed.add(e.pointerId)
      return
    }
    // a Drag handle comes first; a second finger is a pinch, never a handle
    const free = e.pointerType === 'touch' ? this.touches.size === 0 : e.pointerType === 'mouse' ? e.button === 0 && this.mouseId === null && this.mouseSelectId === null : this.penId === null
    if (free && this.handleId === null && this.handle?.press(this.penPoint(e), e.pointerType === 'pen' ? 'pen' : e.pointerType === 'mouse' ? 'mouse' : 'touch')) {
      this.handleId = e.pointerId
      return
    }
    if (e.pointerType === 'pen') {
      if (this.penId !== null) return
      this.penId = e.pointerId
      this.pen?.aim(this.penPoint(e))
      this.startHold(e, 'pen', p)
      return
    }
    if (e.pointerType === 'mouse') {
      if (this.mouseId !== null || this.mouseSelectId !== null) return
      if (e.button === 0) {
        this.mouseSelectId = e.pointerId
        const was = this.lastClick
        this.lastClick = null
        if (this.desktopMode && this.mousePick?.doubleClick && was && e.timeStamp - was.t < DOUBLE_CLICK_TIME && Math.hypot(p.x - was.x, p.y - was.y) <= DOUBLE_CLICK_DISTANCE) {
          this.leftPress = { x: p.x, y: p.y, box: false, refused: true, double: true }
          this.mousePick.doubleClick(this.penPoint(e))
          return
        }
        this.leftPress = this.desktopMode ? { x: p.x, y: p.y, box: false, refused: false } : null
        this.mousePick?.press?.(this.penPoint(e))
        this.mousePick?.aim(this.penPoint(e))
        return
      }
      this.rightDown = e.button === 2 ? { x: p.x, y: p.y } : null
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
      this.startHold(e, 'touch', p)
    } else if (this.touches.size === 2 && this.mode.kind === 'orbit') {
      this.tapping = null
      this.clearHold()
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
    if (this.swallowed.has(e.pointerId)) return
    if (this.holding && this.holding.id === e.pointerId) {
      const q = this.local(e)
      if (Math.hypot(q.x - this.holding.x, q.y - this.holding.y) > HOLD_SLOP) this.clearHold()
    }
    if (this.held === e.pointerId) {
      this.hold?.move(this.penPoint(e))
      return
    }
    if (this.handleId === e.pointerId) {
      this.handle?.move(this.penPoint(e))
      return
    }
    if (e.pointerType === 'pen') {
      if (this.penId === e.pointerId) this.pen?.aim(this.penPoint(e))
      return
    }
    const p = this.local(e)
    const m = this.mode
    if (e.pointerType === 'mouse') {
      this.cursorAt?.(p)
      if (this.mouseSelectId === e.pointerId && this.leftPress) this.leftDrag(this.leftPress, this.penPoint(e))
      else if (this.mouseSelectId === e.pointerId) this.mousePick?.aim(this.penPoint(e))
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

  /** Desktop mode, the left button held: an Aim until it has moved past BOX_SLOP, then a box if the handler takes one. */
  private leftDrag(press: NonNullable<PointerModule['leftPress']>, p: PenPoint): void {
    if (press.double) return
    if (!press.box && !press.refused && Math.abs(p.x - press.x) + Math.abs(p.y - press.y) > BOX_SLOP) {
      if (this.mousePick?.boxStart?.(p)) press.box = true
      else press.refused = true
    }
    if (press.box) this.mousePick?.boxMove?.({ x0: press.x, y0: press.y, x1: p.x, y1: p.y })
    else this.mousePick?.aim(p)
  }

  private drag(m: Mode, p: Touch): void {
    const dx = p.x - this.last.x
    const dy = p.y - this.last.y
    this.last = p
    if (dx === 0 && dy === 0) return
    if (m.kind === 'orbit') {
      if (this.planar) this.host.view.pan(dx, dy)
      else this.host.view.orbit(dx, dy, m.pivot)
    }
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
    const rolled = twistRoll(m.twist, this.planar ? 0 : TWIST_DEAD_ZONE)
    this.host.view.gesture(m.mid.x, m.mid.y, mid.x, mid.y, spacing / m.spacing, rolled - m.rolled)
    m.mid = mid
    m.spacing = spacing
    m.angle = angle
    m.rolled = rolled
    this.host.requestRender()
  }

  private readonly onUp = (e: PointerEvent): void => {
    if (this.swallowed.delete(e.pointerId)) return
    if (this.holding?.id === e.pointerId) this.clearHold()
    if (this.held === e.pointerId) {
      this.held = null
      if (e.pointerType === 'pen') this.penId = null
      else this.touches.delete(e.pointerId)
      this.hold?.release(this.penPoint(e))
      if (e.pointerType !== 'pen') this.afterTouchLeft()
      return
    }
    if (this.handleId === e.pointerId) {
      this.handleId = null
      this.handle?.release(this.penPoint(e))
      return
    }
    if (e.pointerType === 'pen') {
      if (this.penId !== e.pointerId) return
      this.penId = null
      this.pen?.commit(this.penPoint(e))
      return
    }
    if (e.pointerType === 'mouse') {
      if (this.mouseSelectId === e.pointerId) {
        this.mouseSelectId = null
        const press = this.leftPress
        this.leftPress = null
        const pt = this.penPoint(e)
        if (press?.double) return
        if (!press?.box && this.desktopMode) this.lastClick = { x: pt.x, y: pt.y, t: e.timeStamp }
        if (press?.box) this.mousePick?.boxEnd?.({ x0: press.x, y0: press.y, x1: pt.x, y1: pt.y }, pt.shift === true || pt.ctrl === true)
        else this.mousePick?.commit(pt)
        return
      }
      if (this.mouseId !== e.pointerId) return
      this.mouseId = null
      this.mode = { kind: 'idle' }
      const down = this.rightDown
      this.rightDown = null
      if (down && e.button === 2) {
        const q = this.local(e)
        if (Math.hypot(q.x - down.x, q.y - down.y) <= TAP_SLOP) this.mousePick?.secondary?.(this.penPoint(e))
      }
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
      if (!this.tapHandler?.doubleTap?.(x, y)) this.host.fit()
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
    if (this.swallowed.delete(e.pointerId)) return
    if (this.holding?.id === e.pointerId) this.clearHold()
    if (this.held === e.pointerId) {
      this.held = null
      if (e.pointerType === 'pen') this.penId = null
      else {
        this.touches.delete(e.pointerId)
        this.afterTouchLeft()
      }
      this.hold?.cancel()
      return
    }
    if (this.handleId === e.pointerId) {
      this.handleId = null
      this.handle?.cancel()
      return
    }
    if (e.pointerType === 'pen') {
      if (this.penId !== e.pointerId) return
      this.penId = null
      this.pen?.cancel()
      return
    }
    if (e.pointerType === 'mouse') {
      if (this.mouseSelectId === e.pointerId) {
        this.mouseSelectId = null
        const press = this.leftPress
        this.leftPress = null
        if (press?.box) this.mousePick?.boxCancel?.()
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
    if (e.pointerType !== 'mouse') return
    this.cursorAt?.(null)
    if (this.mouseSelectId === null && this.mouseId === null) this.mousePick?.cancel()
  }

  private readonly onWheel = (e: WheelEvent): void => {
    e.preventDefault()
    const unit = e.deltaMode === 1 ? LINE_PIXELS : e.deltaMode === 2 ? this.el.clientHeight : 1
    const p = this.local(e)
    this.host.view.zoomAt(Math.exp(-e.deltaY * unit * WHEEL_RATE), p.x, p.y)
    this.host.requestRender()
  }
}
