import type { PickPointer } from '../../viewport/pick'
import { formatValue } from '../draw'
import type { SketchHit } from '../pick'
import { SKETCH_TOLERANCE } from '../pick'
import type { SketchPointerTool, SketchStore, SketchTransport } from '../store'
import type { FrameEdge, Uv } from '../wire'
import { padText, valueOf } from '../dimension'
import { cornerOf, trimPiece } from './trim'

/** The pad session of the corner Fillet: the radius being taken. */
export interface FilletPadState {
  /** The field's label. */
  label: string
  /** What the field holds when it opens (after a refusal: what was tried). */
  value: string
  /** What it opened with; Enter on it unedited sends the number. */
  initial: string
  pending: boolean
  /** The server's refusal of the last Enter, in words. */
  error: string | null
  /** Bumps on a refusal so the pad field starts again on the text that was tried. */
  seq: number
}

export type EditId = 'trim' | 'corner_fillet' | 'use_edge'

export interface EditState {
  /** The tool on, when it is one of Trim, Fillet and Use edge. */
  tool: EditId | null
  pad: FilletPadState | null
  /** What the tool wants next, in words; null when none of the three is on or the pad is open. */
  hint: string | null
}

const OFF: EditState = { tool: null, pad: null, hint: null }

const HINTS = {
  trim: 'Trim: tap the piece of a line to remove. A circle or an arc goes whole.',
  corner_fillet: 'Corner fillet: tap a corner where two lines meet, then give the radius.',
  use_edge: 'Use edge: tap a model edge to bring it into the sketch.',
} as const

const isEdit = (t: string): t is EditId => t === 'trim' || t === 'corner_fillet' || t === 'use_edge'

/** Distance from `uv` to the polyline of an edge, in mm. */
function edgeDistance(edge: FrameEdge, uv: Uv): number {
  const pts = edge.points
  let best = Infinity
  if (pts.length === 1) return Math.hypot(pts[0]![0] - uv[0], pts[0]![1] - uv[1])
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]!
    const b = pts[i]!
    const dx = b[0] - a[0]
    const dy = b[1] - a[1]
    const l2 = dx * dx + dy * dy
    const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((uv[0] - a[0]) * dx + (uv[1] - a[1]) * dy) / l2))
    best = Math.min(best, Math.hypot(uv[0] - (a[0] + t * dx), uv[1] - (a[1] + t * dy)))
  }
  return best
}

/** The model edge nearest to `uv` within the reach of the pointer, or null. `mmPerPx` turns the screen reach into mm. */
export function edgeAt(edges: readonly FrameEdge[], uv: Uv, pointer: PickPointer, mmPerPx: number): FrameEdge | null {
  const reach = SKETCH_TOLERANCE[pointer].entity * mmPerPx
  let best: FrameEdge | null = null
  let bestD = reach
  for (const e of edges) {
    const d = edgeDistance(e, uv)
    if (d <= bestD) {
      bestD = d
      best = e
    }
  }
  return best
}

export interface EditDeps {
  sketch: SketchStore
  transport: SketchTransport
  /** Open the radius pad in the dialog area (it renders <FilletPad>); and close it. */
  openPad: (tool: EditTools) => void
  closePad: () => void
}

/**
 * Trim, corner Fillet and Use edge (I7.12), the sketch tools that change geometry that is already there. Aim shows what a
 * Commit would do: Trim marks the piece of the line it would remove, Use edge the model edge, seen through the see-through
 * solid. Commit sends it: sketch.trim {entity, at}, sketch.use_edge {pick}; a Commit on a corner point opens the radius on
 * the pad and Enter sends sketch.fillet {point, radius}. The tool is on while it is the sketch's tool. A refused radius
 * stays on the pad in the server's words.
 */
export class EditTools implements SketchPointerTool {
  private state: EditState = OFF
  private readonly listeners = new Set<() => void>()
  private off: (() => void)[] = []
  private corner: string | null = null
  private awaiting = false
  private lastSeq = -1

  constructor(private readonly deps: EditDeps) {}

  attach(): void {
    if (this.off.length > 0) return
    this.off = [this.deps.sketch.addPointerTool(this), this.deps.transport.onSketch((m) => this.onMessage(m)), this.deps.sketch.subscribe(() => this.onSketch())]
    this.onSketch()
  }
  detach(): void {
    for (const f of this.off) f()
    this.off = []
  }

  readonly subscribe = (l: () => void): (() => void) => {
    this.listeners.add(l)
    return () => this.listeners.delete(l)
  }
  readonly getState = (): EditState => this.state

  private set(patch: Partial<EditState>): void {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach((l) => l())
  }

  /** The sketch's tool changed, was tapped again, or the sketch closed: a pad open for a corner is given up. */
  private onSketch(): void {
    const s = this.deps.sketch.getState()
    const tool = s.open && isEdit(s.tool) ? s.tool : null
    // another tool's change is no business of ours (a Constrain tool taps Select again and keeps its selection)
    const changed = tool !== this.state.tool || (tool !== null && s.toolSeq !== this.lastSeq)
    this.lastSeq = s.toolSeq
    if (!changed) return
    if (this.state.pad !== null) this.closePad()
    this.corner = null
    this.deps.sketch.clearSelection()
    this.deps.sketch.setPreview(null)
    this.set({ tool, hint: tool ? HINTS[tool] : null })
  }

