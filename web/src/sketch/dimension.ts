import { isSketchToolChord } from '../input/bindings'
import { chordOf, runKeyChord } from '../input/keys'
import type { PickPointer } from '../viewport/pick'
import { formatValue } from './draw'
import { DRAG_THRESHOLD } from './drag'
import { PREVIEW_DIM } from './pick'
import type { SketchHit } from './pick'
import type { SketchPointerTool, SketchStore, SketchTransport } from './store'
import type { SketchConstraint, SketchData, Uv } from './wire'

/** The kinds of dimension the server makes (sketch.dimension's `made.type`). */
export type DimensionType = 'distance' | 'hdistance' | 'vdistance' | 'radius' | 'diameter' | 'angle'

/** What a dimension on the picks would be: its type and the value it measures now (mm; degrees for an angle). */
export interface DimensionSpec {
  type: DimensionType
  value: number
}

const sub = (a: Uv, b: Uv): Uv => [a[0] - b[0], a[1] - b[1]]
const len = (a: Uv): number => Math.hypot(a[0], a[1])
const cross = (a: Uv, b: Uv): number => a[0] * b[1] - a[1] * b[0]

/**
 * The dimension the picks take, as the server's `sketch_ops.dim_spec` decides it (the desktop's Dimension tool): one line
 * its length, one circle its diameter, one arc its radius; two lines an angle, or their distance when parallel; a point
 * (or a circle's or arc's centre) and a line, or two points, a distance. Two points far apart in both directions take a
 * horizontal or vertical distance when `pos` lies beside their box. Null when the picks take no dimension.
 * `a` and `b` are ids of points or entities; `b` is null for one item.
 */
export function dimensionSpec(sk: SketchData, a: string, b: string | null, pos: Uv | null): DimensionSpec | null {
  const kind = (id: string): string | null => (id in sk.points ? 'point' : (sk.entities[id]?.type ?? null))
  const P = (id: string | undefined): Uv | null => (id === undefined ? null : (sk.points[id] ?? null))
  /** The point an item stands for: itself, a point entity's point, a circle's or arc's centre, a text's start. */
  const pointOf = (id: string, t: string | null): Uv | null => {
    if (t === 'point') return id in sk.points ? sk.points[id]! : P(sk.entities[id]?.p)
    if (t === 'circle' || t === 'arc' || t === 'ellipse') return P(sk.entities[id]?.c)
    if (t === 'text') return P(sk.entities[id]?.p)
    return null
  }
  const ends = (id: string): [Uv, Uv] | null => {
    const e = sk.entities[id]
    const p1 = P(e?.p1)
    const p2 = P(e?.p2)
    return p1 && p2 ? [p1, p2] : null
  }
  const lineDistance = (p: Uv, l: [Uv, Uv]): number => {
    const d = sub(l[1], l[0])
    return Math.abs(cross(d, sub(p, l[0]))) / (len(d) || 1)
  }
  const ta = kind(a)
  const tb = b === null ? null : kind(b)
  if (ta === null || (b !== null && tb === null)) return null
  if (b === null) {
    const e = sk.entities[a]
    if (ta === 'line') {
      const l = ends(a)
      return l ? { type: 'distance', value: len(sub(l[1], l[0])) } : null
    }
    if (ta === 'circle' && e?.r !== undefined) return { type: 'diameter', value: 2 * e.r }
    if (ta === 'arc') {
      const c = P(e?.c)
      const p1 = P(e?.p1)
      return c && p1 ? { type: 'radius', value: len(sub(p1, c)) } : null
    }
    if (ta === 'ellipse' && e?.r !== undefined) return { type: 'radius', value: e.r }
    return null
  }
  if (ta === 'line' && tb === 'line') {
    const l1 = ends(a)
    const l2 = ends(b)
    if (!l1 || !l2) return null
    const d1 = sub(l1[1], l1[0])
    const d2 = sub(l2[1], l2[0])
    const n1 = len(d1)
    const n2 = len(d2)
    if (n1 === 0 || n2 === 0) return null
    const cr = Math.abs(cross(d1, d2)) / (n1 * n2)
    if (cr < 1e-3) return { type: 'distance', value: lineDistance(l2[0], l1) }
    const dt = (d1[0] * d2[0] + d1[1] * d2[1]) / (n1 * n2)
    return { type: 'angle', value: (Math.atan2(cr, dt) * 180) / Math.PI }
  }
  if (ta === 'line' || tb === 'line') {
    const line = ta === 'line' ? a : b
    const other = ta === 'line' ? b : a
    const p = pointOf(other, ta === 'line' ? tb : ta)
    const l = ends(line)
    return p && l ? { type: 'distance', value: lineDistance(p, l) } : null
  }
  const A = pointOf(a, ta)
  const B = pointOf(b, tb)
  if (!A || !B) return null
  let type: DimensionType = 'distance'
  if (pos !== null && Math.abs(A[0] - B[0]) > 1e-6 && Math.abs(A[1] - B[1]) > 1e-6) {
    const inU = Math.min(A[0], B[0]) <= pos[0] && pos[0] <= Math.max(A[0], B[0])
    const inV = Math.min(A[1], B[1]) <= pos[1] && pos[1] <= Math.max(A[1], B[1])
    if (inU && !inV) type = 'hdistance'
    else if (inV && !inU) type = 'vdistance'
  }
  const value = type === 'hdistance' ? Math.abs(A[0] - B[0]) : type === 'vdistance' ? Math.abs(A[1] - B[1]) : len(sub(A, B))
  return { type, value }
}

