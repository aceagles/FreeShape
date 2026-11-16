import { request, tabPath } from '../net/api'

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

/** Where an export goes. I11.14 adds a server folder ({ kind: 'root', root }) beside the download. */
export type ExportDestination = { kind: 'download' }

export interface ExportResult {
  /** The file's name, as the server gave it. */
  name: string
  bytes: number
  /** Names of surface bodies an STL left out. */
  skipped: string[]
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
 * Exports a tab as it is now (the route GET .../tabs/<tab>/export, I11.3) to `to`. The one place an export is requested:
 * a download hands the file to the browser, and the result carries the surface bodies the STL left out. A refusal is an ApiError.
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
  }
}
