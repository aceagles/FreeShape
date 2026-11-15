import { getClientId } from './clientId'
import { RttLog, type RttSummary } from './rtt'

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

/**
 * The live connection to `freeshape serve`: connects to /ws with this tab's client id, reconnects with backoff when
 * the socket closes, keeps the tab list and each tab's revision, and pings every 250 ms while the page is visible.
 * Use `subscribe` and `getState` (React: useConnection), or `onRevision` to refetch a tab's outputs when it changes.
 */
export class ConnectionStore {
  readonly rtt = new RttLog()
  private state: ConnectionState
  private socket: SocketLike | null = null
  private listeners = new Set<Listener>()
  private tabListeners = new Map<string, Set<RevisionListener>>()
  private retry: ReturnType<typeof setTimeout> | null = null
  private pinger: ReturnType<typeof setInterval> | null = null
  private attempts = 0
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
    this.state = { connected: false, client, document: null, tabs: [], revisions: {}, rttCount: 0 }
  }

  readonly subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  readonly getState = (): ConnectionState => this.state

  rttSummary(): RttSummary {
    return this.rtt.summary()
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
    switch (m.type) {
      case 'hello':
        this.onTabs(m.tabs, { connected: true, document: parseDocument(m.document) })
        break
      case 'tabs':
        this.onTabs(m.tabs, {})
        break
      case 'changed':
        this.onChanged(m.revisions)
        break
      case 'pong':
        if (typeof m.t === 'number') {
          this.rtt.record(this.now() - m.t)
          this.update({ rttCount: this.state.rttCount + 1 })
        }
        break
    }
  }

  private onTabs(raw: unknown, extra: Partial<ConnectionState>): void {
    const tabs = Array.isArray(raw) ? (raw.filter(isTabInfo) as TabInfo[]) : this.state.tabs
    const found: Record<string, number> = {}
    for (const t of tabs) if (t.revision !== null) found[t.id] = t.revision
    this.applyRevisions(found, { ...extra, tabs })
  }

  private onChanged(raw: unknown): void {
    const found: Record<string, number> = {}
    if (typeof raw === 'object' && raw !== null) {
      for (const [id, rev] of Object.entries(raw)) if (typeof rev === 'number') found[id] = rev
    }
    this.applyRevisions(found, {})
  }

  /** Keep the newest revision per tab; tell the listeners of the tabs that moved after the state is in place. */
  private applyRevisions(found: Record<string, number>, extra: Partial<ConnectionState>): void {
    const revisions = { ...this.state.revisions }
    const moved: Array<[string, number]> = []
    for (const [id, rev] of Object.entries(found)) {
      if (revisions[id] === undefined || rev > revisions[id]) {
        if (revisions[id] !== undefined) moved.push([id, rev])
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

function parseDocument(raw: unknown): { id: string; name: string } | null {
  if (typeof raw !== 'object' || raw === null) return null
  const { id, name } = raw as { id?: unknown; name?: unknown }
  return typeof id === 'string' && typeof name === 'string' ? { id, name } : null
}

function isTabInfo(raw: unknown): raw is TabInfo {
  if (typeof raw !== 'object' || raw === null) return false
  const t = raw as Record<string, unknown>
  return typeof t.id === 'string' && (t.revision === null || typeof t.revision === 'number')
}
