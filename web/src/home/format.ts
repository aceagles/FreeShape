import type { DocumentRow, Holder } from '../net/api'

const plural = (n: number, unit: string): string => `${n} ${unit}${n === 1 ? '' : 's'} ago`

/** "Modified 3 hours ago", or the date once it is a week old. `modified` is seconds since the epoch. */
export function formatModified(modified: number | null, nowMs: number): string {
  if (modified === null) return 'Modified time unknown'
  const s = nowMs / 1000 - modified
  if (s < 45) return 'Modified just now'
  if (s < 3600) return `Modified ${plural(Math.max(1, Math.round(s / 60)), 'minute')}`
  if (s < 86400) return `Modified ${plural(Math.round(s / 3600), 'hour')}`
  if (s < 2 * 86400) return 'Modified yesterday'
  if (s < 7 * 86400) return `Modified ${plural(Math.floor(s / 86400), 'day')}`
  return `Modified ${new Date(modified * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}`
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
