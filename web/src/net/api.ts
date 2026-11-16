import type { TabInfo } from './connection'

/** Who holds a Document's lock, as the server's routes and socket messages report it. */
export interface Holder {
  program: string
  instance: string
  heartbeat: number
  stale: boolean
}

/** What a row of GET /documents says about an autosave the server kept: when the newest was written, and how many there are. */
export interface AutosaveMark {
  time: number
  count: number
}

/** One row of GET /documents. `modified` is seconds since the epoch, or null when the folder cannot be read. */
export interface DocumentRow {
  id: string
  name: string
  modified: number | null
  holder: Holder | null
  open: boolean
  missing: boolean
  /** Unsaved edits the server kept that nobody holds the lock of: they can be recovered. Absent on an older server. */
  autosave?: AutosaveMark | null
}

/**
 * What recovering an autosave would do, from the open call. `tabs` are the tabs it holds, `deleted` the tab ids it deletes,
 * `moved_on` the tabs whose saved file changed since it was written (recovering replaces that saved work in the open
 * Document), `list_moved_on` that the tab list was saved by someone else too. `writable` is false when another program
 * holds the lock, so it cannot be recovered or discarded yet.
 */
export interface AutosaveOffer {
  time: number
  count: number
  tabs: Array<{ id: string; name: string }>
  deleted: string[]
  moved_on: string[]
  list_moved_on: boolean
  writable: boolean
}

/** What opening a Document, or asking for its tabs, answers. */
export interface DocumentReply {
  document: { id: string; name: string }
  tabs: TabInfo[]
  read_only: boolean
  holder: Holder | null
  /** A recoverable autosave of this Document, or null. */
  autosave?: AutosaveOffer | null
}

/** What POST .../recover answers: the tabs now in the open Document (all unsaved), and what could not be put back. */
export interface RecoverReply {
  recovered: string[]
  moved_on: string[]
  list_moved_on: boolean
  skipped: Array<{ tab: string; why: string }>
  unreadable: number
  tabs: TabInfo[]
}

/** A server answer that was not a success: `status` is 0 when the server could not be reached at all. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }
  /** The Document that is already open, from a 409 on open. */
  get openDocument(): { id: string; name: string } | null {
    const o = this.body.open
    if (typeof o !== 'object' || o === null) return null
    const { id, name } = o as { id?: unknown; name?: unknown }
    return typeof id === 'string' && typeof name === 'string' ? { id, name } : null
  }
  /** The tabs that link to a tab, from a 409 on delete. */
  get linkedBy(): Array<{ id: string; name: string }> {
    const l = this.body.linked_by
    if (!Array.isArray(l)) return []
    return l.filter((x): x is { id: string; name: string } => typeof x?.id === 'string' && typeof x?.name === 'string')
  }
}

export const seg = encodeURIComponent
export const docPath = (doc: string): string => `/documents/${seg(doc)}`
export const tabPath = (doc: string, tab: string): string => `${docPath(doc)}/tabs/${seg(tab)}`

export async function request(method: string, path: string, init: { body?: unknown; headers?: Record<string, string>; signal?: AbortSignal } = {}): Promise<Response> {
  let res: Response
  try {
    res = await fetch(path, {
      method,
      headers: { ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: init.signal,
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, 'The server did not answer. Check the connection and try again.')
  }
  if (res.ok) return res
  let body: Record<string, unknown> = {}
  try {
    const parsed: unknown = await res.json()
    if (typeof parsed === 'object' && parsed !== null) body = parsed as Record<string, unknown>
  } catch {
    // not JSON: the status text below has to do
  }
  throw new ApiError(res.status, typeof body.error === 'string' ? body.error : `The server answered ${res.status}.`, body)
}

export async function json<T>(method: string, path: string, init?: Parameters<typeof request>[2]): Promise<T> {
  return (await (await request(method, path, init)).json()) as T
}

export async function listDocuments(signal?: AbortSignal): Promise<DocumentRow[]> {
  const out = await json<{ documents: DocumentRow[] }>('GET', '/documents', { signal })
  return out.documents
}

export const createDocument = (name: string) => json<{ id: string; name: string }>('POST', '/documents', { body: { name } })
export const openDocument = (doc: string) => json<DocumentReply>('POST', `${docPath(doc)}/open`, { body: {} })
/** Close a Document. Unsaved changes are kept as an autosave, or removed with `discard` (the user chose Discard). */
export const closeDocument = (doc: string, discard = false) =>
  json<{ closed: boolean }>('POST', `${docPath(doc)}/close`, { body: discard ? { discard: true } : {} })
export const recoverDocument = (doc: string) => json<RecoverReply>('POST', `${docPath(doc)}/recover`, { body: {} })
export const discardAutosave = (doc: string) => json<{ discarded: number }>('POST', `${docPath(doc)}/discard-autosave`, { body: {} })
export const takeOver = (doc: string) => json<{ read_only: boolean; holder: Holder | null }>('POST', `${docPath(doc)}/take-over`, { body: {} })
export const fetchDocumentTabs = (doc: string) => json<DocumentReply>('GET', `${docPath(doc)}/tabs`)

export const addTab = (doc: string, kind: 'studio' | 'assembly', name?: string) =>
  json<{ tab: TabInfo; tabs: TabInfo[] }>('POST', `${docPath(doc)}/tabs`, { body: name === undefined ? { kind } : { kind, name } })
export const renameTab = (doc: string, tab: string, name: string) =>
  json<{ tab: TabInfo; tabs: TabInfo[] }>('POST', `${tabPath(doc, tab)}/rename`, { body: { name } })
export const deleteTab = (doc: string, tab: string) => json<{ deleted: string; tabs: TabInfo[] }>('DELETE', tabPath(doc, tab))

export const saveDocument = (doc: string, client: string) =>
  json<{ saved: string[] }>('POST', `${docPath(doc)}/save`, { body: {}, headers: { 'X-Client-Id': client } })

/** One edit of a tab, with the client that makes it and the revision it was based on. */
export const postEdit = (doc: string, tab: string, edit: Record<string, unknown>, client: string, base: number | null) =>
  json<{ rebuilt: string[] }>('POST', `${tabPath(doc, tab)}/edit`, {
    body: edit,
    headers: { 'X-Client-Id': client, ...(base === null ? {} : { 'X-Base-Revision': String(base) }) },
  })

/** The tab's outputs in the engine wire format. */
export async function fetchOutputsBytes(doc: string, tab: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  return (await request('GET', `${tabPath(doc, tab)}/outputs`, { signal })).arrayBuffer()
}

/** One mate type of GET .../assembly/types: its id, label and help (freeshape/assembly.py MATE_TYPES) and the names of its Mate values (MATE_VALUE_DOFS). */
export interface MateTypeInfo {
  id: string
  label: string
  help: string
  freedoms: string[]
}
/** One relation type of GET .../assembly/types (RELATION_TYPES). */
export interface RelationTypeInfo {
  id: string
  label: string
  help: string
}
export interface AssemblyTypesReply {
  document: string
  tab: string
  mates: MateTypeInfo[]
  relations: RelationTypeInfo[]
}
/** The mate and relation types of an Assembly tab, in the order the toolbar shows them. A query: no client id. */
export const fetchAssemblyTypes = (doc: string, tab: string, signal?: AbortSignal) => json<AssemblyTypesReply>('GET', `${tabPath(doc, tab)}/assembly/types`, { signal })
