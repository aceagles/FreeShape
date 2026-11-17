import type { HandleHandler, PenHandler, PenPoint, PointerModule, TapHandler } from '../input/pointer'
import { canvasFramePoint } from '../viewport/canvases'
import type { CanvasSpec, FramePoint } from '../viewport/canvases'
import type { ViewCamera } from '../viewport/camera'
import { viewRay } from '../viewport/pick'

/**
 * The ruler of Calibrate (card I12.6): two ends on the canvas being edited, a line between them and its length, and the
 * request that scales the canvas so the line measures what the person says it does (freeshape/canvas_calibrate.py, I12.2).
 *
 * The frame. An end is a point of the canvas's own frame: millimetres from the image centre along the drawn x (left to right)
 * and the drawn y (bottom to top), flips and Rotation already applied (`CanvasSpec.x` and `y`). It is what the server takes as
 * `p0` and `p1`, and the length drawn is the length in that frame, so it is the canvas's length at its current size.
 *
 * Pointers. The tool takes the pen, mouse, finger and Drag handle slots of the one pointer module through its ruler mode
 * (`PointerModule.setRulerMode`); it adds no listener. The same gesture serves the mouse and the pen: a press places the first end
 * and, once it has moved past DRAG_SLOP, the line follows it and its release places the second (press, drag, release); a press that
 * does not move places the first end on release (click, or tap), and the next press or click places the second on release.
 * A finger tap is a press and a release at once. With both ends set the line is waiting for its distance and each end is a Drag
 * handle. Shift (mouse only) snaps the line to SNAP_STEP degree steps.
 */

/** A line the Shift key snaps to a multiple of this many degrees. */
export const SNAP_STEP = 5
/** A press that moves farther than this (CSS pixels) before it lifts is a drag; one that does not is a click or a tap. */
export const DRAG_SLOP = 8
/** The two ends must be at least this far apart on screen: closer is one point, not a line. */
export const MIN_SEPARATION = 4
/** How near (CSS pixels) a press must be to an end to take it, for the mouse and for a pen or a finger. */
export const GRAB_MOUSE = 12
export const GRAB_TOUCH = 28

export type End = FramePoint
export type RulerPhase = 'first' | 'second' | 'set'

export interface RulerState {
  phase: RulerPhase
  /** The first end; null until a press has landed on the canvas. */
  a: End | null
  /** The second end: set (phase 'set'), or the free end following the pointer; null when there is none. */
  b: End | null
  /** The length of the line in mm at the canvas's current size; null with no line. */
  length: number | null
  /** The mouse's position on the canvas before the first end is placed (drawn as a mark). */
  hover: End | null
  /** Where a pen is pressed or an end is dragged by a pen, in view pixels: the magnifier follows it. */
  loupe: { x: number; y: number } | null
  /** Why the last press did nothing, or null. */
  note: string | null
}

/** What the ruler needs from the viewport it measures on. */
export interface RulerHost {
  readonly pointer: Pick<PointerModule, 'setRulerMode'>
  readonly view: ViewCamera
  /** The canvas being edited as it is drawn now (the dialog's preview), or null when there is none. Asked at every event. */
  canvas(): CanvasSpec | null
}

/** What the ruler's picture needs besides the host: where to draw, the view's own canvas for the magnifier, and when a frame was drawn. */
export interface RulerView extends RulerHost {
  /** The view's box: the ruler's marks are drawn in an element laid over it. */
  readonly element: HTMLElement
  /** The 3D view's own canvas, which the magnifier copies from right after a frame is drawn. */
  readonly surface: HTMLCanvasElement
  /** Called after each frame the view draws. Returns the way to stop. */
  onFrame(fn: () => void): () => void
  requestRender(): void
}

/** The calibrate request body (I12.2): the ends in the canvas frame and the real distance in mm. */
export interface CalibrateBody {
  p0: [number, number]
  p1: [number, number]
  distance: number
  centre: boolean
  snap: boolean
}

const rad = (d: number): number => (d * Math.PI) / 180

/** The end `to` turned about `from` to the nearest multiple of `step` degrees, its distance from `from` kept. */
export function snapAngle(from: End, to: End, step: number = SNAP_STEP): End {
  const dx = to.u - from.u
  const dy = to.v - from.v
  const len = Math.hypot(dx, dy)
  if (len === 0) return to
  const a = Math.round(Math.atan2(dy, dx) / rad(step)) * rad(step)
  return { u: from.u + len * Math.cos(a), v: from.v + len * Math.sin(a) }
}

/** The length of the line between two ends, mm. */
export const lengthOf = (a: End, b: End): number => Math.hypot(b.u - a.u, b.v - a.v)

/** Where an end is on the screen (CSS pixels from the view's top left). */
export function endOnScreen(spec: CanvasSpec, view: ViewCamera, e: End): { x: number; y: number } {
  const p = spec.origin.clone().addScaledVector(spec.x, e.u).addScaledVector(spec.y, e.v)
  return view.project([p.x, p.y, p.z])
}

