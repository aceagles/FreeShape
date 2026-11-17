import type { PickPointer } from '../../viewport/pick'
import { checkExpression, evaluateNumber, formatNumber } from '../../pad/editor'
import type { SketchHit } from '../pick'
import type { SketchPointerTool, SketchStore, SketchTransport } from '../store'
import type { Uv } from '../wire'

export type CopyId = 'sk_mirror' | 'sk_lpattern' | 'sk_cpattern'
export type PatternId = 'sk_lpattern' | 'sk_cpattern'

/** The copy tools the client has (I14.2 Mirror, I14.3 the two patterns). The sketch's tool on is one of these ids. */
export const COPY_TOOLS: ReadonlySet<string> = new Set<CopyId>(['sk_mirror', 'sk_lpattern', 'sk_cpattern'])

const isCopy = (t: string): t is CopyId => COPY_TOOLS.has(t)

/** One number row of the pattern panel: the text as typed (or as the server gave it), and why it cannot be sent, if it cannot. */
export interface PatternRow {
  text: string
  error: string | null
}

/** The two rows: the number of instances, and the spacing (linear) or the total angle (circular). */
export type PatternRowKey = 'count' | 'second'

/** The pattern's numbers, in one small panel in the dialog area (I14.3). */
export interface PatternPanel {
  tool: PatternId
  count: PatternRow
  /** Spacing in mm (linear, empty until the first preview's made.spacing fills it) or Total angle in degrees (circular). */
  second: PatternRow
  /** The server's refusal of the last preview or of Accept, in its words; shown under the rows. */
  refusal: string | null
  /** Accept is sent and not yet answered. */
  pending: boolean
  /** Bumps each time the panel opens, so the panel's own state (the row on the pad) starts again. */
  seq: number
}

export interface CopyState {
  /** The tool on, when it is one of the copy tools. */
  tool: CopyId | null
  /** What the tool wants next, in words, or the server's last refusal; null when no copy tool is on or the panel is open. */
  hint: string | null
  /** The pattern panel, while it is open. */
  panel: PatternPanel | null
}

const OFF: CopyState = { tool: null, hint: null, panel: null }

/** The server message of each tool. */
const MESSAGE: Record<CopyId, string> = { sk_mirror: 'sketch.mirror', sk_lpattern: 'sketch.linear_pattern', sk_cpattern: 'sketch.circular_pattern' }

/** The desktop's words (freeshape/ui/sketcher.py): before something is selected, and after. */
const HINTS: Record<CopyId, { pick: string; line: string }> = {
  sk_mirror: { pick: 'Mirror: tap the geometry to mirror, then the mirror line.', line: 'Mirror: tap the line to mirror about.' },
  sk_lpattern: { pick: 'Linear pattern: tap the geometry, then a line for the direction.', line: 'Linear pattern: tap a line for the direction.' },
  sk_cpattern: { pick: 'Circular pattern: tap the geometry, then the centre point.', line: 'Circular pattern: tap the centre point.' },
}

/** The panel's title and its second row, per pattern. */
export const PATTERN_PANEL: Record<PatternId, { title: string; second: string; unit: string; count: string; opening: string }> = {
  sk_lpattern: { title: 'Linear pattern', second: 'Spacing', unit: 'mm', count: '3', opening: '' },
  sk_cpattern: { title: 'Circular pattern', second: 'Total angle', unit: 'deg', count: '6', opening: '360' },
}

/**
 * The linear pattern's least opening spacing, the desktop's `Sketcher._nice(vp, 40)`: `px` screen pixels in mm, rounded up
 * to 1, 2, 5 or 10 times a power of ten.
 */
export function patternFloor(mmPerPx: number, px = 40): number {
  const raw = mmPerPx * px
  const mag = 10 ** Math.floor(Math.log10(Math.max(raw, 1e-9)))
  for (const m of [1, 2, 5, 10]) if (m * mag >= raw) return m * mag
  return 10 * mag
}