  private get active(): EditId | null {
    return this.state.tool
  }

  /** Aim: show what a Commit here would do. */
  aim(hit: SketchHit | null, uv: Uv | null, pointer: PickPointer, mmPerPx = 1): void {
    const tool = this.active
    const view = this.deps.sketch.getState().view
    if (tool === null || !view) return
    if (tool === 'trim' && hit?.kind === 'entity' && uv) {
      const piece = trimPiece(view.sketch, hit.id, uv)
      if (piece && !piece.whole) return this.deps.sketch.setPreview({ kind: 'trim', from: piece.from, to: piece.to })
    } else if (tool === 'use_edge' && uv) {
      const edge = edgeAt(view.edges, uv, pointer, mmPerPx)
      if (edge) return this.deps.sketch.setPreview({ kind: 'edge', points: edge.points })
    }
    this.deps.sketch.setPreview(null)
  }

  /** The pointer's Commit: true when a tool of this class is on and took it. */
  commit(hit: SketchHit | null, uv: Uv | null, pointer: PickPointer, mmPerPx = 1): boolean {
    const tool = this.active
    if (tool === null) return false
    const view = this.deps.sketch.getState().view
    if (!view || this.state.pad !== null) return true
    this.deps.sketch.setPreview(null)
    if (tool === 'trim') {
      if (hit?.kind !== 'entity' || !uv) return this.say('Tap on the line, circle or arc itself to trim it.')
      this.deps.transport.send({ type: 'sketch.trim', entity: hit.id, at: [uv[0], uv[1]] })
      return true
    }
    if (tool === 'use_edge') {
      const edge = uv ? edgeAt(view.edges, uv, pointer, mmPerPx) : null
      if (!edge) return this.say('No model edge there. Tap on one of the model\'s edges.')
      this.deps.transport.send({ type: 'sketch.use_edge', pick: edge.pick })
      return true
    }
    const shorter = hit?.kind === 'point' ? cornerOf(view.sketch, hit.id) : null
    if (hit === null || shorter === null) return this.say('That is not a corner. Tap a point where exactly two lines meet.')
    this.corner = hit.id
    // opens on a small radius that fits the lines: a quarter of the shorter one, at most 5 mm
    const text = padText('radius', Number(formatValue(Math.min(5, shorter / 4))))
    this.deps.sketch.setSelection([hit])
    this.set({ pad: { label: 'Radius', value: text, initial: text, pending: false, error: null, seq: 0 }, hint: null })
    this.deps.openPad(this)
    return true
  }

  doubleTap(): boolean {
    return false
  }

  private say(hint: string): true {
    this.set({ hint })
    return true
  }

  /** The pad's Enter. */
  padCommit = (text: string): void => {
    const pad = this.state.pad
    if (pad === null || pad.pending || this.corner === null) return
    const typed = text.trim()
    this.awaiting = true
    this.set({ pad: { ...pad, pending: true, error: null, value: typed } })
    // opened and left as is, the radius goes as the number shown
    const radius = typed === pad.initial ? Number(formatValue(parseFloat(pad.initial))) : valueOf(typed)
    if (!this.deps.transport.send({ type: 'sketch.fillet', point: this.corner, radius })) this.refused('The connection is down.')
  }

  /** The pad's Cancel: nothing is sent. */
  padCancel = (): void => {
    if (this.state.pad === null) return
    this.closePad()
    this.deps.sketch.clearSelection()
    this.set({ hint: this.active ? HINTS[this.active] : null })
  }

  /** The dialog area was closed under the pad. */
  padGone(): void {
    if (this.state.pad === null) return
    this.awaiting = false
    this.corner = null
    this.deps.sketch.clearSelection()
    this.set({ pad: null, hint: this.active ? HINTS[this.active] : null })
  }

  private closePad(): void {
    if (this.state.pad === null) return
    this.awaiting = false
    this.corner = null
    this.set({ pad: null })
    this.deps.closePad()
  }

  private refused(message: string): void {
    const pad = this.state.pad
    if (pad === null) return
    this.awaiting = false
    // the pad shows the words; the chip over the view need not say them again
    this.deps.sketch.clearError()
    this.set({ pad: { ...pad, pending: false, error: message, seq: pad.seq + 1 } })
  }

  private onMessage(m: Record<string, unknown>): void {
    if (!this.awaiting || this.state.pad === null) return
    if (m['type'] === 'error') {
      if (m['for'] === 'sketch.fillet') this.refused(typeof m['message'] === 'string' ? m['message'] : 'The sketch could not round that corner.')
      return
    }
    if (m['type'] !== 'sketch.changed') return
    const made = m['made']
    if (typeof made !== 'object' || made === null || typeof (made as Record<string, unknown>)['arc'] !== 'string') return
    this.closePad()
    this.deps.sketch.clearSelection()
    this.set({ hint: this.active ? HINTS[this.active] : null })
  }
}
