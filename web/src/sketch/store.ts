import type { SketchHit } from './pick'
import { parseSketchView } from './wire'
import type { Frame, FrameEdge, SketchView } from './wire'

/** 2D keeps the view square to the sketch plane (one finger pans, two pan, zoom and twist); 3D orbits as the Part Studio does. */
export type SketchNav = '2d' | '3d'

/** What the sketch store needs of the live connection (ConnectionStore has it). */
export interface SketchTransport {
  send(msg: Record<string, unknown>): boolean
  /** Every `sketch.*` answer and every error for a sketch message of the Document being watched. */
  onSketch(listener: (m: Record<string, unknown>) => void): () => void
  /** The socket (re)connected: a sketch that was open is asked for again. */
  onHello(listener: () => void): () => void
}

/** What to open: an existing sketch feature, or a new sketch on a plane reference (as a sketch's `plane` param stores it). */
export type SketchTarget = { document: string; tab: string } & ({ feature: string } | { plane: Record<string, unknown> })

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
}

const CLOSED: SketchState = { open: false, opening: false, view: null, nav: '2d', selected: [], aimed: null, multi: false, error: null, faceSeq: 0, openSeq: 0, committed: null }

const same = (a: SketchHit | null, b: SketchHit | null): boolean => a === b || (a !== null && b !== null && a.kind === b.kind && a.id === b.id)

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

  constructor(private readonly transport: SketchTransport) {
    this.attach()
  }

  /** Listen to the connection (done by the constructor; call it again after `detach`, as React's strict mode does). */
  attach(): void {
    if (this.off.length > 0) return
    this.off = [this.transport.onSketch((m) => this.onMessage(m)), this.transport.onHello(() => this.state.open && this.transport.send({ type: 'sketch.state' }))]
  }

  /** Stop listening to the connection. */
  detach(): void {
    for (const f of this.off) f()
    this.off = []
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  readonly getState = (): SketchState => this.state

  private set(patch: Partial<SketchState>): void {
    this.state = { ...this.state, ...patch }
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
    if (hit === null) next = this.state.multi ? now : []
    else if (this.state.multi) next = now.some((i) => same(i, hit)) ? now.filter((i) => !same(i, hit)) : [...now, hit]
    else next = [hit]
    if (next !== now) this.set({ selected: next })
  }
  setSelection(items: readonly SketchHit[]): void {
    this.set({ selected: items })
  }
  clearSelection(): void {
    if (this.state.selected.length > 0) this.set({ selected: [] })
  }
  setMulti(on: boolean): void {
    this.set({ multi: on })
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
        this.set({ view, error: null, selected: this.state.selected.filter(alive), aimed: null })
        return
      }
      case 'sketch.committed':
        this.frame = null
        this.set({ ...CLOSED, openSeq: this.state.openSeq, faceSeq: this.state.faceSeq, committed: typeof m['revision'] === 'number' ? m['revision'] : null })
        return
      case 'sketch.cancelled':
        this.frame = null
        this.set({ ...CLOSED, openSeq: this.state.openSeq, faceSeq: this.state.faceSeq })
        return
      case 'error':
        this.set({ opening: false, error: typeof m['message'] === 'string' ? m['message'] : 'The sketch could not do that.' })
        return
    }
  }
}
