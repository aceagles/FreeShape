import { ApiError, json, request, tabPath } from '../net/api'
import type { TabInfo } from '../net/connection'
import { UploadTooLarge, uploadFile } from '../net/upload'

/** The STL resolution presets of the desktop's STL dialog (freeshape/export_options.py PRESETS), then Custom. */
export type ExportPreset = 'coarse' | 'medium' | 'fine' | 'custom'

export const EXPORT_PRESETS: ReadonlyArray<readonly [ExportPreset, string]> = [
  ['coarse', 'Coarse'],
  ['medium', 'Medium'],
  ['fine', 'Fine'],
  ['custom', 'Custom'],
]

/** What each named preset stands for, to start a Custom resolution from: deviation in mm, angle in degrees (the server holds radians: 0.5, 0.4, 0.3). */
export const PRESET_VALUES: Readonly<Record<Exclude<ExportPreset, 'custom'>, { defl: number; angle: number }>> = {
  coarse: { defl: 0.2, angle: (0.5 * 180) / Math.PI },
  medium: { defl: 0.1, angle: (0.4 * 180) / Math.PI },
  fine: { defl: 0.05, angle: (0.3 * 180) / Math.PI },
}

/** The limits the server holds a Custom resolution to (export_options.py DEFL_RANGE and ANGLE_RANGE; the angle's is in radians there). */
export const DEFL_RANGE_MM = [0.001, 10] as const
export const ANGLE_RANGE_DEG = [(0.01 * 180) / Math.PI, (1.5 * 180) / Math.PI] as const

export type ExportFormat = 'step' | 'stl'

/** One export request: what the GET route's query takes. `defl` is in mm, `angleDeg` in degrees (the route takes radians). */
export interface ExportOptions {
  format: ExportFormat
  /** Part ids of a Part Studio; absent or empty is every part. */
  parts?: readonly string[]
  /** STL only. */
  preset?: ExportPreset
  defl?: number
  angleDeg?: number
  /** STL only: ASCII, else binary. */
  ascii?: boolean
}

/** An export root the server lists (GET /files, I11.13): a folder an export can be saved into. */
export interface ExportRoot {
  name: string
  max_bytes: number
}

/** A read-only file root the server lists (GET /files, I11.2): `files` is how many STEP files its index holds, `max_upload` the largest the server imports. */
export interface FileRoot {
  name: string
  files: number
  max_upload: number
}

/** The reply of GET /files: the read-only roots to browse and the export roots to save into. */
export interface FilesReply {
  roots: FileRoot[]
  export_roots: ExportRoot[]
}

/** The one GET /files: both the explorer (roots) and the Export dialog (export roots) read their part of it. A server that cannot say gives an error. */
export async function fetchFiles(): Promise<FilesReply> {
  const r = await json<{ roots?: FileRoot[]; export_roots?: ExportRoot[] }>('GET', '/files')
  return {
    roots: Array.isArray(r.roots) ? r.roots.filter((x) => typeof x?.name === 'string') : [],
    export_roots: Array.isArray(r.export_roots) ? r.export_roots.filter((x) => typeof x?.name === 'string') : [],
  }
}

/** The export roots the server was started with; none when it has none or cannot be asked. */
export async function fetchExportRoots(): Promise<ExportRoot[]> {
  try {
    return (await fetchFiles()).export_roots
  } catch {
    return []
  }
}

/** A sub-folder of a file root's folder (`path` is what `dir` takes). */
export interface FolderRef {
  name: string
  path: string
  files: number
}

/** One STEP file of a root, as the root's index and manifest.csv give it: `licence`, `authors` and `url` are null when the manifest says nothing. */
export interface FileEntry {
  id: string
  name: string
  file: string
  path: string
  size: number
  description: string
  tags: string[]
  licence: string | null
  authors: string | null
  url: string | null
}

/** The reply of GET /files/<root>: one folder's sub-folders and one page of its entries, or the page of a search. */
export interface FolderReply {
  root: string
  dir: string
  q: string
  dirs: FolderRef[]
  entries: FileEntry[]
  total: number
  offset: number
  limit: number
}

/** How many entries the explorer asks for at a time (the server's default page). */
export const PAGE_SIZE = 200

