import type { DragTarget } from './select'
import type { SketchStore, SketchTransport } from './store'
import type { SketchData, Uv } from './wire'

/** A press that moves farther than this many CSS pixels drags the item under it (the pen); less is an Aim. Settled on the browser probe. */
export const DRAG_THRESHOLD = 6

/** Where the items of a drag are drawn: moved points, circle radii and dimension labels, over the sketch the server holds. */
export interface LiveDrag {
  points: Readonly<Record<string, Uv>>
  radii: Readonly<Record<string, number>>
  /** A dimension's label position (sketch coordinates), while its label is dragged. */
  labels: Readonly<Record<string, Uv>>
}

/** The sketch as a drag shows it: `live` laid over `data`. Returns `data` itself when there is nothing to lay over. */
export function applyLive(data: SketchData, live: LiveDrag | null): SketchData {
  if (live === null) return data
  const points = { ...data.points }
  for (const [id, p] of Object.entries(live.points)) if (id in points) points[id] = p
  const entities = { ...data.entities }
  for (const [id, r] of Object.entries(live.radii)) if (entities[id]) entities[id] = { ...entities[id]!, r }
  // a placed label sits where the drag put it: no anchor, so geometry that moves with it does not shift it again
  const constraints = data.constraints.map((c) => {
    const at = live.labels[c.id]
    if (!at) return c
    const placed: typeof c = { ...c, pos: at }
    delete placed.pos_anchor
    return placed
  })
  return { points, entities, constraints }
}

/** The points an entity is moved by, as freeshape/sketch.py entity_points has them. */
function entityPoints(data: SketchData, id: string): string[] {
  const e = data.entities[id]
  if (!e) return []
  return [e.p1, e.p2, e.c, e.p].filter((p): p is string => typeof p === 'string')
}

/** Where the dragged items are when the pointer is at `at` and the drag began at `start`: the server's rule (sketch_session.py drag_move) without the solve. */
export function predict(data: SketchData, target: DragTarget, start: Uv, at: Uv, origin: Uv | null = null): LiveDrag {
  const dx = at[0] - start[0]
  const dy = at[1] - start[1]
  const none = { points: {}, radii: {}, labels: {} }
  if (target.kind === 'point') {
    const p = data.points[target.id]
    return p ? { ...none, points: { [target.id]: [p[0] + dx, p[1] + dy] } } : none
  }
  if (target.kind === 'dimension') return origin ? { ...none, labels: { [target.id]: [origin[0] + dx, origin[1] + dy] } } : none
  const e = data.entities[target.id]
  if (!e) return none
  if (e.type === 'circle') {
    const c = data.points[e.c ?? '']
    return c ? { ...none, radii: { [target.id]: Math.max(Math.hypot(at[0] - c[0], at[1] - c[1]), 1e-3) } } : none
  }
  const points: Record<string, Uv> = {}
  for (const id of entityPoints(data, target.id)) {
    const p = data.points[id]
    if (p && id !== 'origin') points[id] = [p[0] + dx, p[1] + dy]
  }
  return { ...none, points }
}

interface Drag {
  target: DragTarget
  start: Uv
  at: Uv
  /** A dimension's label centre when the drag began. */
  origin: Uv | null
  moved: boolean
  /** The end has been sent: only the server's `sketch.changed` closes the drag. */
  ended: boolean
}

/**
 * A drag in the open sketch (I7.9): `sketch.drag.begin`, a `sketch.drag.move` for every pointer move, `sketch.drag.end`.
 * The server answers each move with the solved sketch (a move replaced by a later one gets no answer); until then the
 * dragged item is drawn where the pointer is, and the answer replaces it with the solved position. A dimension's label
 * is the exception: it moves the label alone, with one `sketch.dimension.set` at the end and no solve.
 */
export class SketchDrag {
  private cur: Drag | null = null
  private off: (() => void) | null = null

  constructor(
    private readonly transport: SketchTransport,
    private readonly store: SketchStore,
  ) {}

  attach(): void {
    this.off ??= this.transport.onSketch((m) => this.onMessage(m))
  }
  detach(): void {
    this.off?.()
    this.off = null
    this.cur = null
  }

  /** A drag is on (begun and not yet closed by the server). */
  get active(): boolean {
    return this.cur !== null
  }

  /** Start dragging `target` from the sketch point `at`; `labelAt` is a dimension label's centre now. False when a drag is on or the connection is down. */
  begin(target: DragTarget, at: Uv, labelAt: Uv | null = null): boolean {
    if (this.cur !== null || !this.store.getState().open) return false
    if (target.kind !== 'dimension') {
      const body = target.kind === 'point' ? { point: target.id } : { entity: target.id }
      if (!this.transport.send({ type: 'sketch.drag.begin', ...body, at })) return false
    }
    this.cur = { target, start: at, at, origin: labelAt, moved: false, ended: false }
    this.store.aim(null)
    return true
  }

