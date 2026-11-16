import type { SketchHit } from './pick'
import type { SketchStore, SketchTransport } from './store'
import type { ToolState } from '../tools/useToolTypes'

/**
 * The constraint tools the server offers (sketch.constrain's `tool`, in the order sketch.fits lists them). There is no
 * "point on" tool: the server makes that constraint from Coincident on a point and a curve.
 */
export const CONSTRAIN_TOOLS: readonly string[] = ['coincident', 'horizontal', 'vertical', 'parallel', 'perpendicular', 'tangent', 'equal', 'midpoint', 'concentric', 'symmetric', 'fix', 'normal']
const TOOL_SET: ReadonlySet<string> = new Set(CONSTRAIN_TOOLS)

export const isConstrainTool = (id: string): boolean => TOOL_SET.has(id)

export interface ConstraintState {
  /** The tools the server says take the current selection (sketch.fits); empty while it has not answered. */
  enabled: ReadonlySet<string>
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
  private state: ConstraintState = { enabled: NONE, message: null }
  private readonly listeners = new Set<() => void>()
  private off: (() => void)[] = []
  private asked = ''
  private seen: unknown = null
  private pending: string | null = null

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

  /** Whether a tool's button is on: the server listed it for the current selection. */
  stateOf(tool: string): ToolState {
    return this.state.enabled.has(tool) ? 'ok' : 'dimmed'
  }

  /** A constraint button was tapped: send the tool with the selection, in the order picked, and clear the selection. */
  apply(tool: string): void {
    const s = this.sketch.getState()
    if (!s.open || !isConstrainTool(tool) || !this.state.enabled.has(tool)) return
    const picks = pickIds(s.selected)
    if (picks.length === 0) return
    if (!this.transport.send({ type: 'sketch.constrain', tool, picks })) return
    this.pending = tool
    this.sketch.clearSelection()
    this.set({ enabled: NONE, message: null })
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
        this.set({ enabled: new Set(m['tools'].filter((t): t is string => typeof t === 'string' && TOOL_SET.has(t))) })
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
