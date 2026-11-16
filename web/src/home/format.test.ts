import { expect, it } from 'vitest'
import type { DocumentRow } from '../net/api'
import { formatModified, rowStatus } from './format'

const NOW = Date.UTC(2026, 10, 16, 12, 0, 0)
const ago = (s: number) => NOW / 1000 - s

it('says how long ago a Document changed, and gives the date after a week', () => {
  expect(formatModified(ago(10), NOW)).toBe('Modified just now')
  expect(formatModified(ago(60), NOW)).toBe('Modified 1 minute ago')
  expect(formatModified(ago(3 * 3600), NOW)).toBe('Modified 3 hours ago')
  expect(formatModified(ago(30 * 3600), NOW)).toBe('Modified yesterday')
  expect(formatModified(ago(4 * 86400), NOW)).toBe('Modified 4 days ago')
  expect(formatModified(ago(30 * 86400), NOW)).toBe('Modified 17 Oct 2026')
  expect(formatModified(null, NOW)).toBe('Modified time unknown')
})

const row = (extra: Partial<DocumentRow>): DocumentRow => ({ id: 'a', name: 'A', modified: 1, holder: null, open: false, missing: false, ...extra })

it('names who holds a Document', () => {
  expect(rowStatus(row({}))).toBeNull()
  expect(rowStatus(row({ open: true }))).toMatchObject({ label: 'Open', tone: 'accent' })
  expect(rowStatus(row({ holder: { program: 'desktop', instance: 'i', heartbeat: 1, stale: false } }))).toMatchObject({ label: 'Open in the desktop', readOnly: true })
  expect(rowStatus(row({ holder: { program: 'desktop', instance: 'i', heartbeat: 1, stale: true } }))?.label).toBe('Open in the desktop, not responding')
  expect(rowStatus(row({ open: true, holder: { program: 'browser', instance: 'i', heartbeat: 1, stale: false } }))?.label).toBe('Open')
  expect(rowStatus(row({ missing: true }))).toMatchObject({ tone: 'bad' })
})
