import type { SketchHit } from './pick'
import type { SketchStore, SketchTransport } from './store'

/**
 * The constraint tools the server offers (sketch.constrain's `tool`, in the order sketch.fits lists them). There is no
 * "point on" tool: the server makes that constraint from Coincident on a point and a curve.
 */
export const CONSTRAIN_TOOLS: readonly string[] = ['coincident', 'horizontal', 'vertical', 'parallel', 'perpendicular', 'tangent', 'equal', 'midpoint', 'concentric', 'symmetric', 'fix', 'normal']
const TOOL_SET: ReadonlySet<string> = new Set(CONSTRAIN_TOOLS)

export const isConstrainTool = (id: string): boolean => TOOL_SET.has(id)

/** What each tool wants picked, in the desktop sketcher's words (freeshape/ui/sketcher.py CONSTRAINT_TOOLS). */
const WANTS: Readonly<Record<string, string>> = {
  coincident: 'two points, or a point and a curve',
  horizontal: 'a line or two points',
  vertical: 'a line or two points',
  parallel: 'two lines',
  perpendicular: 'two lines',
  tangent: 'a line and an arc or circle, or two arcs or circles',
  equal: 'two lines or two arcs or circles',
  midpoint: 'a point and a line',
  concentric: 'two arcs or circles',
  symmetric: 'two points (or two circles or lines) and the line they are symmetric about',
  fix: 'points or curves to fix in place',
  normal: 'a line and an arc or circle',
}
/** How many picks a tool needs before a refusal means "does not fit" rather than "pick more". */
const NEED: Readonly<Record<string, number>> = { fix: 1, symmetric: 3 }

export interface ConstraintState {
  /** The tools the server says take the current selection (sketch.fits): a hint only, every tool can be chosen. Empty while it has not answered. */
  enabled: ReadonlySet<string>
  /** The tool that is collecting picks (chosen when the selection did not fit it yet), or null. */
  picking: string | null
  /** The last refused constraint, in words; cleared by the next selection or constraint. */
  message: string | null
}

const NONE: ReadonlySet<string> = new Set()
const label = (tool: string): string => tool.charAt(0).toUpperCase() + tool.slice(1)

/** The points and entities of a selection, in the order picked: what sketch.fits and sketch.constrain take. */
const pickIds = (items: readonly SketchHit[]): string[] => items.filter((i) => i.kind === 'point' || i.kind === 'entity').map((i) => i.id)

/**
 * Constraints in the sketch strip (card I7.10). The client holds no fitting rules: it asks the server (sketch.fits) which
 * tools take the selection, sends sketch.constrain with the tool and the picks, and shows the answer. A refused
 * constraint changes nothing.
 */
export class ConstraintStore {
  private state: ConstraintState = { enabled: NONE, picking: null, message: null }
  private readonly listeners = new Set<() => void>()
  private off: (() => void)[] = []
  private asked = ''
  private seen: unknown = null
  private pending: string | null = null
  private pickSeq = 0

  constructor(
    private readonly sketch: SketchStore,
    private readonly transport: SketchTransport,
  ) {
    this.attach()
  }