/** The text a pad opens with for a value: "25 mm", "45 deg". */
export const padText = (type: string, value: number): string => `${formatValue(value)} ${type === 'angle' ? 'deg' : 'mm'}`

const LABELS: Record<string, string> = { distance: 'Distance', hdistance: 'Horizontal distance', vdistance: 'Vertical distance', radius: 'Radius', diameter: 'Diameter', angle: 'Angle', offset: 'Offset' }

/** A number typed plainly goes as a JSON number; anything else (units, 2*10, #width) goes as the text the user typed, for the server to evaluate. */
export const valueOf = (text: string): number | string => {
  const t = text.trim()
  return /^[+-]?(\d+\.?\d*|\.\d+)$/.test(t) ? Number(t) : t
}

/** The pad session of the Dimension tool: a new dimension being given its value, or a placed one being changed. */
export interface DimensionPadState {
  mode: 'new' | 'set'
  /** The field's label: "Length", "Diameter", "Vertical distance". */
  label: string
  /** What the field holds when it opens (after a refusal: what was tried). */
  value: string
  /** What the dimension measures or holds now; a "new" dimension sent unedited takes the server's measure. */
  initial: string
  /** The answer to the last Enter is awaited. */
  pending: boolean
  /** The server's refusal of the last Enter, in words. */
  error: string | null
  /** Bumps on a refusal so the pad field starts again on the text that was tried. */
  seq: number
  /** Desktop mode with a mouse (card I9.16): the value is typed in the inline box at the dimension, not on the pad, and the dialog area stays shut. */
  inline?: true
}

export interface DimensionState {
  /** The Dimension tool is the tool on. */
  active: boolean
  /** The items picked so far (at most two). */
  picks: readonly SketchHit[]
  pad: DimensionPadState | null
  /** What the tool wants next, in words; null when it is off or the pad is open. */
  hint: string | null
}

const OFF: DimensionState = { active: false, picks: [], pad: null, hint: null }
/** Two Commits within this time (ms) on one label are a double tap, for the pen and the mouse (a finger's double tap comes from the pointer module). */
const DOUBLE_MS = 400

export interface DimensionDeps {
  sketch: SketchStore
  transport: SketchTransport
  /** Open the pad in the dialog area (it renders <DimensionPad>); and close it. */
  openPad: (tool: DimensionTool) => void
  closePad: () => void
  now?: () => number
}