/** A row's text as the server takes it: the number it stands for, or the text itself when it names a variable (the server evaluates it). */
function wireValue(text: string): number | string {
  const t = text.trim()
  const r = evaluateNumber(t)
  return r.ok ? r.value : t
}

/** What the pattern was picked with: the entities, and the line and side (linear) or the centre (circular). */
type PatternPick = { tool: 'sk_lpattern'; ids: string[]; line: string; at: Uv; floor: number } | { tool: 'sk_cpattern'; ids: string[]; centre: Uv | { at: Uv; point: string } }

export interface CopyDeps {
  sketch: SketchStore
  transport: SketchTransport
  /** Open the pattern panel in the dialog area (it renders <CopyPanel>); and close it. */
  openPanel?: (tool: CopyTools) => void
  closePanel?: () => void
  /** The variable names in scope (without "#"), to judge a row's text. */
  variables?: () => readonly string[]
}

/**
 * Mirror (I14.2) and the linear and circular patterns (I14.3), the sketch's tools that copy what is selected. The flow is the
 * desktop's (Sketcher._ops_click): the tool keeps the selection it is chosen with; a Commit on an entity adds it to the
 * selection or takes it out. Mirror: a Commit on a line that is not selected, with something selected, is the mirror line
 * and sends sketch.mirror {ids, line}; Aim on such a line asks for a preview and shows the copies as a ghost.
 * Linear pattern: a Commit on such a line takes it as the direction and the tapped point as the side (`at`). Circular
 * pattern: with something selected, a Commit on a point takes it as the centre ({at, point}); on empty space or on an
 * entity that is not selected the tapped point ([u, v], unsnapped); on a selected entity it only takes it out. A pattern
 * then opens its panel: Instances and Spacing (or Total angle); every valid change asks for a preview (one in flight,
 * the latest waiting), Accept sends the message, Cancel sends nothing and goes back to the picks with the selection kept.
 * The answer clears the selection and returns the tool to Select; a refusal stays in the hint (Mirror) or under the rows
 * (a pattern) with the selection kept. Points and dimensions in the selection are never sent. Construction mode is not
 * sent: each copy keeps its source's flag, as on the desktop.
 */
export class CopyTools implements SketchPointerTool {
  private state: CopyState = OFF
  private readonly listeners = new Set<() => void>()
  private off: (() => void)[] = []
  private lastSeq = -1
  /** The selection the ghost and the hint were made for: a change of it drops the ghost. */
  private lastIds = ''
  private lastView: unknown = null
  private requests = 0
  private panels = 0
  /** The line the ghost is for (or being asked for), and the id of that request: an answer with another id is dropped. */
  private aimedLine: string | null = null
  private previewId: string | null = null
  /** The pattern's ghost is drawn. */
  private ghost = false
  /** The pattern's picks while its panel is open. */
  private pick: PatternPick | null = null
  /** A pattern preview's change came while one was in flight: it is sent when that one is answered. */
  private waiting = false
  /** The id of the commit sent and not yet answered. */
  private pendingId: string | null = null

  constructor(private readonly deps: CopyDeps) {}

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
  readonly getState = (): CopyState => this.state

  private set(patch: Partial<CopyState>): void {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach((l) => l())
  }

  /** The entities of the selection that exist in the sketch, in selection order: points, dimensions and constraints are left out. */
  private selectedIds(): string[] {
    const s = this.deps.sketch.getState()
    const entities = s.view?.sketch.entities ?? {}
    return s.selected.filter((h) => h.kind === 'entity' && h.id in entities).map((h) => h.id)
  }

  private baseHint(tool: CopyId): string {
    return this.selectedIds().length > 0 ? HINTS[tool].line : HINTS[tool].pick
  }

  /** The ghost is dropped, and an answer still on its way is no longer wanted. */
  private clearGhost(): void {
    const had = this.aimedLine !== null || this.ghost
    this.aimedLine = null
    this.previewId = null
    this.ghost = false
    this.waiting = false
    if (had) this.deps.sketch.setPreview(null)
  }