/** One folder of a root: `dir` ('' is the root's top), a search `q` (below `dir`), and the page from `offset`. */
export function fetchFolder(root: string, o: { dir?: string; q?: string; offset?: number; limit?: number; signal?: AbortSignal } = {}): Promise<FolderReply> {
  const q = new URLSearchParams()
  if (o.dir !== undefined && o.dir !== '') q.set('dir', o.dir)
  if (o.q !== undefined && o.q !== '') q.set('q', o.q)
  if (o.offset !== undefined && o.offset > 0) q.set('offset', String(o.offset))
  q.set('limit', String(o.limit ?? PAGE_SIZE))
  return json<FolderReply>('GET', `/files/${encodeURIComponent(root)}?${q.toString()}`, { signal: o.signal })
}

/** What the import route answers (I11.1): the tab the file went into, the feature made (`error` is why it did not build) and the tab list. */
export interface ImportReply {
  document: string
  tab: { id: string; name: string }
  feature: { id: string; name: string; error: string | null } | null
  parts: Array<{ id: string; name: string }>
  tabs: TabInfo[]
}

/**
 * Imports a file root's entry (I11.2): the body is {root, entry} and nothing else, as the server takes it. `newTab` makes a new
 * Part Studio named after the file (new_tab=1) instead of adding to `tab`. A refusal is an ApiError (404 gone, 409 changed on disk,
 * 413 too big, 415 not STEP or BREP, 423 read-only).
 */
export function importEntry(doc: string, tab: string, root: string, entry: string, newTab: boolean): Promise<ImportReply> {
  return json<ImportReply>('POST', `${tabPath(doc, tab)}/import${newTab ? '?new_tab=1' : ''}`, { body: { root, entry } })
}

/** The extensions the import takes, as the desktop's drop does (freeshape/ui/home.py IMPORT_EXTS): STEP, then BREP. */
export const IMPORT_EXTENSIONS: readonly string[] = ['.step', '.stp', '.brep', '.brp']

/** The picker's `accept` for a device import. */
export const IMPORT_ACCEPT = IMPORT_EXTENSIONS.join(',')

/** Whether a file's name ends in one of the import's extensions (the server checks the bytes). */
export const isImportName = (name: string): boolean => IMPORT_EXTENSIONS.some((x) => name.toLowerCase().endsWith(x))

/**
 * The largest file the import route takes, as GET /files says it per root (every root gets the one server limit). A device upload
 * has no root, so this is the smallest `max_upload` listed; null when the server lists no root, then the server's 413 is the check.
 */
export function uploadLimitOf(roots: readonly FileRoot[]): number | null {
  const sizes = roots.map((r) => r.max_upload).filter((n) => typeof n === 'number' && n > 0)
  return sizes.length === 0 ? null : Math.min(...sizes)
}

/** `uploadLimitOf` for the server's roots, asked now; null when it cannot be asked. */
export async function fetchUploadLimit(): Promise<number | null> {
  try {
    return uploadLimitOf((await fetchFiles()).roots)
  } catch {
    return null
  }
}

/**
 * Imports a file from the device (I11.8): the route of I11.1 with the bytes as application/octet-stream and the name in `?name=`.
 * The reply is the one `importEntry` gets. Refusals are an ApiError with the server's text (415 not STEP or BREP, 413 too big, 423
 * read-only), a file over `limit` is an UploadTooLarge before any request, and an abort is an AbortError.
 */
export async function importDevice(
  doc: string,
  tab: string,
  file: File,
  newTab: boolean,
  o: { limit?: number | null; onProgress?: (sent: number, total: number) => void; signal?: AbortSignal } = {},
): Promise<ImportReply> {
  return (await uploadFile(file, {
    url: `${tabPath(doc, tab)}/import`,
    query: { name: file.name, ...(newTab ? { new_tab: '1' } : {}) },
    ...o,
  })) as ImportReply
}

/** What to tell the person about a device import that did not go through. */
export function importFailure(e: unknown, fallback = 'The import failed.'): string {
  if (e instanceof UploadTooLarge) return `${e.fileName} is ${(e.size / 1e6).toFixed(1)} MB; the server takes files up to ${(e.limit / 1e6).toFixed(0)} MB.`
  return e instanceof Error ? e.message : fallback
}

/** Where an export goes: the browser's download, or a file saved into a server folder (`replace` lets it take the place of one of that name). */
export type ExportDestination = { kind: 'download' } | { kind: 'root'; root: string; name: string; replace?: boolean }

export interface ExportResult {
  /** The file's name, as the server gave it. */
  name: string
  bytes: number
  /** Names of surface bodies an STL left out. */
  skipped: string[]
  /** The export root it was saved into; absent for a download. */
  root?: string
}