type Source = 'pen' | 'mouse' | 'touch'

export class RulerTool {
  private st: RulerState = { phase: 'first', a: null, b: null, length: null, hover: null, loupe: null, note: null }
  private readonly listeners = new Set<() => void>()
  private gesture: { x0: number; y0: number; drag: boolean; source: Source } | null = null
  private grab: { which: 'a' | 'b'; before: End } | null = null
  private running = false

  constructor(private readonly host: RulerHost) {}

  get state(): RulerState {
    return this.st
  }

  /** Called after every change of the state. Returns the way to stop. */
  subscribe(fn: () => void): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  /** Takes the pointer module's pen, mouse, finger and Drag handle slots. */
  start(): void {
    if (this.running) return
    this.running = true
    this.host.pointer.setRulerMode({ pen: this.pen, mouse: this.mouse, tap: this.tap, handle: this.handle })
  }

  /** Gives them back. The ends are kept; `reset` clears them. */
  stop(): void {
    if (!this.running) return
    this.running = false
    this.gesture = null
    this.grab = null
    this.host.pointer.setRulerMode(null)
    this.set({ loupe: null, hover: null })
  }

  /** Both ends away, ready for the first. */
  reset(): void {
    this.gesture = null
    this.grab = null
    this.st = { phase: 'first', a: null, b: null, length: null, hover: null, loupe: null, note: null }
    this.emit()
  }

  /** The request that scales the canvas, or null while there is no line. `distance` is in mm. */
  body(distance: number, centre: boolean, snap: boolean): CalibrateBody | null {
    const { a, b, phase } = this.st
    if (phase !== 'set' || a === null || b === null) return null
    return { p0: [a.u, a.v], p1: [b.u, b.v], distance, centre, snap }
  }

  private emit(): void {
    for (const fn of [...this.listeners]) fn()
  }

  private set(patch: Partial<RulerState>): void {
    const next = { ...this.st, ...patch }
    next.length = next.a !== null && next.b !== null ? lengthOf(next.a, next.b) : null
    this.st = next
    this.emit()
  }

  /**
   * The canvas frame point under a view position, or null when it misses the rectangle (or there is no canvas). With `from`
   * and `shift` the point is snapped to the line's 5 degree steps about `from`. With `clamp` a point beyond the rectangle is
   * moved to its edge (an end being dragged stays on the picture).
   */
  private at(x: number, y: number, opts: { from?: End | null; shift?: boolean; clamp?: boolean } = {}): End | null {
    const spec = this.host.canvas()
    if (spec === null) return null
    const hit = canvasFramePoint(spec, viewRay(this.host.view, x, y))
    if (hit === null) return null
    if (!hit.inside && opts.clamp !== true) return null
    let p: End = opts.clamp === true ? { u: Math.max(-spec.width / 2, Math.min(spec.width / 2, hit.u)), v: Math.max(-spec.height / 2, Math.min(spec.height / 2, hit.v)) } : { u: hit.u, v: hit.v }
    if (opts.shift === true && opts.from) p = snapAngle(opts.from, p)
    return p
  }

  /** Whether two ends are far enough apart on screen to be a line. */
  private apart(a: End, b: End): boolean {
    const spec = this.host.canvas()
    if (spec === null) return false
    const pa = endOnScreen(spec, this.host.view, a)
    const pb = endOnScreen(spec, this.host.view, b)
    return Math.hypot(pa.x - pb.x, pa.y - pb.y) >= MIN_SEPARATION
  }

  private static readonly MISS = 'Put the end on the picture.'
  private static readonly SAME = 'The two ends are the same point: put the second end away from the first.'

  private down(p: PenPoint, source: Source): void {
    if (this.st.phase === 'set') return
    this.gesture = { x0: p.x, y0: p.y, drag: false, source }
    const loupe = source === 'pen' ? { x: p.x, y: p.y } : null
    if (this.st.phase === 'first') this.set({ a: this.at(p.x, p.y), b: null, hover: null, loupe, note: null })
    else this.set({ b: this.at(p.x, p.y, { from: this.st.a, shift: p.shift }), loupe, note: null })
  }

  private move(p: PenPoint, source: Source): void {
    const g = this.gesture
    if (g === null) {
      // the mouse over the view, no button: the line follows it (a pen has no hover, and none is relied on)
      if (source !== 'mouse') return
      if (this.st.phase === 'second') this.set({ b: this.at(p.x, p.y, { from: this.st.a, shift: p.shift }) })
      else if (this.st.phase === 'first') this.set({ hover: this.at(p.x, p.y) })
      return
    }
    if (!g.drag && Math.hypot(p.x - g.x0, p.y - g.y0) > DRAG_SLOP) g.drag = true
    const loupe = g.source === 'pen' ? { x: p.x, y: p.y } : null
    if (this.st.phase === 'first') {
      // the first end stays where the press landed; once the press is a drag the second follows it
      if (g.drag && this.st.a !== null) this.set({ b: this.at(p.x, p.y, { from: this.st.a, shift: p.shift }), loupe })
      else this.set({ loupe })
    } else if (this.st.phase === 'second') this.set({ b: this.at(p.x, p.y, { from: this.st.a, shift: p.shift }), loupe })
  }

