import type { AssemblyOutputs, StudioOutputs } from '../wire'

/**
 * What the visibility keys keep in the client, per tab (card I9.8): the View settings the desktop keeps in its window
 * (`MainWindow.act_planes`, `show_axes`, `show_connectors`, `_transparent`). Parts, sketches and the Assembly's instances are
 * shown and hidden on the server (shell/visibilityCommands.ts); nothing here is sent anywhere or saved with the Document.
 * The viewport reads the state of its tab and draws by it.
 */
export interface ViewState {
  /**
   * The default planes (Top, Front, Right) that are hidden. The server stores such a hide (`hidden_features` takes their names)
   * but does not send it back, so the client keeps its own, per tab; it is lost on a reload. The Origin rows' eyes and P change it.
   * The planes a person made, and the axes and sketches, keep their state on the server: the eye of their row.
   */
  readonly hiddenPlanes: readonly string[]
  /** K: mate connectors are drawn. Nothing draws them until card I10.3. */
  readonly connectors: boolean
  /** J: mates are drawn. Nothing draws them until card I10.3. */
  readonly mates: boolean
  /** Shift+I: only these parts (a Part Studio) or instances (an Assembly) are drawn; null is no isolation. */
  readonly isolate: readonly string[] | null
  /** Shift+T: these parts or instances are drawn see-through. */
  readonly transparent: readonly string[]
}

export const DEFAULT_VIEW: ViewState = { hiddenPlanes: [], connectors: true, mates: true, isolate: null, transparent: [] }

type Listener = () => void

/** The view state of one tab: a snapshot that is a new object on every change (for `useSyncExternalStore`). */
export class TabView {
  private state: ViewState = DEFAULT_VIEW
  private readonly listeners = new Set<Listener>()

  readonly getState = (): ViewState => this.state

  readonly subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l)
    return () => {
      this.listeners.delete(l)
    }
  }

  private set(patch: Partial<ViewState>): void {
    this.state = { ...this.state, ...patch }
    for (const l of [...this.listeners]) l()
  }

  /** Hide these default planes and show the rest (P and Shift+P decide with the planes' own eyes, see shell/visibilityCommands.ts). */
  setHiddenPlanes(names: readonly string[]): void {
    const same = names.length === this.state.hiddenPlanes.length && names.every((n) => this.state.hiddenPlanes.includes(n))
    if (!same) this.set({ hiddenPlanes: [...names] })
  }

  /** The eye of an Origin row: show or hide one default plane. */
  toggleDefaultPlane = (name: string): void => {
    const h = this.state.hiddenPlanes
    this.set({ hiddenPlanes: h.includes(name) ? h.filter((n) => n !== name) : [...h, name] })
  }

  /** Set the mate connectors and mates flags (Shift+P in an Assembly). */
  setConnectorsAndMates(on: boolean): void {
    this.set({ connectors: on, mates: on })
  }

  /** Set the mate connectors flag (Shift+P in a Part Studio). */
  setConnectors(on: boolean): void {
    if (on !== this.state.connectors) this.set({ connectors: on })
  }

  /** K. */
  toggleConnectors(): boolean {
    this.set({ connectors: !this.state.connectors })
    return this.state.connectors
  }

  /** J. */
  toggleMates(): boolean {
    this.set({ mates: !this.state.mates })
    return this.state.mates
  }

  /** Shift+I: isolate `ids`; while something is isolated, end the isolation instead. Returns whether something is isolated now. */
  toggleIsolate(ids: readonly string[]): boolean {
    if (this.state.isolate !== null) this.set({ isolate: null })
    else if (ids.length > 0) this.set({ isolate: [...ids] })
    return this.state.isolate !== null
  }

  /** End isolation (Shift+Y shows every part, the isolated ones' neighbours too). */
  endIsolation(): void {
    if (this.state.isolate !== null) this.set({ isolate: null })
  }

  /**
   * Shift+T (`_vis_transparent`): with nothing aimed at, every part is solid again; with parts that are all transparent already,
   * they are solid again; otherwise they become transparent. Returns the ids that are transparent now.
   */
  toggleTransparent(ids: readonly string[]): readonly string[] {
    const cur = new Set(this.state.transparent)
    if (ids.length === 0) cur.clear()
    else if (ids.every((i) => cur.has(i))) for (const i of ids) cur.delete(i)
    else for (const i of ids) cur.add(i)
    this.set({ transparent: [...cur] })
    return this.state.transparent
  }
}

/** Every tab's view state, made when it is first asked for. */
export class ViewVisibility {
  private readonly tabs = new Map<string, TabView>()

  /** `key` names the tab across Documents (the Document id and the tab id). */
  tab(key: string): TabView {
    let t = this.tabs.get(key)
    if (t === undefined) {
      t = new TabView()
      this.tabs.set(key, t)
    }
    return t
  }
}

/** The ids of `ids` that `known` has; null when none is, so a stale isolation (its parts deleted) hides nothing. */
function live(ids: readonly string[] | null, known: ReadonlySet<string>): ReadonlySet<string> | null {
  if (ids === null) return null
  const keep = new Set(ids.filter((i) => known.has(i)))
  return keep.size > 0 ? keep : null
}

/** The studio as the view draws it: with an isolation, every part but the isolated ones is hidden (Context bodies are left). */
export function shapeStudio(outputs: StudioOutputs, view: ViewState): StudioOutputs {
  const keep = live(view.isolate, new Set(outputs.parts.map((p) => p.id)))
  if (keep === null) return outputs
  return { ...outputs, parts: outputs.parts.map((p) => (keep.has(p.id) ? (p.hidden ? { ...p, hidden: false } : p) : p.hidden ? p : { ...p, hidden: true })) }
}

/** The Assembly as the view draws it: with an isolation, every instance but the isolated ones is hidden. */
export function shapeAssembly(outputs: AssemblyOutputs, view: ViewState): AssemblyOutputs {
  const keep = live(view.isolate, new Set(outputs.instances.map((i) => i.id)))
  if (keep === null) return outputs
  return { ...outputs, instances: outputs.instances.map((i) => (keep.has(i.id) ? (i.hidden ? { ...i, hidden: false } : i) : i.hidden ? i : { ...i, hidden: true })) }
}