/** The server's refusal of a save because the name is taken (409 `exists`), with the name it is taken under; else null. */
export function existingName(e: unknown): string | null {
  if (!(e instanceof ApiError) || e.status !== 409 || e.body.error !== 'exists') return null
  return typeof e.body.name === 'string' ? e.body.name : ''
}

const rad = (deg: number): number => Math.round(((deg * Math.PI) / 180) * 1e5) / 1e5

/** The query of the export route for these options. */
export function exportQuery(o: ExportOptions): string {
  const q = [`format=${o.format}`]
  if (o.parts !== undefined && o.parts.length > 0) q.push(`parts=${o.parts.map(encodeURIComponent).join(',')}`)
  if (o.format === 'stl') {
    const preset = o.preset ?? 'fine'
    q.push(`preset=${preset}`)
    if (preset === 'custom') {
      if (o.defl !== undefined) q.push(`defl=${o.defl}`)
      if (o.angleDeg !== undefined) q.push(`angle=${rad(o.angleDeg)}`)
    }
    q.push(`ascii=${o.ascii === true ? 1 : 0}`)
  }
  return q.join('&')
}

/** The body of the save route (POST .../export, I11.13) for these options. */
function saveBody(o: ExportOptions, to: { root: string; name: string; replace?: boolean }): Record<string, unknown> {
  const body: Record<string, unknown> = { root: to.root, name: to.name, format: o.format }
  if (o.parts !== undefined && o.parts.length > 0) body.parts = [...o.parts]
  if (o.format === 'stl') {
    const preset = o.preset ?? 'fine'
    body.preset = preset
    if (preset === 'custom') {
      if (o.defl !== undefined) body.defl = o.defl
      if (o.angleDeg !== undefined) body.angle = rad(o.angleDeg)
    }
    body.ascii = o.ascii === true
  }
  if (to.replace === true) body.replace = true
  return body
}

/** The file name a Content-Disposition header gives (`filename*` first, then `filename`), or null. */
export function dispositionName(header: string | null): string | null {
  if (header === null) return null
  const star = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(header)
  if (star !== null) {
    try {
      return decodeURIComponent(star[1]!.trim())
    } catch {
      // fall through to the plain name
    }
  }
  const plain = /filename\s*=\s*"((?:[^"\\]|\\.)*)"/.exec(header) ?? /filename\s*=\s*([^;]+)/.exec(header)
  return plain === null ? null : plain[1]!.replace(/\\(.)/g, '$1').trim()
}

/** The skipped surface bodies of the X-FreeShape-Skipped header: percent-encoded names, comma-separated, empty when none. */
export function skippedNames(header: string | null): string[] {
  if (header === null || header.trim() === '') return []
  return header.split(',').flatMap((n) => {
    try {
      const name = decodeURIComponent(n.trim())
      return name === '' ? [] : [name]
    } catch {
      return n.trim() === '' ? [] : [n.trim()]
    }
  })
}

/** Hands `blob` to the browser to save, through a hidden link (on the iPad, Safari offers the file to Files). */
export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.hidden = true
  document.body.append(link)
  link.click()
  link.remove()
  // the browser has started the save by now; the address goes a little later, so a slow save keeps its file
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/**
 * Exports a tab as it is now to `to`. The one place an export is requested: a download (GET .../tabs/<tab>/export, I11.3)
 * hands the file to the browser; a root (POST the same path, I11.13) saves it in that server folder under the name the server
 * returns. The result carries the surface bodies the STL left out. A refusal is an ApiError (see `existingName`).
 */
export async function exportTab(doc: string, tab: string, options: ExportOptions, to: ExportDestination = { kind: 'download' }): Promise<ExportResult> {
  switch (to.kind) {
    case 'download': {
      const res = await request('GET', `${tabPath(doc, tab)}/export?${exportQuery(options)}`)
      const blob = await res.blob()
      const name = dispositionName(res.headers.get('Content-Disposition')) ?? `export.${options.format}`
      saveBlob(blob, name)
      return { name, bytes: blob.size, skipped: skippedNames(res.headers.get('X-FreeShape-Skipped')) }
    }
    case 'root': {
      const res = await request('POST', `${tabPath(doc, tab)}/export`, { body: saveBody(options, to) })
      const r = (await res.json()) as { root?: string; name?: string; bytes?: number; skipped?: string[] }
      return { name: r.name ?? to.name, bytes: r.bytes ?? 0, skipped: Array.isArray(r.skipped) ? r.skipped : [], root: r.root ?? to.root }
    }
  }
}
