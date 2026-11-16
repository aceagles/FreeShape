import { SketchDrag } from './drag'
import type { LiveDrag } from './drag'
import type { SketchHit } from './pick'
import type { PickPointer } from '../viewport/pick'
import { constructionPlan, deletionIds } from './select'
import { isDrawTool } from './tools/shapes'
import { parseSketchView } from './wire'
import type { Frame, FrameEdge, SketchView, Uv } from './wire'

/** 2D keeps the view square to the sketch plane (one finger pans, two pan, zoom and twist); 3D orbits as the Part Studio does. */
export type SketchNav = '2d' | '3d'

/** What the sketch store needs of the live connection (ConnectionStore has it). */
export interface SketchTransport {
  send(msg: Record<string, unknown>): boolean
  /** Every `sketch.*` answer and every error for a sketch message of the Document being watched. */
  onSketch(listener: (m: Record<string, unknown>) => void): () => void
  /** The socket (re)connected: a sketch that was open is asked for again. */
  onHello(listener: () => void): () => void
  /** The connection's clock in ms, to stamp a `sketch.drag.move` (ConnectionStore.stamp); the page's clock when absent. */
  stamp?(): number
}

/** What to open: an existing sketch feature, or a new sketch on a plane reference (as a sketch's `plane` param stores it). */
export type SketchTarget = { document: string; tab: string } & ({ feature: string } | { plane: Record<string, unknown> })

/** A tool that takes the pointer's Commit before the selection does (the Dimension tool, I7.11). */
export interface SketchPointerTool {
  /** A Commit on `hit` (or on nothing) at sketch point `uv`; true when the tool took it, so nothing is selected. */
  commit(hit: SketchHit | null, uv: Uv | null, pointer: PickPointer, mmPerPx?: number): boolean
  /** The pen or the mouse Aims at `hit` or at `uv` (I7.12): the tool shows what a Commit there would do (`SketchStore.setPreview`). */
  aim?(hit: SketchHit | null, uv: Uv | null, pointer: PickPointer, mmPerPx?: number): void
  /** The mouse's left button went down on `hit` (or on nothing) at `uv`, Desktop mode and Tablet mode alike; its Commit follows on release (F22). */
  press?(hit: SketchHit | null, uv: Uv | null, pointer: PickPointer, mmPerPx?: number): void
  /** A double tap or double click on `hit`; true when the tool took it (the view then does not fit). */
  doubleTap(hit: SketchHit): boolean
  /** Esc (Desktop mode, I9.7): give up what the tool has half done (an open value box, the picks made); true when there was something. */
  escape?(): boolean
}

/** What the Trim, Use edge and copy tools show for the Aim: the piece of a line a Trim would remove, the model edge a Use would bring in, or the copies a Mirror would make (each an outline). All in sketch coordinates. */
export type SketchPreview = { kind: 'trim'; from: Uv; to: Uv } | { kind: 'edge'; points: readonly Uv[] } | { kind: 'copies'; outlines: readonly (readonly Uv[])[] }

export interface SketchState {
  /** No sketch is open. */
  open: boolean
  /** An open message is on its way. */
  opening: boolean
  view: SketchView | null
  nav: SketchNav
  selected: readonly SketchHit[]
  aimed: SketchHit | null
  multi: boolean
  /** The server's last refusal, in words; cleared by the next step. */
  error: string | null
  /** Bumps when the view should turn square to the plane again (Normal to). */
  faceSeq: number
  /** Bumps on every new sketch opened, so the view fits it once. */
  openSeq: number
  /** The revision a commit made, until the next open. */
  committed: number | null
  /** The tool that is on (I7.8): a strip id such as 'select', 'line', 'rectangle'; 'center_rectangle' is the Rectangle's other form. */
  tool: string
  /** Bumps when the tool is tapped again, so a shape in progress starts over. */
  toolSeq: number
  /** A Constrain tool is collecting picks: a tap adds to the selection or takes the item off it, as in Multi mode. */
  picking: boolean
  /** How many points of a shape in progress are committed (set by the drawing tools). */
  drawing: number
  /** Items of a drag in progress as they are drawn (moved points, radii, a dimension's label); null when none is dragged (I7.9). */
  live: LiveDrag | null
  /** What the tool on shows for the pen's Aim (I7.12); null when it shows nothing. */
  preview: SketchPreview | null
  /** Construction mode (F20): what the drawing tools make now is construction geometry. Off when a sketch opens and when it closes; it stays on across tools, as the desktop's does. */
  construction: boolean
  /** A short note about a key that did nothing ("Desktop only for now"), shown over the view until the tool or the selection changes (I9.7). */
  notice: string | null
}