/**
 * The Dimension tool (I7.11). Commit on one item (a line, a circle, an arc) or on two (two points, a point and a line, two
 * lines), then Commit once more where the label goes: the pad opens on the measured value and Enter sends sketch.dimension.
 * Where the label goes decides distance, horizontal or vertical distance for two points, as on the desktop. A double tap on
 * a dimension's label opens the pad on its value (any tool) and Enter sends sketch.dimension.set. A refusal keeps the pad open
 * on what was tried, with the server's words, and the sketch as it was.
 */
export class DimensionTool implements SketchPointerTool {
  private state: DimensionState = OFF
  private readonly listeners = new Set<() => void>()
  private off: (() => void)[] = []
  private last: { id: string; t: number } | null = null
  /** What the pad's Enter was for, to match the answer. */
  private sent: { type: string; constraint?: string } | null = null
  /** Where the label of the pending dimension goes. */
  private placing: { picks: string[]; pos: Uv | null; kind?: DimensionType } | null = null
  private target: string | null = null
  /** Desktop mode: a mouse's dimension takes its value in the inline box (setDesktop). */
  private desktop = false
  /** Desktop mode, a mouse press in the view: what it went down on and how it has gone since (F22). */
  private gesture: { picked: boolean; uv: Uv | null; moved: boolean; dropped: boolean } | null = null
  /** The preview of the dimension being placed is on the view (it is the store's `live`). */
  private previewKey: string | null = null
  private readonly now: () => number

  constructor(private readonly deps: DimensionDeps) {
    this.now = deps.now ?? (() => Date.now())
    deps.sketch.pointerTool = this
  }

  attach(): void {
    if (this.off.length > 0) return
    this.deps.sketch.pointerTool = this
    this.off = [this.deps.transport.onSketch((m) => this.onMessage(m)), this.deps.sketch.subscribe(() => this.onSketch())]
  }
  detach(): void {
    for (const f of this.off) f()
    this.off = []
    if (this.deps.sketch.pointerTool === this) this.deps.sketch.pointerTool = null
  }

  readonly subscribe = (l: () => void): (() => void) => {
    this.listeners.add(l)
    return () => this.listeners.delete(l)
  }
  readonly getState = (): DimensionState => this.state

  private set(patch: Partial<DimensionState>): void {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach((l) => l())
  }

  /** Desktop mode is on (true) or Tablet mode is (false). A switch closes an open box or pad: nothing is half done. */
  setDesktop(on: boolean): void {
    if (on === this.desktop) return
    if (this.state.pad !== null) this.padCancel()
    this.desktop = on
    this.gesture = null
    this.hidePreview()
    if (this.state.active && this.state.hint !== null && this.state.pad === null) this.set({ hint: this.hintFor(this.state.picks) })
  }

  /** The tool is chosen on the strip (true) or another one is (false). */
  setActive(on: boolean): void {
    if (on === this.state.active) return
    this.closePad()
    this.clearPicks()
    this.gesture = null
    this.hidePreview()
    this.set({ active: on, hint: on ? this.hintFor([]) : null })
  }

  private hintFor(picks: readonly SketchHit[]): string {
    const sk = this.deps.sketch.getState().view?.sketch
    // Desktop mode is a mouse's: Click, and a press that drags to the place (F22)
    const verb = this.desktop ? 'Click' : 'Tap'
    if (picks.length === 0) return this.desktop ? 'Click a line, circle, arc or point, or press on it and drag to where the dimension goes' : 'Pick a line, circle, arc or point to dimension'
    if (picks.length === 2) return `${verb} where the dimension goes`
    const first = picks[0]!
    return sk && dimensionSpec(sk, first.id, null, null) ? `${verb} where the dimension goes, or ${this.desktop ? 'click' : 'pick'} a second item` : this.desktop ? 'Click a second point or a line' : 'Pick a second point or a line'
  }

  /** The dimension the picks take with its label at `at`, as the view draws it before it is placed; null when they take none. */
  private previewOf(picks: readonly SketchHit[], at: Uv): SketchConstraint | null {
    const sk = this.deps.sketch.getState().view?.sketch
    if (!sk || picks.length === 0) return null
    const refs = picks.map((p) => p.id)
    const spec = dimensionSpec(sk, refs[0]!, refs[1] ?? null, at)
    return spec === null ? null : { id: PREVIEW_DIM, type: spec.type, refs, value: spec.value, pos: at }
  }

