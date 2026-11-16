import type { SketchHit } from '../pick'
import type { SketchPointerTool, SketchStore, SketchTransport } from '../store'
import type { Uv } from '../wire'

export type CopyId = 'sk_mirror'

/** The copy tools the client has (I14.2 Mirror; I14.3 adds the two patterns here). The sketch's tool on is one of these ids. */
export const COPY_TOOLS: ReadonlySet<string> = new Set<CopyId>(['sk_mirror'])

const isCopy = (t: string): t is CopyId => COPY_TOOLS.has(t)

export interface CopyState {
  /** The tool on, when it is one of the copy tools. */
  tool: CopyId | null
  /** What the tool wants next, in words, or the server's last refusal; null when no copy tool is on. */
  hint: string | null
}

const OFF: CopyState = { tool: null, hint: null }

/** The server message of each tool. */
const MESSAGE: Record<CopyId, string> = { sk_mirror: 'sketch.mirror' }

/** The desktop's words (freeshape/ui/sketcher.py): before something is selected, and after. */
const HINTS: Record<CopyId, { pick: string; line: string }> = {
  sk_mirror: { pick: 'Mirror: tap the geometry to mirror, then the mirror line.', line: 'Mirror: tap the line to mirror about.' },
}

export interface CopyDeps {
  sketch: SketchStore
  transport: SketchTransport
}

/**
 * Mirror (I14.2), the sketch's tools that copy what is selected. The flow is the desktop's (Sketcher._ops_click): the tool
 * keeps the selection it is chosen with; a Commit on an entity adds it to the selection or takes it out; a Commit on a line
 * that is not selected, with something selected, is the mirror line and sends sketch.mirror {ids, line}. The answer clears
 * the selection and returns the tool to Select; a refusal stays in the hint with the tool and the selection as they were.
 * Aim on a line that would be the mirror line asks the server for a preview (`preview: true`, answered by sketch.previewed)
 * and shows the copies as a ghost. Points and dimensions in the selection are never sent. Construction mode is not sent:
 * each copy keeps its source's flag, as on the desktop. The pattern tools (I14.3) share the picks, the preview and the send.
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
  /** The line the ghost is for (or being asked for), and the id of that request: an answer with another id is dropped. */
  private aimedLine: string | null = null
  private previewId: string | null = null
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
    const had = this.aimedLine !== null
    this.aimedLine = null
    this.previewId = null
    if (had) this.deps.sketch.setPreview(null)
  }

  /** The sketch's tool changed, was tapped again, the selection moved, or the sketch closed. The selection is the store's to clear, not ours. */
  private onSketch(): void {
    const s = this.deps.sketch.getState()
    const tool = s.open && isCopy(s.tool) ? s.tool : null
    const changed = tool !== this.state.tool || (tool !== null && s.toolSeq !== this.lastSeq)
    this.lastSeq = s.toolSeq
    const ids = this.selectedIds().join(',')
    // the sketch changed under the ghost (an undo, a drag): the ghost is no longer true
    const moved = ids !== this.lastIds || s.view !== this.lastView
    this.lastIds = ids
    this.lastView = s.view
    if (changed) {
      this.clearGhost()
      this.pendingId = null
      this.set({ tool, hint: tool ? this.baseHint(tool) : null })
    } else if (moved && tool !== null) {
      this.clearGhost()
      this.set({ hint: this.baseHint(tool) })
    }
  }

  /** What a Commit on `hit` would make the mirror line: an entity that is a line, is not selected, with something selected. */
  private lineAt(hit: SketchHit | null): string | null {
    const view = this.deps.sketch.getState().view
    if (hit?.kind !== 'entity' || !view || view.sketch.entities[hit.id]?.type !== 'line') return null
    const ids = this.selectedIds()
    return ids.length > 0 && !ids.includes(hit.id) ? hit.id : null
  }

  /** Aim: show what a Commit here would do, the copies as a ghost. One request per aimed line. */
  aim(hit: SketchHit | null): void {
    const tool = this.state.tool
    if (tool === null || this.pendingId !== null) return
    const line = this.lineAt(hit)
    if (line === null) return this.clearGhost()
    if (line === this.aimedLine) return
    this.deps.sketch.setPreview(null)
    this.aimedLine = line
    this.previewId = `copy-preview-${++this.requests}`
    if (!this.deps.transport.send({ type: MESSAGE[tool], id: this.previewId, ids: this.selectedIds(), line, preview: true })) this.clearGhost()
  }

  /** The pointer's Commit: true when a copy tool is on and took it. */
  commit(hit: SketchHit | null): boolean {
    const tool = this.state.tool
    if (tool === null) return false
    if (this.pendingId !== null) return true
    this.clearGhost()
    if (hit?.kind !== 'entity') return true
    const line = this.lineAt(hit)
    if (line !== null) {
      this.pendingId = `copy-${++this.requests}`
      if (!this.deps.transport.send({ type: MESSAGE[tool], id: this.pendingId, ids: this.selectedIds(), line })) {
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

  private onMessage(m: Record<string, unknown>): void {
    const id = m['id']
    if (typeof id !== 'string') return
    if (id === this.previewId) {
      if (m['type'] === 'sketch.previewed') {
        this.deps.sketch.setPreview({ kind: 'copies', outlines: outlinesOf(m['outlines']) })
      } else if (m['type'] === 'error') {
        // a preview that is refused is no refusal of the user's: the ghost is simply not drawn
        this.deps.sketch.clearError()
        this.clearGhost()
      }
      return
    }
    if (id !== this.pendingId) return
    this.pendingId = null
    if (this.state.tool === null) return
    if (m['type'] === 'error') {
      this.deps.sketch.clearError()
      this.set({ hint: typeof m['message'] === 'string' ? m['message'] : 'The sketch could not copy that.' })
    } else if (m['type'] === 'sketch.changed') {
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