/** What a `sketch.add` made: the ids of the point of each pick and of the entities. */
export interface Made {
  points: string[]
  entities: string[]
}

const CLOSED: SketchState = { open: false, opening: false, view: null, nav: '2d', selected: [], aimed: null, multi: false, error: null, faceSeq: 0, openSeq: 0, committed: null, tool: 'select', toolSeq: 0, picking: false, drawing: 0, live: null, preview: null, construction: false, notice: null }

const same = (a: SketchHit | null, b: SketchHit | null): boolean => a === b || (a !== null && b !== null && a.kind === b.kind && a.id === b.id)

/**
 * Whether the Construct button shows pressed: with entities selected, when every one is construction (a press then makes
 * them real); with none, when construction mode is on. So the button always shows what a press will undo.
 */
export function constructionPressed(state: Pick<SketchState, 'open' | 'view' | 'selected' | 'construction'>): boolean {
  if (!state.open || !state.view) return false
  const plan = constructionPlan(state.view.sketch, state.selected)
  return plan ? !plan.value : state.construction
}

/** The words for what is selected: "1 point", "2 points, 1 line", "Nothing selected". */
export function selectionText(items: readonly SketchHit[]): string {
  if (items.length === 0) return 'Nothing selected'
  const names: Record<SketchHit['kind'], [string, string]> = { point: ['point', 'points'], entity: ['item', 'items'], dimension: ['dimension', 'dimensions'], constraint: ['constraint', 'constraints'] }
  const parts: string[] = []
  for (const kind of ['point', 'entity', 'dimension', 'constraint'] as const) {
    const n = items.filter((i) => i.kind === kind).length
    if (n > 0) parts.push(`${n} ${names[kind][n === 1 ? 0 : 1]}`)
  }
  return parts.join(', ')
}

/** How the solve stands, for the status chip: "3 items free", "Fully defined", "2 conflicting". */
export function statusText(view: SketchView): { text: string; tone: 'free' | 'defined' | 'conflicting' } {
  const all = Object.values(view.status.entities)
  const bad = all.filter((s) => s === 'conflicting').length + Object.values(view.status.constraints).filter((s) => s === 'conflicting').length
  if (bad > 0) return { text: `${bad} conflicting`, tone: 'conflicting' }
  const free = all.filter((s) => s === 'free').length
  if (free > 0) return { text: `${free} ${free === 1 ? 'item' : 'items'} free`, tone: 'free' }
  return { text: all.length > 0 ? 'Fully defined' : 'Empty', tone: 'defined' }
}

/**
 * The client side of a sketch session (I7.3): sends sketch.open, takes in the answers, and keeps what the view draws and
 * what is selected in it. The sketch itself lives on the server; every change comes back as a whole sketch plus its
 * solve status. One per page, for the sketch this client has open.
 */
export class SketchStore {
  private state: SketchState = CLOSED
  private readonly listeners = new Set<() => void>()
  private off: (() => void)[] = []
  private frame: { frame: Frame; edges: FrameEdge[] } | null = null
  /** The tool that takes Commits first, if one is on (set by the tool itself). */
  pointerTool: SketchPointerTool | null = null
  /** More tools that take Commits and Aims first (Trim, Fillet and Use edge, I7.12); each takes them only while it is the tool on. */
  private readonly extraTools = new Set<SketchPointerTool>()
  /** The `sketch.add` messages sent and not yet answered, oldest first. The server answers a client's messages in order. */
  private adds: ((made: Made | null) => void)[] = []
  /** Moving by dragging (I7.9). */
  readonly drag: SketchDrag

  constructor(private readonly transport: SketchTransport) {
    this.drag = new SketchDrag(transport, this)
    this.attach()
  }