  private up(p: PenPoint): void {
    const g = this.gesture
    this.gesture = null
    if (g === null) return
    const { phase, a } = this.st
    if (phase === 'first') {
      if (a === null) return this.set({ b: null, loupe: null, note: RulerTool.MISS })
      if (g.drag) {
        const end = this.at(p.x, p.y, { from: a, shift: p.shift })
        if (end !== null && this.apart(a, end)) return this.set({ b: end, phase: 'set', loupe: null, note: null })
        // the drag ended off the picture or on the first end: the first end stays and waits for its second
        return this.set({ b: null, phase: 'second', loupe: null, note: end === null ? RulerTool.MISS : RulerTool.SAME })
      }
      // a click or a tap: the first end is where it lifted (where it went down, if it lifted off the picture)
      return this.set({ a: this.at(p.x, p.y) ?? a, b: null, phase: 'second', loupe: null, note: null })
    }
    if (phase === 'second' && a !== null) {
      const end = this.at(p.x, p.y, { from: a, shift: p.shift })
      if (end === null) return this.set({ b: null, loupe: null, note: RulerTool.MISS })
      if (!this.apart(a, end)) return this.set({ b: null, loupe: null, note: RulerTool.SAME })
      this.set({ b: end, phase: 'set', loupe: null, note: null })
    }
  }

  private abandon(): void {
    this.gesture = null
    // a press that is cancelled places nothing: the end it had shown goes
    if (this.st.phase === 'first') this.set({ a: null, b: null, hover: null, loupe: null })
    else if (this.st.phase === 'second') this.set({ b: null, loupe: null })
    else this.set({ loupe: null })
  }

  /** The pen's Aim is its press and every move while it is down; its release is the Commit. */
  private readonly pen: PenHandler = {
    aim: (p) => (this.gesture === null ? this.down(p, 'pen') : this.move(p, 'pen')),
    commit: (p) => this.up(p),
    cancel: () => this.abandon(),
  }

  /** The mouse: a hover moves the line, a press and its release click an end. No `doubleClick`: two clicks are two clicks. */
  private readonly mouse: PenHandler = {
    press: (p) => this.down(p, 'mouse'),
    aim: (p) => this.move(p, 'mouse'),
    commit: (p) => this.up(p),
    // the mouse left the view with no button down: the line stops following it
    cancel: () => (this.gesture === null ? this.set(this.st.phase === 'second' ? { b: null } : { hover: null }) : this.abandon()),
  }

  /** A finger tap is a press and a release at once. The second of two quick taps is a tap too, not a view gesture. */
  private readonly tap: TapHandler = {
    tap: (x, y) => {
      this.down({ x, y, pressure: 0, pointerId: 0 }, 'touch')
      this.up({ x, y, pressure: 0, pointerId: 0 })
    },
    undoTap: () => {},
    doubleTap: (x, y) => {
      this.tap.tap(x, y)
      return true
    },
  }

  /** With both ends set, a press near one takes it: drag it, and the line and its length follow. */
  private readonly handle: HandleHandler = {
    press: (p, pointer) => {
      const spec = this.host.canvas()
      const { phase, a, b } = this.st
      if (phase !== 'set' || spec === null || a === null || b === null) return false
      const reach = pointer === 'mouse' ? GRAB_MOUSE : GRAB_TOUCH
      const da = endOnScreen(spec, this.host.view, a)
      const db = endOnScreen(spec, this.host.view, b)
      const fa = Math.hypot(da.x - p.x, da.y - p.y)
      const fb = Math.hypot(db.x - p.x, db.y - p.y)
      if (Math.min(fa, fb) > reach) return false
      const which = fa <= fb ? 'a' : 'b'
      this.grab = { which, before: which === 'a' ? a : b }
      this.set({ loupe: pointer === 'pen' ? { x: p.x, y: p.y } : null, note: null })
      return true
    },
    move: (p) => this.dragEnd(p, false),
    release: (p) => this.dragEnd(p, true),
    cancel: () => {
      const g = this.grab
      this.grab = null
      if (g) this.set(g.which === 'a' ? { a: g.before, loupe: null } : { b: g.before, loupe: null })
    },
  }

  private dragEnd(p: PenPoint, done: boolean): void {
    const g = this.grab
    if (g === null) return
    const other = g.which === 'a' ? this.st.b : this.st.a
    const end = this.at(p.x, p.y, { from: other, shift: p.shift, clamp: true })
    const patch: Partial<RulerState> = {}
    if (end !== null && other !== null && this.apart(end, other)) {
      if (g.which === 'a') patch.a = end
      else patch.b = end
    }
    if (this.st.loupe !== null) patch.loupe = done ? null : { x: p.x, y: p.y }
    if (done) this.grab = null
    this.set(patch)
  }
}
