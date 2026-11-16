import type { DocumentRow, Holder } from '../net/api'

const plural = (n: number, unit: string): string => `${n} ${unit}${n === 1 ? '' : 's'} ago`

/** "3 hours ago", or the date once it is a week old. `time` is seconds since the epoch. */
export function formatAgo(time: number, nowMs: number): string {
  const s = nowMs / 1000 - time
  if (s < 45) return 'just now'
  if (s < 3600) return plural(Math.max(1, Math.round(s / 60)), 'minute')
  if (s < 86400) return plural(Math.round(s / 3600), 'hour')
  if (s < 2 * 86400) return 'yesterday'
  if (s < 7 * 86400) return plural(Math.floor(s / 86400), 'day')
  return new Date(time * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

/** "Modified 3 hours ago", or the date once it is a week old. `modified` is seconds since the epoch. */
export function formatModified(modified: number | null, nowMs: number): string {
  return modified === null ? 'Modified time unknown' : `Modified ${formatAgo(modified, nowMs)}`
}

export interface RowStatus {
  label: string
  /** `accent`: open here; `held`: someone else has the lock, so it opens read-only; `bad`: cannot be opened. */
  tone: 'accent' | 'held' | 'bad'
  /** The Document opens read-only here. */
  readOnly: boolean
}

const foreign = (h: Holder | null): h is Holder => h !== null && h.program !== 'browser'

/** What the home screen says about who holds a Document, or null when nobody does and it is closed. */
export function rowStatus(row: DocumentRow): RowStatus | null {
  if (row.missing) return { label: 'Folder missing', tone: 'bad', readOnly: false }
  const h = row.holder
  const stale = h?.stale ? ', not responding' : ''
  if (foreign(h)) {
    const who = h.program === 'desktop' ? 'the desktop' : h.program
    return { label: `Open in ${who}${stale}`, tone: 'held', readOnly: true }
  }
  if (row.open) return { label: 'Open', tone: 'accent', readOnly: false }
  if (h) return { label: `Open in another browser client${stale}`, tone: 'held', readOnly: true }
  return null
}

/** The marker for a Document with unsaved edits the server kept, or null. */
export function recoverMark(row: DocumentRow): string | null {
  return row.autosave && !row.missing ? 'Unsaved edits can be recovered' : null
}