  /** The panel is put away (nothing sent); the ghost goes with it. */
  private dropPanel(): void {
    if (this.state.panel === null) return
    this.pick = null
    this.clearGhost()
    this.set({ panel: null })
    this.deps.closePanel?.()
  }

  /** The sketch's tool changed, was tapped again, the selection moved, or the sketch closed. The selection is the store's to clear, not ours. */
  private onSketch(): void {
    const s = this.deps.sketch.getState()
    const tool = s.open && isCopy(s.tool) ? s.tool : null
    const changed = tool !== this.state.tool || (tool !== null && s.toolSeq !== this.lastSeq)
    this.lastSeq = s.toolSeq
    const ids = this.selectedIds().join(',')
    // the sketch changed under the ghost (an undo, a drag): the ghost is no longer true
    const viewMoved = s.view !== this.lastView
    const moved = ids !== this.lastIds || viewMoved
    this.lastIds = ids
    this.lastView = s.view
    if (changed) {
      this.dropPanel()
      this.clearGhost()
      this.pendingId = null
      this.set({ tool, hint: tool ? this.baseHint(tool) : null })
    } else if (this.state.panel !== null) {
      // the panel holds its own picks; a sketch that changed under it is previewed again
      if (viewMoved && !this.state.panel.pending) this.askPreview()
    } else if (moved && tool !== null) {
      this.clearGhost()
      this.set({ hint: this.baseHint(tool) })
    }
  }

  /** What a Commit on `hit` would make the mirror or direction line: an entity that is a line, is not selected, with something selected. */
  private lineAt(hit: SketchHit | null): string | null {
    const view = this.deps.sketch.getState().view
    if (hit?.kind !== 'entity' || !view || view.sketch.entities[hit.id]?.type !== 'line') return null
    const ids = this.selectedIds()
    return ids.length > 0 && !ids.includes(hit.id) ? hit.id : null
  }

  /** Aim (Mirror): show what a Commit here would do, the copies as a ghost. One request per aimed line. */
  aim(hit: SketchHit | null): void {
    const tool = this.state.tool
    if (tool !== 'sk_mirror' || this.pendingId !== null) return
    const line = this.lineAt(hit)
    if (line === null) return this.clearGhost()
    if (line === this.aimedLine) return
    this.deps.sketch.setPreview(null)
    this.aimedLine = line
    this.previewId = `copy-preview-${++this.requests}`
    if (!this.deps.transport.send({ type: MESSAGE[tool], id: this.previewId, ids: this.selectedIds(), line, preview: true })) this.clearGhost()
  }

  /** The pointer's Commit: true when a copy tool is on and took it. */
  commit(hit: SketchHit | null, uv?: Uv | null, _pointer?: PickPointer, mmPerPx?: number): boolean {
    const tool = this.state.tool
    if (tool === null) return false
    // the panel has the numbers: the view takes no picks until it is closed
    if (this.pendingId !== null || this.state.panel !== null) return true
    this.clearGhost()
    const ids = this.selectedIds()
    if (tool === 'sk_cpattern' && ids.length > 0 && uv) {
      // the desktop's rule: a point is the centre; empty space or an entity not selected gives the tapped point
      if (hit?.kind === 'point') return this.openPanel({ tool, ids, centre: { at: uv, point: hit.id } })
      if (hit?.kind !== 'entity' || !ids.includes(hit.id)) return this.openPanel({ tool, ids, centre: [uv[0], uv[1]] })
    }
    if (hit?.kind !== 'entity') return true
    const line = tool === 'sk_cpattern' ? null : this.lineAt(hit)
    if (line !== null && tool === 'sk_lpattern') {
      if (uv) this.openPanel({ tool, ids, line, at: [uv[0], uv[1]], floor: patternFloor(mmPerPx ?? 0) })
      return true
    }
    if (line !== null) {
      this.pendingId = `copy-${++this.requests}`
      if (!this.deps.transport.send({ type: MESSAGE[tool], id: this.pendingId, ids, line })) {
        this.pendingId = null
        this.set({ hint: 'The connection is down.' })
      }
      return true
    }
    // any other entity joins the selection or leaves it; selected points and dimensions stay as they are
    const now = this.deps.sketch.getState().selected
    this.deps.sketch.setSelection(now.some((h) => h.kind === 'entity' && h.id === hit.id) ? now.filter((h) => !(h.kind === 'entity' && h.id === hit.id)) : [...now, hit])
    return true
  }

