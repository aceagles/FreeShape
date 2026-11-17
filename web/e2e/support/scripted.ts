import type { Page, WebSocketRoute } from '@playwright/test'

// A scripted server for the static specs: the Document routes and the socket, answering from memory. It has no
// timers: it only ever answers a request, or sends what the test tells it to (`push`), so a test decides the order.

export interface Holder {
  program: string
  instance: string
  heartbeat: number
  stale: boolean
}
export interface Tab {
  id: string
  kind: string
  name: string
  state: string
  error: string | null
  dirty: boolean
  revision: number | null
}
/** What the server offers to recover (the open reply's `autosave`). */
export interface Autosave {
  time: number
  count: number
  tabs: Array<{ id: string; name: string }>
  deleted: string[]
  moved_on: string[]
  list_moved_on: boolean
  writable: boolean
}
export interface Doc {
  id: string
  name: string
  modified: number | null
  holder: Holder | null
  open: boolean
  missing: boolean
  tabs: Tab[]
  /** An autosave the server kept for this Document, until it is recovered or discarded. */
  autosave?: Autosave | null
}

export const tab = (id: string, kind: string, name: string, dirty = false): Tab => ({ id, kind, name, state: 'ok', error: null, dirty, revision: 1 })
export const holder = (program: string): Holder => ({ program, instance: 'x', heartbeat: 1, stale: false })

export class Scripted {
  /** Every request that changed something and every socket message the page sent, in order. */
  log: string[] = []
  private sockets: WebSocketRoute[] = []
  private made = 0
  /** tab id -> the tab that links to it (it cannot be deleted while that one exists). */
  links: Record<string, string> = { T1: 'T2' }
  constructor(public docs: Doc[]) {}

  doc(id: string): Doc {
    const d = this.docs.find((x) => x.id === id)
    if (!d) throw new Error(`no scripted Document ${id}`)
    return d
  }

  private reply(d: Doc, readOnly = d.holder !== null && d.holder.program !== 'browser') {
    return { document: { id: d.id, name: d.name }, tabs: d.tabs, read_only: readOnly, holder: readOnly ? d.holder : null, autosave: d.autosave ?? null }
  }

  /** Tell the page something over the socket, as the server would. */
  push(msg: object): void {
    for (const s of this.sockets) s.send(JSON.stringify(msg))
  }