  private showPreview(c: SketchConstraint | null): void {
    const key = c === null ? null : JSON.stringify(c)
    if (key === this.previewKey) return
    this.previewKey = key
    this.deps.sketch.setLive(c === null ? null : { points: {}, radii: {}, labels: {}, extra: [c] })
  }

  private hidePreview(): void {
    this.showPreview(null)
  }

  /**
   * The mouse's Aim, Desktop mode (F22): once an item is picked the dimension follows the pointer, drawn as a placed one is, so
   * the next click clearly places it (the desktop's `_dim_over_item`: while the pointer is over an item it stays off, so the
   * item can be picked as the second). A press-drag past the slop marks the gesture as a drag.
   */
  aim(hit: SketchHit | null, uv: Uv | null, pointer: PickPointer, mmPerPx?: number): void {
    const s = this.state
    if (!this.desktop || pointer !== 'mouse' || !s.active || s.pad !== null || uv === null) return this.hidePreview()
    const g = this.gesture
    if (g && !g.moved && g.uv && Math.hypot(uv[0] - g.uv[0], uv[1] - g.uv[1]) > DRAG_THRESHOLD * (mmPerPx ?? 0)) g.moved = true
    const over = hit !== null && (hit.kind === 'point' || hit.kind === 'entity')
    this.showPreview(s.picks.length === 2 || (s.picks.length === 1 && !over) ? this.previewOf(s.picks, uv) : null)
  }

  /** The mouse went down (Desktop mode): an item under it is picked at once, as the desktop picks on a press; the release then places it if the press dragged. */
  press(hit: SketchHit | null, uv: Uv | null, pointer: PickPointer): void {
    this.gesture = null
    if (!this.desktop || pointer !== 'mouse' || !this.state.active || this.state.pad !== null || !this.deps.sketch.getState().view) return
    const item = hit !== null && (hit.kind === 'point' || hit.kind === 'entity') ? hit : null
    const picked = this.state.picks.length === 0 && item !== null
    if (picked) this.setPicks([item])
    this.gesture = { picked, uv, moved: false, dropped: false }
  }

  private clearPicks(): void {
    if (this.state.picks.length > 0) {
      this.set({ picks: [] })
      this.deps.sketch.clearSelection()
    }
  }

  private setPicks(picks: readonly SketchHit[], hint?: string): void {
    this.set({ picks, hint: hint ?? this.hintFor(picks) })
    this.deps.sketch.setSelection(picks)
  }

  /** The sketch closed under the tool: nothing is half done. */
  private onSketch(): void {
    if (!this.deps.sketch.getState().open && (this.state.active || this.state.pad !== null || this.state.picks.length > 0)) {
      this.closePad()
      this.set({ ...OFF })
    }
  }

  /** The pointer's Commit: true when the tool took it (the selection then does nothing). */
  commit(hit: SketchHit | null, uv: Uv | null, pointer: PickPointer, mmPerPx?: number): boolean {
    const g = this.gesture
    this.gesture = null
    this.hidePreview()
    if (g && this.desktop && pointer === 'mouse' && this.state.active) {
      if (g.dropped) return true // Esc during the press: nothing is picked or placed by its release
      if (g.uv && uv && Math.hypot(uv[0] - g.uv[0], uv[1] - g.uv[1]) > DRAG_THRESHOLD * (mmPerPx ?? 0)) g.moved = true
      // a press that picked an item and let go in place is only the pick; the label goes at the next click
      if (g.picked && !g.moved) return true
    }
    if (pointer !== 'touch' && hit?.kind === 'dimension') {
      const prev = this.last
      const t = this.now()
      this.last = { id: hit.id, t }
      if (prev && prev.id === hit.id && t - prev.t < DOUBLE_MS) {
        this.last = null
        if (this.doubleTap(hit, pointer)) return true
      }
    } else this.last = null
    const s = this.state
    if (!s.active) return false
    // the pad has the say while it is open; a Commit in the view neither picks nor places
    if (s.pad !== null) return true
    const sk = this.deps.sketch.getState().view?.sketch
    if (!sk) return true
    const item = hit !== null && (hit.kind === 'point' || hit.kind === 'entity') ? hit : null
    const picks = s.picks
    if (picks.length === 0) {
      if (item) this.setPicks([item])
      return true
    }
    const first = picks[0]!
    if (picks.length === 1 && item && item.id !== first.id && !this.isEndOf(sk, item, first)) {
      if (dimensionSpec(sk, first.id, item.id, null) === null) {
        this.set({ hint: 'Those two items take no dimension. Pick another, or Select to start over' })
        return true
      }
      this.setPicks([first, item])
      return true
    }
    // a Commit anywhere else places the label
    if (picks.length === 1 && dimensionSpec(sk, first.id, null, null) === null) {
      this.set({ hint: this.hintFor(picks) })
      return true
    }
    this.place(sk, picks.map((p) => p.id), uv, pointer)
    return true
  }