  doubleTap(): boolean {
    return false
  }

  /** Esc with no field in focus (Desktop mode): an open panel is cancelled; true when there was one. */
  escape(): boolean {
    if (this.state.panel === null) return false
    if (!this.state.panel.pending) this.cancel()
    return true
  }

  // ------------------------------------------------------------------ the pattern panel (I14.3)

  private openPanel(pick: PatternPick): true {
    this.pick = pick
    const look = PATTERN_PANEL[pick.tool]
    this.set({
      hint: null,
      panel: { tool: pick.tool, count: { text: look.count, error: null }, second: { text: look.opening, error: null }, refusal: null, pending: false, seq: ++this.panels },
    })
    this.deps.openPanel?.(this)
    this.askPreview()
    return true
  }

  /** Why a row's text cannot be sent, or null. Instances is a whole number, at least 2, when it names no variable. */
  private check(key: PatternRowKey, text: string): string | null {
    const t = text.trim()
    if (t === '') return 'Enter a value.'
    const form = checkExpression(t, this.deps.variables?.() ?? [])
    if (!form.ok) return form.error
    if (key === 'count') {
      const r = evaluateNumber(t)
      if (r.ok && (!Number.isInteger(r.value) || r.value < 2)) return 'A whole number, 2 or more.'
    }
    return null
  }

  /** A row's text changed (each key on the pad, a typed field's Enter, Tab or leaving it): marked, and previewed when both rows can be sent. */
  readonly edit = (key: PatternRowKey, text: string): void => {
    const p = this.state.panel
    if (p === null || p.pending) return
    if (p[key].text === text && p[key].error === null) return
    const row: PatternRow = { text, error: this.check(key, text) }
    this.set({ panel: { ...p, [key]: row } })
    if (row.error === null) this.askPreview()
  }

  /** Both rows can be sent: neither is marked, and the linear spacing may still be the server's to give. */
  private sendable(p: PatternPanel): boolean {
    return p.count.error === null && p.second.error === null
  }

  /** Accept is on: both rows hold a value the server can take, and nothing is in flight. */
  canAccept(): boolean {
    const p = this.state.panel
    return p !== null && !p.pending && this.sendable(p) && p.second.text.trim() !== ''
  }

  /** The message of the pattern with the panel's numbers; the spacing left out (and the floor sent) until the server has given one. */
  private body(p: PatternPanel, pick: PatternPick): Record<string, unknown> {
    const count = wireValue(p.count.text)
    if (pick.tool === 'sk_lpattern') {
      const spacing = p.second.text.trim() === '' ? { floor: pick.floor } : { spacing: wireValue(p.second.text) }
      return { type: MESSAGE[pick.tool], ids: pick.ids, line: pick.line, at: pick.at, count, ...spacing }
    }
    return { type: MESSAGE[pick.tool], ids: pick.ids, centre: pick.centre, count, angle: wireValue(p.second.text) }
  }

  /** One preview in flight; a change while it is out waits, and only the latest is sent when the answer comes. */
  private askPreview(): void {
    const p = this.state.panel
    if (p === null || this.pick === null || !this.sendable(p)) return
    if (this.previewId !== null) {
      this.waiting = true
      return
    }
    this.waiting = false
    this.previewId = `copy-preview-${++this.requests}`
    if (!this.deps.transport.send({ ...this.body(p, this.pick), id: this.previewId, preview: true })) {
      this.previewId = null
      this.set({ panel: { ...p, refusal: 'The connection is down.' } })
    }
  }

