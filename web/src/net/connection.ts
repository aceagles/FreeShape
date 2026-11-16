import { postEdit } from './api'
import type { DocumentReply, Holder } from './api'
import { getClientId } from './clientId'
import { RttLog, type RttSummary } from './rtt'
import { DragLatency, type DragSummary } from '../sketch/latency'

/** One entry of the server's tab list (session.tabs() plus the tab's revision, null when unknown). */
export interface TabInfo {
  id: string
  kind: string
  name: string
  state: string
  error: string | null
  dirty: boolean
  revision: number | null
}

export interface ConnectionState {
  connected: boolean
  client: string
  document: { id: string; name: string } | null
  tabs: readonly TabInfo[]
  /** tab id -> the newest revision heard of */
  revisions: Readonly<Record<string, number>>
  /** bumps on every round trip, so a panel showing `rtt` re-renders */
  rttCount: number
  /** The server opened the Document for someone else (read-only for this client), and who has the lock. */
  readOnly: boolean
  holder: Holder | null
  /** Bumps when what the tabs show may differ from what was loaded (a reconnect that found changes, a take-over). */
  epoch: number
  /** Why the Document being watched is gone: closed on the server (`idle`: after nobody was connected), or not open there any more. */
  ended: 'closed' | 'idle' | 'not-open' | null
}

/** The part of the browser's WebSocket the store uses; tests pass a fake. */
export interface SocketLike {
  readonly readyState: number
  send(data: string): void
  close(): void
  onopen: ((ev: unknown) => void) | null
  onmessage: ((ev: { data: unknown }) => void) | null
  onclose: ((ev: unknown) => void) | null
  onerror: ((ev: unknown) => void) | null
}

export interface ConnectionOptions {
  url?: string
  clientId?: string
  createSocket?: (url: string) => SocketLike
  /** reconnect delay in ms after the nth failed attempt in a row (0 is the first) */
  backoff?: (attempt: number) => number
  pingMs?: number
  now?: () => number
  visible?: () => boolean
}

const OPEN = 1
export const PING_MS = 250

export function defaultBackoff(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 8000)
}