  /** `item` is a point of the line `first`: the end of the very line being dimensioned is no second item. */
  private isEndOf(sk: SketchData, item: SketchHit, first: SketchHit): boolean {
    const e = sk.entities[first.id]
    return item.kind === 'point' && e !== undefined && (e.p1 === item.id || e.p2 === item.id)
  }

  private place(sk: SketchData, picks: string[], uv: Uv | null, pointer: PickPointer): void {
    const spec = dimensionSpec(sk, picks[0]!, picks[1] ?? null, uv)
    if (spec === null) {
      this.clearPicks()
      this.set({ hint: 'Those items take no dimension' })
      return
    }
    const kind = spec.type === 'hdistance' || spec.type === 'vdistance' ? spec.type : undefined
    this.placing = { picks, pos: uv, ...(kind ? { kind } : {}) }
    const text = padText(spec.type, spec.value)
    const label = picks.length === 1 && spec.type === 'distance' ? 'Length' : (LABELS[spec.type] ?? 'Value')
    this.target = null
    this.sent = null
    this.openSession({ mode: 'new', label, value: text, initial: text, pending: false, error: null, seq: 0 }, pointer)
  }

  /** Shows the value's session: in the inline box for a mouse in Desktop mode, else on the pad in the dialog area. */
  private openSession(pad: DimensionPadState, pointer: PickPointer): void {
    const inline = this.desktop && pointer === 'mouse'
    this.set({ pad: inline ? { ...pad, inline } : pad, hint: null })
    if (!inline) this.deps.openPad(this)
  }

  /** Where the inline box goes: the label of the dimension being changed (its id), or the point where the new label goes. */
  inlineAnchor(): { constraint: string } | { uv: Uv; picks: readonly string[]; points: Readonly<Record<string, Uv>> } | null {
    if (this.state.pad?.inline !== true) return null
    if (this.target !== null) return { constraint: this.target }
    const p = this.placing
    const points = this.deps.sketch.getState().view?.sketch.points ?? {}
    return p === null ? null : { uv: p.pos ?? [0, 0], picks: p.picks, points }
  }

  /** A double tap (or double click) on a dimension's label: the pad (Desktop mode with a mouse: the inline box) opens on its value. True when it did. */
  doubleTap(hit: SketchHit, pointer: PickPointer = 'touch'): boolean {
    if (hit.kind !== 'dimension') return false
    const c = this.deps.sketch.getState().view?.sketch.constraints.find((k) => k.id === hit.id)
    if (!c || c.value === undefined) return false
    this.closePad()
    this.clearPicks()
    // a dimension made from an expression opens on that text, so it can be edited
    const text = c.expr ?? padText(c.type, c.value)
    this.placing = null
    this.target = c.id
    this.sent = null
    this.openSession({ mode: 'set', label: c.type === 'distance' && c.refs.length === 1 ? 'Length' : (LABELS[c.type] ?? 'Value'), value: text, initial: text, pending: false, error: null, seq: 0 }, pointer)
    return true
  }