  /** Add a tool to those that take the pointer first; returns the way to remove it. */
  addPointerTool(tool: SketchPointerTool): () => void {
    this.extraTools.add(tool)
    return () => void this.extraTools.delete(tool)
  }
  private get tools(): SketchPointerTool[] {
    return [...(this.pointerTool ? [this.pointerTool] : []), ...this.extraTools]
  }
  /** The pointer's Commit goes to the tools in turn; true when one took it, so nothing is selected. */
  commitTools(hit: SketchHit | null, uv: Uv | null, pointer: PickPointer, mmPerPx?: number): boolean {
    return this.tools.some((t) => t.commit(hit, uv, pointer, mmPerPx))
  }
  pressTools(hit: SketchHit | null, uv: Uv | null, pointer: PickPointer, mmPerPx?: number): void {
    for (const t of this.tools) t.press?.(hit, uv, pointer, mmPerPx)
  }
  doubleTapTools(hit: SketchHit): boolean {
    return this.tools.some((t) => t.doubleTap(hit))
  }
  aimTools(hit: SketchHit | null, uv: Uv | null, pointer: PickPointer, mmPerPx?: number): void {
    for (const t of this.tools) t.aim?.(hit, uv, pointer, mmPerPx)
  }
  setPreview(preview: SketchPreview | null): void {
    const now = this.state.preview
    if (now === preview) return
    if (now !== null && preview !== null && JSON.stringify(now) === JSON.stringify(preview)) return
    this.set({ preview })
  }

  /** Listen to the connection (done by the constructor; call it again after `detach`, as React's strict mode does). */
  attach(): void {
    if (this.off.length > 0) return
    this.drag.attach()
    this.off = [this.transport.onSketch((m) => this.onMessage(m)), this.transport.onHello(() => this.state.open && this.transport.send({ type: 'sketch.state' }))]
  }