  /** The panel's Accept (its button, or Enter in the last row in Desktop mode). False when it cannot be sent now. */
  readonly accept = (): boolean => {
    const p = this.state.panel
    if (p === null || this.pick === null || !this.canAccept()) return false
    this.pendingId = `copy-${++this.requests}`
    this.set({ panel: { ...p, pending: true, refusal: null } })
    if (!this.deps.transport.send({ ...this.body(p, this.pick), id: this.pendingId })) {
      this.pendingId = null
      this.set({ panel: { ...p, pending: false, refusal: 'The connection is down.' } })
      return false
    }
    return true
  }

  /** The panel's Cancel, Esc, or the panel closed: nothing is sent, and the tool is back at its picks with the selection kept. */
  readonly cancel = (): void => {
    const p = this.state.panel
    if (p === null || p.pending) return
    this.dropPanel()
    const tool = this.state.tool
    this.set({ hint: tool ? this.baseHint(tool) : null })
  }

  /** The dialog area was closed under the panel (by the shell). */
  panelGone(): void {
    const p = this.state.panel
    if (p === null) return
    this.pick = null
    this.clearGhost()
    const tool = this.state.tool
    this.set({ panel: null, hint: tool && !p.pending ? this.baseHint(tool) : null })
  }

  private onMessage(m: Record<string, unknown>): void {
    const id = m['id']
    if (typeof id !== 'string') return
    if (id === this.previewId) {
      const p = this.state.panel
      if (p === null) {
        // Mirror's Aim
        if (m['type'] === 'sketch.previewed') {
          this.deps.sketch.setPreview({ kind: 'copies', outlines: outlinesOf(m['outlines']) })
        } else if (m['type'] === 'error') {
          // a preview that is refused is no refusal of the user's: the ghost is simply not drawn
          this.deps.sketch.clearError()
          this.clearGhost()
        }
        return
      }
      this.previewId = null
      if (m['type'] === 'sketch.previewed') {
        this.ghost = true
        this.deps.sketch.setPreview({ kind: 'copies', outlines: outlinesOf(m['outlines']) })
        const made = m['made'] as Record<string, unknown> | undefined
        const spacing = made?.['spacing']
        // the server's opening spacing fills the row the first time
        const second = p.tool === 'sk_lpattern' && p.second.text.trim() === '' && typeof spacing === 'number' ? { text: formatNumber(spacing), error: null } : p.second
        this.set({ panel: { ...p, second, refusal: null } })
      } else if (m['type'] === 'error') {
        // the desktop shows the reason and puts the sketch back: the words under the rows, no ghost, no chip
        this.deps.sketch.clearError()
        if (this.ghost) this.deps.sketch.setPreview(null)
        this.ghost = false
        this.set({ panel: { ...p, refusal: typeof m['message'] === 'string' ? m['message'] : 'The sketch could not make that pattern.' } })
      }
      if (this.waiting) this.askPreview()
      return
    }
    if (id !== this.pendingId) return
    this.pendingId = null
    if (this.state.tool === null) return
    const p = this.state.panel
    if (m['type'] === 'error') {
      this.deps.sketch.clearError()
      const words = typeof m['message'] === 'string' ? m['message'] : 'The sketch could not copy that.'
      if (p !== null) {
        if (this.ghost) this.deps.sketch.setPreview(null)
        this.ghost = false
        this.set({ panel: { ...p, pending: false, refusal: words } })
      } else this.set({ hint: words })
    } else if (m['type'] === 'sketch.changed') {
      this.dropPanel()
      this.deps.sketch.clearSelection()
      this.deps.sketch.chooseTool('select')
    }
  }
}

/** The polylines of a `sketch.previewed` answer: the ones made of well-formed points. */
function outlinesOf(v: unknown): Uv[][] {
  if (!Array.isArray(v)) return []
  const out: Uv[][] = []
  for (const line of v) {
    if (!Array.isArray(line)) continue
    const pts = line.filter((p): p is Uv => Array.isArray(p) && p.length === 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]))
    if (pts.length > 1) out.push(pts)
  }
  return out
}