  move(at: Uv): void {
    const d = this.cur
    const view = this.store.getState().view
    if (d === null || d.ended || view === null) return
    d.at = at
    d.moved = true
    this.store.setLive(predict(view.sketch, d.target, d.start, at, d.origin))
    if (d.target.kind !== 'dimension') this.transport.send({ type: 'sketch.drag.move', at, t: this.transport.stamp?.() ?? performance.now() })
  }

  end(cancel = false): void {
    const d = this.cur
    if (d === null || d.ended) return
    if (d.target.kind === 'dimension') {
      this.cur = null
      if (!cancel && d.moved) {
        // the label stays where it was dropped until the server's answer redraws the sketch with it
        this.transport.send({ type: 'sketch.dimension.set', constraint: d.target.id, pos: [d.at[0] - d.start[0] + (d.origin?.[0] ?? 0), d.at[1] - d.start[1] + (d.origin?.[1] ?? 0)] })
      } else this.store.setLive(null)
      return
    }
    d.ended = true
    this.transport.send(cancel ? { type: 'sketch.drag.end', cancel: true } : { type: 'sketch.drag.end' })
  }

  private onMessage(m: Record<string, unknown>): void {
    const d = this.cur
    switch (m['type']) {
      case 'sketch.drag.moved': {
        if (d === null || d.target.kind === 'dimension') return
        const points = m['points']
        const radii = m['radii']
        if (typeof points !== 'object' || points === null || typeof radii !== 'object' || radii === null) return
        this.store.setLive({ points: points as Record<string, Uv>, radii: radii as Record<string, number>, labels: {} })
        return
      }
      case 'sketch.changed':
      case 'sketch.opened':
      case 'sketch.state':
      case 'sketch.cancelled':
      case 'sketch.committed':
        this.cur = null
        return
      case 'error':
        if (typeof m['for'] === 'string' && m['for'].startsWith('sketch.drag.')) {
          this.cur = null
          this.store.setLive(null)
        }
        return
    }
  }
}

/** What a pointer's drag needs of the view it happens in. */
export interface DragView {
  /** The sketch point under a view position (CSS pixels), or null when the view looks along the plane. */
  uvAt(x: number, y: number): Uv | null
  /** The centre of a dimension's label in sketch coordinates now, or null. */
  labelUv(id: string): Uv | null
}

/**
 * One pointer's press on a movable item, from the press to the lift. It waits until the pointer has moved more than
 * `threshold` pixels from the press and only then begins the drag (at the sketch point of the press, so the item does not
 * jump); a press that stays inside is no drag, and the owner treats it as an Aim or a tap. Used by the pen's direct drag
 * and by the Drag handle.
 */
export class PointerDrag {
  private press: { target: DragTarget; x: number; y: number; threshold: number } | null = null
  private dragging = false
  private last = { x: NaN, y: NaN }

  constructor(
    private readonly store: SketchStore,
    private readonly view: DragView,
  ) {}

  /** A press on `target`; a drag is already on (or none can start): false. */
  arm(target: DragTarget, x: number, y: number, threshold = DRAG_THRESHOLD): boolean {
    if (this.press !== null || this.store.drag.active) return false
    this.press = { target, x, y, threshold }
    this.dragging = false
    return true
  }

  /** Armed and not lifted yet. */
  get armed(): boolean {
    return this.press !== null
  }
  /** The pointer has gone far enough: the item follows it. */
  get active(): boolean {
    return this.dragging
  }

  move(x: number, y: number): void {
    const pr = this.press
    if (pr === null) return
    if (!this.dragging) {
      if (Math.hypot(x - pr.x, y - pr.y) <= pr.threshold) return
      const at = this.view.uvAt(pr.x, pr.y)
      const label = pr.target.kind === 'dimension' ? this.view.labelUv(pr.target.id) : null
      if (at === null || (pr.target.kind === 'dimension' && label === null) || !this.store.drag.begin(pr.target, at, label)) {
        this.press = null
        return
      }
      this.dragging = true
    }
    this.last = { x, y }
    const uv = this.view.uvAt(x, y)
    if (uv !== null) this.store.drag.move(uv)
  }

  /** The pointer lifted: true when a drag was on (and is ended). */
  release(x: number, y: number): boolean {
    // the lift usually lands where the last move did
    if (this.dragging && (x !== this.last.x || y !== this.last.y)) this.move(x, y)
    return this.finish(false)
  }

  /** The pointer was taken away: the drag ends without keeping its result. */
  cancel(): void {
    this.finish(true)
  }

  private finish(cancel: boolean): boolean {
    const was = this.dragging
    if (was) this.store.drag.end(cancel)
    this.press = null
    this.dragging = false
    return was
  }
}