  /** Stop listening to the connection. */
  detach(): void {
    this.drag.detach()
    for (const f of this.off) f()
    this.off = []
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  readonly getState = (): SketchState => this.state

  private set(patch: Partial<SketchState>): void {
    // a note stays until the tool or the selection moves
    const moved = this.state.notice !== null && !('notice' in patch) && ('tool' in patch || 'toolSeq' in patch || 'selected' in patch)
    this.state = { ...this.state, ...patch, ...(moved ? { notice: null } : {}) }
    this.listeners.forEach((l) => l())
  }

  /** Open an existing sketch or a new one. The answer is `sketch.opened`. */
  open(target: SketchTarget): void {
    if (this.state.open || this.state.opening) return
    this.set({ opening: true, error: null })
    if (!this.transport.send({ type: 'sketch.open', ...target })) this.set({ opening: false, error: 'The connection is down.' })
  }
  commit(): void {
    if (this.state.open) this.transport.send({ type: 'sketch.commit' })
  }
  cancel(): void {
    if (this.state.open) this.transport.send({ type: 'sketch.cancel' })
  }
  undo(): void {
    if (this.state.open && this.state.view?.canUndo) this.transport.send({ type: 'sketch.undo' })
  }
  redo(): void {
    if (this.state.open && this.state.view?.canRedo) this.transport.send({ type: 'sketch.redo' })
  }
  /** Drop the refusal on show (a tool that shows it itself, as the Dimension pad does). */
  clearError(): void {
    if (this.state.error !== null) this.set({ error: null })
  }
  /** Ask for the whole sketch again. */
  refresh(): void {
    if (this.state.open) this.transport.send({ type: 'sketch.state' })
  }

  setNav(nav: SketchNav): void {
    if (!this.state.open || nav === this.state.nav) return
    // back to 2D turns the view square to the plane again
    this.set(nav === '2d' ? { nav, faceSeq: this.state.faceSeq + 1 } : { nav })
  }
  /** Turn the view square to the plane; the mode stays. */
  normalTo(): void {
    if (this.state.open) this.set({ faceSeq: this.state.faceSeq + 1 })
  }

  aim(hit: SketchHit | null): void {
    if (!same(this.state.aimed, hit)) this.set({ aimed: hit })
  }
  /** Commit on an item (or on nothing): single mode replaces the selection, Multi mode toggles the item. */
  select(hit: SketchHit | null): void {
    const now = this.state.selected
    let next: readonly SketchHit[]
    if (hit === null) next = this.state.multi || this.state.picking ? now : []
    else if (this.state.multi || this.state.picking) next = now.some((i) => same(i, hit)) ? now.filter((i) => !same(i, hit)) : [...now, hit]
    else next = [hit]
    if (next !== now) this.set({ selected: next })
  }
  setSelection(items: readonly SketchHit[]): void {
    this.set({ selected: items })
  }
  clearSelection(): void {
    if (this.state.selected.length > 0) this.set({ selected: [] })
  }
  setPicking(on: boolean): void {
    if (on !== this.state.picking) this.set({ picking: on })
  }
  setMulti(on: boolean): void {
    this.set({ multi: on })
  }

  /** Send a `sketch.add` (the body: shape, picks, constraints ...). Resolves with what it made, or null when it was refused or the sketch is not open. */
  add(body: Record<string, unknown>): Promise<Made | null> {
    if (!this.state.open) return Promise.resolve(null)
    return new Promise((resolve) => {
      if (!this.transport.send({ type: 'sketch.add', ...body })) return resolve(null)
      this.adds.push(resolve)
    })
  }
  /**
   * Choose a tool from the strip, the Desktop toolbar or the quick menu. Delete and Construct act on the selection and change
   * no tool (I7.9). A tool that is already on is put away: the tool goes back to Select and the shape in progress, the
   * dimension's picks or a Constrain tool's picks are dropped (F20). Select chosen with Select on does nothing but tell the
   * tools to start over, so it also ends a Constrain tool's pick mode. Choosing a drawing tool drops the selection, as the
   * desktop's set_tool does, so Construct then has no selection to act on and sets the mode.
   */
  chooseTool(id: string): void {
    if (!this.state.open) return
    if (id === 'delete') return this.deleteSelected()
    if (id === 'construct') return this.toggleConstruction()
    const now = this.state.tool
    if (now === id && id !== 'select') return this.leaveTool()
    if (now !== id) return this.set({ tool: id, toolSeq: this.state.toolSeq + 1, drawing: 0, ...(isDrawTool(id) && this.state.selected.length > 0 ? { selected: [] } : {}) })
    this.set({ toolSeq: this.state.toolSeq + 1, drawing: 0 })
  }
  /**
   * A tool key (I9.7). The desktop's set_tool: the tool chosen drops the selection, and a tool already on starts over (the shape in
   * progress is given up and the tool stays on), where the same BUTTON pressed again goes back to Select (`chooseTool`).
   */
  keyTool(id: string): void {
    if (!this.state.open) return
    if (this.state.tool === id) this.set({ toolSeq: this.state.toolSeq + 1, drawing: 0, selected: [] })
    else {
      this.chooseTool(id)
      this.clearSelection()
    }
  }
  /** Esc's give-up step for the tools that hold picks of their own (the Dimension tool): true when one had something to drop. */
  escapeTools(): boolean {
    return this.tools.some((t) => t.escape?.() === true)
  }
  /** Ctrl+A: every item of the sketch is selected, as the desktop selects its entities. */
  selectAll(): void {
    const view = this.state.view
    if (this.state.open && view) this.set({ selected: Object.keys(view.sketch.entities).map((id): SketchHit => ({ kind: 'entity', id })) })
  }
  /** Say why a key did nothing, over the view. */
  showNotice(text: string): void {
    if (this.state.open) this.set({ notice: text })
  }
  /** Back to Select with whatever the tool had in progress given up. The same step as the second Esc, and the first when nothing is in progress. */
  private leaveTool(): void {
    this.set({ tool: 'select', toolSeq: this.state.toolSeq + 1, drawing: 0 })
  }
  /**
   * End the shape in progress without adding anything: a chained line stops where it is, a rectangle half placed is dropped.
   * The tool stays on. True when there was a shape to end.
   */
  endShape(): boolean {
    if (!this.state.open || this.state.drawing === 0) return false
    this.set({ toolSeq: this.state.toolSeq + 1, drawing: 0 })
    return true
  }
  /**
   * Esc, as the desktop sketcher takes it (freeshape/ui/sketcher.py `escape`): the first press ends the shape in progress, the
   * next leaves the drawing tool for Select, and with Select on it clears the selection. True when it did something.
   */
  stepBack(): boolean {
    const s = this.state
    if (!s.open) return false
    if (this.endShape()) return true
    if (s.tool !== 'select') {
      this.leaveTool()
      return true
    }
    if (s.selected.length > 0) {
      this.clearSelection()
      return true
    }
    return false
  }
  /**
   * Cancel whatever is going on and leave Select on, by Esc's own steps (`stepBack`) repeated until they have nothing left to end
   * (the quick menu's Select, F19). The selection is kept. The tool counter is bumped last: the constraint tools' pick mode
   * watches it and leaves.
   */
  toSelect(): void {
    if (!this.state.open) return
    while ((this.state.drawing !== 0 || this.state.tool !== 'select') && this.stepBack()) continue
    this.set({ toolSeq: this.state.toolSeq + 1 })
  }
  setDrawing(n: number): void {
    if (n !== this.state.drawing) this.set({ drawing: n })
  }

  /** Draw items of a drag where the pointer or the solve has them; null when the drag is over. */
  setLive(live: LiveDrag | null): void {
    if (live !== null || this.state.live !== null) this.set({ live })
  }

  /** Delete what is selected, and what depends on it (the server's rule); the selection is empty at once. */
  deleteSelected(): void {
    if (!this.state.open) return
    const ids = deletionIds(this.state.selected)
    if (ids.length === 0) return
    if (this.transport.send({ type: 'sketch.delete', ids })) this.set({ selected: [] })
  }

  /**
   * Construct, as the desktop's toggle_construction (Q): with entities selected, make them construction, or real again when they
   * all are (the selection stays, so a second press reverses it); with none, turn construction mode on or off. A selection of
   * points or dimensions only has no entity to flag, so it sets the mode too. Also the command the sketch's Q key will call.
   */
  toggleConstruction(): void {
    const view = this.state.view
    if (!this.state.open || !view) return
    const plan = constructionPlan(view.sketch, this.state.selected)
    if (plan) this.transport.send({ type: 'sketch.construction', ids: plan.ids, value: plan.value })
    else this.set({ construction: !this.state.construction })
  }

  private settleAdds(made: Made | null, all = false): void {
    const list = all ? this.adds.splice(0) : this.adds.splice(0, 1)
    for (const r of list) r(made)
  }

  private onMessage(m: Record<string, unknown>): void {
    switch (m['type']) {
      case 'sketch.opened':
      case 'sketch.state': {
        const view = parseSketchView(m, this.frame ?? undefined)
        if (!view) return
        this.frame = { frame: view.frame, edges: view.edges }
        const fresh = !this.state.open
        this.set({
          open: true,
          opening: false,
          view,
          error: null,
          live: null,
          ...(fresh ? { nav: '2d' as const, selected: [], aimed: null, multi: false, openSeq: this.state.openSeq + 1, faceSeq: this.state.faceSeq + 1, committed: null } : {}),
        })
        return
      }
      case 'sketch.constrained':
      case 'sketch.changed': {
        // a refused constraint changes nothing; an accepted one is a whole sketch like any step's answer
        if (m['type'] === 'sketch.constrained' && m['accepted'] === false) return
        const view = parseSketchView(m, this.frame ?? undefined)
        if (!view || !this.state.open) return
        // an item the step removed is no longer selected
        const alive = (h: SketchHit) =>
          h.kind === 'point' ? h.id in view.sketch.points : h.kind === 'entity' ? h.id in view.sketch.entities : view.sketch.constraints.some((c) => c.id === h.id)
        this.set({ view, error: null, selected: this.state.selected.filter(alive), aimed: null, live: null, preview: null })
        const made = m['made']
        if (typeof made === 'object' && made !== null) {
          const o = made as Record<string, unknown>
          const ids = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
          this.settleAdds({ points: ids(o['points']), entities: ids(o['entities']) })
        }
        return
      }
      case 'sketch.committed':
        this.frame = null
        this.settleAdds(null, true)
        this.set({ ...CLOSED, openSeq: this.state.openSeq, faceSeq: this.state.faceSeq, committed: typeof m['revision'] === 'number' ? m['revision'] : null })
        return
      case 'sketch.cancelled':
        this.frame = null
        this.settleAdds(null, true)
        this.set({ ...CLOSED, openSeq: this.state.openSeq, faceSeq: this.state.faceSeq })
        return
      case 'error':
        if (m['for'] === 'sketch.add') this.settleAdds(null)
        this.set({ opening: false, live: null, error: typeof m['message'] === 'string' ? m['message'] : 'The sketch could not do that.' })
        return
    }
  }
}