  async install(page: Page): Promise<void> {
    await page.route('**/health', (route) => route.fulfill({ json: { ok: true, version: '1.2.3' } }))
    await page.route(/\/documents(\/|$)/, async (route) => {
      const req = route.request()
      const path = new URL(req.url()).pathname
      const method = req.method()
      const json = (status: number, body: object) => route.fulfill({ status, json: body })
      // the client's one probe for a Documents root (card I11.10): an empty archive is refused, as a real root does; it is not a step of any flow, so it is not logged
      if (path === '/documents/archive' && method === 'POST') return json(422, { error: 'not a zip file' })
      if (method !== 'GET') this.log.push(`${method} ${path}`)
      if (path === '/documents') {
        if (method === 'GET') {
          return json(200, {
            documents: this.docs.map((d) => ({
              id: d.id,
              name: d.name,
              modified: d.modified,
              holder: d.holder,
              open: d.open,
              missing: d.missing,
              autosave: d.autosave ? { time: d.autosave.time, count: d.autosave.count } : null,
            })),
          })
        }
        const name = (req.postDataJSON() as { name: string }).name
        const d: Doc = { id: `new${++this.made}`, name, modified: Date.now() / 1000, holder: null, open: false, missing: false, tabs: [tab('T1', 'studio', 'Part Studio 1')] }
        this.docs.push(d)
        return json(201, { id: d.id, name: d.name })
      }
      const m = /^\/documents\/([^/]+)\/(open|close|take-over|save|recover|discard-autosave|tabs)(?:\/([^/]+)(?:\/(outputs|rename))?)?$/.exec(path)
      if (!m) return json(404, { error: 'not found' })
      const [, id, verb, tabId, sub] = m
      const d = this.doc(id!)
      if (verb === 'open') {
        const other = this.docs.find((x) => x.open && x.id !== id)
        if (other) return json(409, { error: `another Document is open: '${other.name}'`, open: { id: other.id, name: other.name } })
        d.open = true
        return json(200, this.reply(d))
      }
      if (verb === 'close') {
        d.open = false
        return json(200, { closed: true })
      }
      if (verb === 'recover') {
        const a = d.autosave
        if (!a) return json(404, { error: 'there is no autosave to recover' })
        for (const t of d.tabs) if (a.tabs.some((x) => x.id === t.id)) t.dirty = true
        d.autosave = null
        return json(200, { recovered: a.tabs.map((t) => t.id), moved_on: a.moved_on, list_moved_on: a.list_moved_on, skipped: [], unreadable: 0, tabs: d.tabs })
      }
      if (verb === 'discard-autosave') {
        if (!d.autosave) return json(404, { error: 'there is no autosave to discard' })
        d.autosave = null
        return json(200, { discarded: 1 })
      }
      if (verb === 'take-over') {
        d.holder = null
        return json(200, { read_only: false, holder: null })
      }
      if (verb === 'save') {
        if (d.holder && d.holder.program !== 'browser') return json(423, { error: 'locked', document: d.id, holder: d.holder })
        for (const t of d.tabs) t.dirty = false
        return json(200, { saved: d.tabs.map((t) => t.id) })
      }
      // tabs
      if (!tabId) {
        if (method === 'GET') return json(200, this.reply(d))
        const kind = (req.postDataJSON() as { kind: string }).kind
        const n = d.tabs.filter((t) => t.kind === kind).length + 1
        const t = tab(`T${d.tabs.length + 1}`, kind, `${kind === 'studio' ? 'Part Studio' : 'Assembly'} ${n}`, true)
        d.tabs.push(t)
        return json(201, { document: d.id, tab: t, tabs: d.tabs })
      }
      if (sub === 'outputs') return json(404, { error: 'not found' })
      const t = d.tabs.find((x) => x.id === tabId)
      if (!t) return json(404, { error: 'not found' })
      if (sub === 'rename') {
        t.name = (req.postDataJSON() as { name: string }).name
        t.dirty = true
        return json(200, { document: d.id, tab: t, tabs: d.tabs })
      }
      if (method === 'DELETE') {
        const linking = d.tabs.find((x) => x.id === this.links[t.id])
        if (linking) return json(409, { error: `${t.name} is used by ${linking.name}`, document: d.id, tab: t.id, linked_by: [{ id: linking.id, name: linking.name }] })
        d.tabs = d.tabs.filter((x) => x !== t)
        return json(200, { document: d.id, deleted: t.id, tabs: d.tabs })
      }
      return json(404, { error: 'not found' })
    })
    await page.routeWebSocket(/\/ws\?client=/, (ws) => {
      this.sockets.push(ws)
      ws.send(JSON.stringify({ type: 'hello', client: 'x', document: null, tabs: [] }))
      ws.onMessage((raw) => {
        const msg = JSON.parse(String(raw)) as { type: string; t?: number; document?: string }
        if (msg.type === 'ping') ws.send(JSON.stringify({ type: 'pong', t: msg.t }))
        else {
          this.log.push(`ws ${msg.type}${msg.document ? ` ${msg.document}` : ''}`)
          if (msg.type === 'subscribe' && msg.document) {
            const d = this.doc(msg.document)
            ws.send(JSON.stringify({ type: 'hello', client: 'x', ...this.reply(d) }))
          }
        }
      })
    })
  }
}

/** A single-Document server, as the preview starts it: one Document, already open. */
export const singleDocument = (name = 'Bracket'): Scripted =>
  new Scripted([{ id: 'd', name, modified: Date.now() / 1000 - 600, holder: null, open: true, missing: false, tabs: [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'assembly', 'Assembly 1')] }])