  attach(): void {
    if (this.off.length > 0) return
    this.off = [this.transport.onSketch((m) => this.onMessage(m)), this.sketch.subscribe(() => this.follow())]
  }
  detach(): void {
    for (const f of this.off) f()
    this.off = []
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  readonly getState = (): ConstraintState => this.state

  private set(patch: Partial<ConstraintState>): void {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach((l) => l())
  }

  /** The sketch store changed: ask which tools fit the selection when the selection or the sketch changed. */
  private follow(): void {
    const s = this.sketch.getState()
    if (this.state.picking !== null && (!s.open || s.tool !== 'select' || s.toolSeq !== this.pickSeq)) this.leave()
    const ids = s.open ? pickIds(s.selected) : []
    const key = ids.join('\n')
    const view = s.view
    if (key === this.asked && view === this.seen) return
    const selectionMoved = key !== this.asked
    this.asked = key
    this.seen = view
    if (ids.length === 0) {
      if (this.state.enabled.size > 0 || this.state.message !== null) this.set({ enabled: NONE, message: null })
      return
    }
    if (selectionMoved && this.state.message !== null) this.set({ message: null })
    this.transport.send({ type: 'sketch.fits', selection: ids })
  }

  /** Whether the server listed a tool for the current selection: the button's marker. */
  fits(tool: string): boolean {
    return this.state.enabled.has(tool)
  }

  /**
   * A constraint button was tapped (the desktop's set_tool): the tool already picked is put away; a selection the server says
   * fits is sent at once, in the order picked; otherwise the tool waits for picks, the selection counting as the first.
   */
  choose(tool: string): void {
    const s = this.sketch.getState()
    if (!s.open || !isConstrainTool(tool)) return
    if (this.state.picking === tool) return this.leave()
    if (this.state.picking !== null) this.leave()
    if (pickIds(s.selected).length > 0 && this.state.enabled.has(tool) && this.send(tool)) return
    this.pickSeq = s.toolSeq
    this.sketch.setPicking(true)
    this.set({ picking: tool, message: null })
  }

  /** Leave pick mode (Esc, another tool, the tool tapped again). The selection stays. */
  leave(): void {
    if (this.state.picking === null) return
    this.sketch.setPicking(false)
    this.set({ picking: null })
  }

  /** Esc: in pick mode, drop the picks and leave it. Returns whether it did anything. */
  escape(): boolean {
    if (this.state.picking === null) return false
    this.leave()
    this.sketch.clearSelection()
    return true
  }

  /** What to tell the user while a tool collects picks. */
  hint(): string | null {
    const tool = this.state.picking
    if (tool === null) return null
    const n = pickIds(this.sketch.getState().selected).length
    return `${label(tool)}: select ${WANTS[tool] ?? 'what it constrains'}${n > 0 ? ` (${n} picked)` : ''}`
  }

  private send(tool: string): boolean {
    const s = this.sketch.getState()
    const picks = pickIds(s.selected)
    if (picks.length === 0 || !this.transport.send({ type: 'sketch.constrain', tool, picks })) return false
    this.pending = tool
    this.sketch.clearSelection()
    this.set({ enabled: NONE, message: null })
    return true
  }

  /** Delete: remove every constraint in the selection. */
  removeSelected(): boolean {
    const s = this.sketch.getState()
    if (!s.open) return false
    const picked = s.selected.filter((c) => c.kind === 'constraint')
    for (const c of picked) this.transport.send({ type: 'sketch.unconstrain', constraint: c.id })
    return picked.length > 0
  }

  private onMessage(m: Record<string, unknown>): void {
    switch (m['type']) {
      case 'sketch.fitted': {
        const sel = m['selection']
        if (!Array.isArray(sel) || sel.join('\n') !== this.asked || !Array.isArray(m['tools'])) return
        const tools = new Set(m['tools'].filter((t): t is string => typeof t === 'string' && TOOL_SET.has(t)))
        this.set({ enabled: tools })
        const tool = this.state.picking
        if (tool === null) return
        // the picks fit the tool: make it, and stay in pick mode for the next one, as the desktop does
        if (tools.has(tool)) this.send(tool)
        else if (sel.length >= (NEED[tool] ?? 2)) this.set({ message: `Those do not fit ${label(tool)}. Select ${WANTS[tool] ?? 'what it constrains'}.` })
        return
      }
      case 'sketch.constrained': {
        const tool = this.pending
        this.pending = null
        if (m['accepted'] === false) this.set({ message: `${tool === null ? 'That constraint' : label(tool)} conflicts with the constraints already there. Nothing changed.` })
        return
      }
      case 'error':
        if (m['for'] === 'sketch.constrain') this.pending = null
        return
    }
  }
}