function defaultUrl(clientId: string): string {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${location.host}/ws?client=${encodeURIComponent(clientId)}`
}

type Listener = () => void
type RevisionListener = (revision: number) => void

/** The server's `preview` message: a feature dialog's newest preview is ready to fetch (sent to the client that owns the dialog only). */
export interface PreviewMessage {
  document: string
  tab: string
  dialog: string
  /** The edited feature's id; '' for a dialog of a new feature (the server sends null). */
  feature: string
  preview: number
  /** The committed revision the preview was built on. */
  revision: number
  /** The feature built. False: it failed, and the model is the one before it (when `outputs`). */
  ok: boolean
  /** There is a model to fetch. */
  outputs: boolean
  error: string | null
}

/**
 * The live connection to `freeshape serve`: connects to /ws with this tab's client id, reconnects with backoff when
 * the socket closes, keeps the tab list and each tab's revision, and pings every 250 ms while the page is visible.
 * Use `subscribe` and `getState` (React: useConnection), or `onRevision` to refetch a tab's outputs when it changes.
 */
export class ConnectionStore {
  readonly rtt = new RttLog()
  readonly drag = new DragLatency()
  private state: ConnectionState
  private socket: SocketLike | null = null
  private listeners = new Set<Listener>()
  private tabListeners = new Map<string, Set<RevisionListener>>()
  private previewListeners = new Set<(m: PreviewMessage) => void>()
  private sketchListeners = new Set<(m: Record<string, unknown>) => void>()
  private helloListeners = new Set<() => void>()
  private retry: ReturnType<typeof setTimeout> | null = null
  private pinger: ReturnType<typeof setInterval> | null = null
  private attempts = 0
  private wanted: string | null = null
  private stopped = true
  private readonly url: string
  private readonly create: (url: string) => SocketLike
  private readonly backoff: (attempt: number) => number
  private readonly pingMs: number
  private readonly now: () => number
  private readonly visible: () => boolean

  constructor(options: ConnectionOptions = {}) {
    const client = options.clientId ?? getClientId()
    this.url = options.url ?? defaultUrl(client)
    this.create = options.createSocket ?? ((url) => new WebSocket(url) as unknown as SocketLike)
    this.backoff = options.backoff ?? defaultBackoff
    this.pingMs = options.pingMs ?? PING_MS
    this.now = options.now ?? (() => performance.now())
    this.visible = options.visible ?? (() => typeof document === 'undefined' || document.visibilityState !== 'hidden')
    this.state = {
      connected: false,
      client,
      document: null,
      tabs: [],
      revisions: {},
      rttCount: 0,
      readOnly: false,
      holder: null,
      epoch: 0,
      ended: null,
    }
  }

  readonly subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  readonly getState = (): ConnectionState => this.state

  rttSummary(): RttSummary {
    return this.rtt.summary()
  }

  dragSummary(): DragSummary {
    return this.drag.summary()
  }

  /** The connection's clock in ms: stamp a `sketch.drag.move` with it as `t` so its answer gives the round trip. */
  stamp(): number {
    return this.now()
  }

  /** Call `listener` with the new revision whenever `tab`'s revision changes; returns the unsubscribe function. */
  onRevision(tab: string, listener: RevisionListener): () => void {
    let set = this.tabListeners.get(tab)
    if (!set) this.tabListeners.set(tab, (set = new Set()))
    set.add(listener)
    return () => {
      set.delete(listener)
    }
  }

  /** Call `listener` with every `preview` message of the Document being watched; returns the unsubscribe function. */
  onPreview(listener: (m: PreviewMessage) => void): () => void {
    this.previewListeners.add(listener)
    return () => {
      this.previewListeners.delete(listener)
    }
  }

  /** Call `listener` with every `sketch.*` message, and every error for one, that the server sends this client (a sketch session, I7.3); returns the unsubscribe function. */
  onSketch(listener: (m: Record<string, unknown>) => void): () => void {
    this.sketchListeners.add(listener)
    return () => {
      this.sketchListeners.delete(listener)
    }
  }

  /** Call `listener` each time the server greets the socket (the first connection and every reconnect); returns the unsubscribe function. */
  onHello(listener: () => void): () => void {
    this.helloListeners.add(listener)
    return () => {
      this.helloListeners.delete(listener)
    }
  }

  /** Send one JSON message on the socket. False when the socket is not open (nothing is queued). */
  send(msg: Record<string, unknown>): boolean {
    if (!this.socket || this.socket.readyState !== OPEN) return false
    this.socket.send(JSON.stringify(msg))
    return true
  }

  /**
   * Follow Document `id`: subscribe the socket to it (again after every reconnect) and keep its tabs, revisions and
   * lock. `seed` is the answer of the open call, so the screen has the Document before the socket's hello.
   */
  watch(id: string, seed?: DocumentReply): void {
    const same = this.state.document?.id === id
    this.wanted = id
    this.update(same ? { ended: null } : { document: null, tabs: [], revisions: {}, readOnly: false, holder: null, ended: null })
    if (seed) this.apply(seed, false)
    this.sendSubscribe()
  }

  /** Stop following the Document: unsubscribe and forget its tabs. */
  unwatch(): void {
    const id = this.wanted
    this.wanted = null
    if (id !== null) this.sendJson({ type: 'unsubscribe', document: id })
    this.update({ document: null, tabs: [], revisions: {}, readOnly: false, holder: null, ended: null })
  }

  /** Take in a tab list the server answered over HTTP (the socket pushes the same list to everyone). */
  setTabs(tabs: readonly TabInfo[]): void {
    this.onTabs(tabs, {})
  }

  /** Take in the lock state the server answered over HTTP. */
  setLock(readOnly: boolean, holder: Holder | null): void {
    this.update({ readOnly, holder })
  }

  /** Ask the panels to load what they show again. */
  reload(): void {
    this.update({ epoch: this.state.epoch + 1 })
  }

  /** One edit of `tab`, made by this client on the revision it last heard of. */
  edit(tab: string, edit: Record<string, unknown>): Promise<{ rebuilt: string[] }> {
    const doc = this.wanted ?? this.state.document?.id
    if (!doc) return Promise.reject(new Error('no Document is open'))
    return postEdit(doc, tab, edit, this.state.client, this.state.revisions[tab] ?? null)
  }

  private sendSubscribe(): void {
    if (this.wanted !== null) this.sendJson({ type: 'subscribe', document: this.wanted })
  }

  private sendJson(msg: object): void {
    if (this.socket && this.socket.readyState === OPEN) this.socket.send(JSON.stringify(msg))
  }

  start(): void {
    if (!this.stopped) return
    this.stopped = false
    this.pinger = setInterval(() => this.ping(), this.pingMs)
    document.addEventListener?.('visibilitychange', this.onVisible)
    this.open()
  }

  stop(): void {
    this.stopped = true
    if (this.retry !== null) clearTimeout(this.retry)
    if (this.pinger !== null) clearInterval(this.pinger)
    this.retry = this.pinger = null
    document.removeEventListener?.('visibilitychange', this.onVisible)
    const socket = this.socket
    this.socket = null
    if (socket) {
      socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null
      socket.close()
    }
    this.update({ connected: false })
  }

  private readonly onVisible = (): void => {
    // iPadOS drops the socket while the page is hidden; come back at once instead of waiting out the backoff
    if (this.stopped || !this.visible() || this.socket) return
    if (this.retry !== null) clearTimeout(this.retry)
    this.retry = null
    this.open()
  }

  private open(): void {
    this.retry = null
    let socket: SocketLike
    try {
      socket = this.create(this.url)
    } catch {
      this.scheduleRetry()
      return
    }
    this.socket = socket
    socket.onopen = () => {
      this.attempts = 0
      this.sendSubscribe()
    }
    socket.onmessage = (ev) => this.onMessage(ev.data)
    socket.onclose = () => {
      if (this.socket !== socket) return
      this.socket = null
      this.update({ connected: false })
      this.scheduleRetry()
    }
    socket.onerror = () => {
      // a close event follows; nothing to do here
    }
  }

  private scheduleRetry(): void {
    if (this.stopped || this.retry !== null) return
    const delay = this.backoff(this.attempts++)
    this.retry = setTimeout(() => this.open(), delay)
  }

  private ping(): void {
    if (!this.socket || this.socket.readyState !== OPEN || !this.state.connected || !this.visible()) return
    this.socket.send(JSON.stringify({ type: 'ping', t: this.now() }))
  }

  private onMessage(data: unknown): void {
    if (typeof data !== 'string') return
    let msg: unknown
    try {
      msg = JSON.parse(data)
    } catch {
      return
    }
    if (typeof msg !== 'object' || msg === null) return
    const m = msg as Record<string, unknown>
    const about = typeof m.document === 'string' ? m.document : null
    if (m.type !== 'hello' && about !== null && this.wanted !== null && about !== this.wanted) return
    if (typeof m.type === 'string' && (m.type.startsWith('sketch.') || (m.type === 'error' && typeof m.for === 'string' && m.for.startsWith('sketch.')))) {
      this.sketchListeners.forEach((l) => l(m))
      // the drag round trip is the connection's own (the debug panel), so that answer goes on to the switch below
      if (m.type !== 'sketch.drag.moved') return
    }
    switch (m.type) {
      case 'hello': {
        const doc = parseDocument(m.document)
        if (doc === null || (this.wanted !== null && doc.id !== this.wanted)) {
          this.update({ connected: true })
        } else {
          this.apply(m as unknown as DocumentReply, true)
          this.update({ connected: true })
        }
        this.helloListeners.forEach((l) => l())
        break
      }
      case 'tabs':
        this.onTabs(m.tabs, {})
        break
      case 'changed':
        this.onChanged(m.revisions)
        break
      case 'lock':
        if (typeof m.read_only === 'boolean') this.update({ readOnly: m.read_only, holder: parseHolder(m.holder) })
        break
      case 'closed':
        this.update({ document: null, tabs: [], revisions: {}, readOnly: false, holder: null, ended: m.reason === 'idle' ? 'idle' : 'closed' })
        break
      case 'error':
        if (about !== null && about === this.wanted && m.id === undefined) {
          this.update({ document: null, tabs: [], revisions: {}, readOnly: false, holder: null, ended: 'not-open' })
        }
        break
      case 'preview': {
        const p = parsePreview(m)
        if (p !== null) this.previewListeners.forEach((l) => l(p))
        break
      }
      case 'sketch.drag.moved':
        if (typeof m.t === 'number' && typeof m.solve_ms === 'number') {
          this.drag.record(this.now() - m.t, m.solve_ms)
          this.update({ rttCount: this.state.rttCount + 1 })
        }
        break
      case 'pong':
        if (typeof m.t === 'number') {
          this.rtt.record(this.now() - m.t)
          this.update({ rttCount: this.state.rttCount + 1 })
        }
        break
    }
  }

  /** A Document's state from a hello or an open call. `fromHello` is true for a hello (a reload may follow). */
  private apply(m: DocumentReply, fromHello: boolean): void {
    const doc = parseDocument(m.document)
    if (doc === null) return
    const known = this.state.revisions
    const tabs = Array.isArray(m.tabs) ? (m.tabs.filter(isTabInfo) as TabInfo[]) : []
    const differs = fromHello && tabs.some((t) => t.revision !== null && known[t.id] !== undefined && known[t.id] !== t.revision)
    this.onTabs(tabs, {
      document: doc,
      readOnly: m.read_only === true,
      holder: parseHolder(m.holder),
      ended: null,
      ...(differs ? { epoch: this.state.epoch + 1 } : {}),
    }, true)
  }

  private onTabs(raw: unknown, extra: Partial<ConnectionState>, replace = false): void {
    const tabs = Array.isArray(raw) ? (raw.filter(isTabInfo) as TabInfo[]) : this.state.tabs
    const found: Record<string, number> = {}
    for (const t of tabs) if (t.revision !== null) found[t.id] = t.revision
    this.applyRevisions(found, { ...extra, tabs }, replace)
  }

  private onChanged(raw: unknown): void {
    const found: Record<string, number> = {}
    if (typeof raw === 'object' && raw !== null) {
      for (const [id, rev] of Object.entries(raw)) if (typeof rev === 'number') found[id] = rev
    }
    this.applyRevisions(found, {})
  }

  /** Keep the newest revision per tab; tell the listeners of the tabs that moved after the state is in place. */
  private applyRevisions(found: Record<string, number>, extra: Partial<ConnectionState>, replace = false): void {
    const revisions = replace ? {} : { ...this.state.revisions }
    const before = this.state.revisions
    const moved: Array<[string, number]> = []
    for (const [id, rev] of Object.entries(found)) {
      if (revisions[id] === undefined || rev > revisions[id]) {
        if (before[id] !== undefined && rev > before[id]) moved.push([id, rev])
        revisions[id] = rev
      }
    }
    this.update({ ...extra, revisions })
    for (const [id, rev] of moved) this.tabListeners.get(id)?.forEach((l) => l(rev))
  }

  private update(patch: Partial<ConnectionState>): void {
    this.state = { ...this.state, ...patch }
    this.listeners.forEach((l) => l())
  }
}

function parsePreview(m: Record<string, unknown>): PreviewMessage | null {
  if (typeof m.document !== 'string' || typeof m.tab !== 'string' || typeof m.dialog !== 'string' || (typeof m.feature !== 'string' && m.feature !== null)) return null
  if (typeof m.preview !== 'number' || typeof m.revision !== 'number') return null
  return {
    document: m.document,
    tab: m.tab,
    dialog: m.dialog,
    feature: typeof m.feature === 'string' ? m.feature : '', // null for a dialog of a new feature: the engine gives it an id at Accept
    preview: m.preview,
    revision: m.revision,
    ok: m.ok === true,
    outputs: m.outputs === true,
    error: typeof m.error === 'string' ? m.error : null,
  }
}

function parseDocument(raw: unknown): { id: string; name: string } | null {
  if (typeof raw !== 'object' || raw === null) return null
  const { id, name } = raw as { id?: unknown; name?: unknown }
  return typeof id === 'string' && typeof name === 'string' ? { id, name } : null
}

function parseHolder(raw: unknown): Holder | null {
  if (typeof raw !== 'object' || raw === null) return null
  const h = raw as Record<string, unknown>
  if (typeof h.program !== 'string') return null
  return {
    program: h.program,
    instance: typeof h.instance === 'string' ? h.instance : '',
    heartbeat: typeof h.heartbeat === 'number' ? h.heartbeat : 0,
    stale: h.stale === true,
  }
}

function isTabInfo(raw: unknown): raw is TabInfo {
  if (typeof raw !== 'object' || raw === null) return false
  const t = raw as Record<string, unknown>
  return typeof t.id === 'string' && (t.revision === null || typeof t.revision === 'number')
}