  /**
   * Esc (Desktop mode): the open value box or pad closes, or else the picks made so far are dropped and the tool stays on, as the
   * desktop's escape does. True when there was something to close.
   */
  escape(): boolean {
    if (this.gesture) this.gesture.dropped = true
    this.hidePreview()
    if (this.state.pad !== null) {
      this.padCancel()
      return true
    }
    if (this.state.picks.length === 0) return false
    this.clearPicks()
    this.set({ hint: this.hintFor([]) })
    return true
  }

  /**
   * A key pressed in a new dimension's value box or pad while the value is as it opened (the desktop's `_offer_dims` tool_key):
   * when it is a tool key, the box closes with no dimension and the key then does what it does in the sketch. True when it took the key.
   */
  readonly toolKey = (e: Parameters<typeof chordOf>[0]): boolean => {
    if (this.state.pad?.mode !== 'new' || !isSketchToolChord(chordOf(e))) return false
    this.padCancel()
    return runKeyChord(e)
  }

  /** The pad's Enter. */
  padCommit = (text: string): void => {
    const pad = this.state.pad
    if (pad === null || pad.pending) return
    const typed = text.trim()
    if (pad.mode === 'set') {
      if (typed === pad.initial) return this.padCancel()
      const msg = { type: 'sketch.dimension.set', constraint: this.target ?? '', value: valueOf(typed) }
      this.sent = { type: msg.type, constraint: msg.constraint }
      this.send(msg, typed)
      return
    }
    const p = this.placing
    if (p === null) return
    // left as measured, the value is not sent: the server keeps the exact measure, not the rounded text
    const msg = {
      type: 'sketch.dimension',
      picks: p.picks,
      ...(p.pos ? { pos: [p.pos[0], p.pos[1]] } : {}),
      ...(p.kind ? { kind: p.kind } : {}),
      ...(typed !== pad.initial ? { value: valueOf(typed) } : {}),
    }
    this.sent = { type: msg.type }
    this.send(msg, typed)
  }

  private send(msg: Record<string, unknown>, typed: string): void {
    const pad = this.state.pad!
    this.set({ pad: { ...pad, pending: true, error: null, value: typed } })
    if (!this.deps.transport.send(msg)) this.refused('The connection is down.')
  }

  /** The pad's Cancel (or the dialog's close): nothing is sent, nothing is changed. */
  padCancel = (): void => {
    if (this.state.pad === null) return
    this.closePad()
    this.clearPicks()
    this.set({ hint: this.state.active ? this.hintFor([]) : null })
  }

  /** The dialog area was closed under the pad. */
  padGone(): void {
    // the inline box is not in the dialog area: the dialog area being shut says nothing about it
    if (this.state.pad === null || this.state.pad.inline) return
    this.sent = null
    this.placing = null
    this.target = null
    this.clearPicks()
    this.set({ pad: null, hint: this.state.active ? this.hintFor([]) : null })
  }

  private closePad(): void {
    const pad = this.state.pad
    if (pad === null) return
    this.sent = null
    this.placing = null
    this.target = null
    this.set({ pad: null })
    if (!pad.inline) this.deps.closePad()
  }

  private refused(message: string): void {
    const pad = this.state.pad
    if (pad === null) return
    this.sent = null
    // the pad shows the words; the chip over the view need not say them again
    this.deps.sketch.clearError()
    this.set({ pad: { ...pad, pending: false, error: message, seq: pad.seq + 1 } })
  }

  private onMessage(m: Record<string, unknown>): void {
    const sent = this.sent
    if (sent === null || this.state.pad === null) return
    if (m['type'] === 'error') {
      const f = m['for']
      if (f === sent.type) this.refused(typeof m['message'] === 'string' ? m['message'] : 'The sketch could not take that.')
      return
    }
    if (m['type'] !== 'sketch.changed') return
    const made = m['made']
    const c = typeof made === 'object' && made !== null ? (made as Record<string, unknown>)['constraint'] : undefined
    if (typeof c !== 'string' || (sent.constraint !== undefined && c !== sent.constraint)) return
    this.closePad()
    this.clearPicks()
    this.set({ hint: this.state.active ? this.hintFor([]) : null })
  }
}
