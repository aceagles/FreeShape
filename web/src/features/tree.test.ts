import { describe, expect, it } from 'vitest'
import { fixture } from '../test/fixtures'
import { barPosition, buildRows, nearestSlot, slotBefore } from './tree'
import { featureIcon, GENERIC_ICON, hasOwnIcon } from './featureIcons'

const tree = fixture('tree').outputs

describe('buildRows', () => {
  it('draws a folder header above its first member and its members after it when it is open', () => {
    const rows = buildRows(tree, {})
    expect(rows.map((r) => (r.kind === 'folder' ? `folder ${r.folder.id}` : r.feature.id))).toEqual(['F1', 'F2', 'F3', 'folder D1', 'F4', 'F5', 'F9', 'F6', 'F7', 'F8'])
  })

  it('hides the members of a folder the person closed, and the file wins when they made no choice', () => {
    const closed = buildRows(tree, { D1: false })
    expect(closed.map((r) => (r.kind === 'folder' ? 'folder' : r.feature.id))).toEqual(['F1', 'F2', 'F3', 'folder', 'F9', 'F6', 'F7', 'F8'])
    const shut = { ...tree, folders: tree.folders.map((f) => ({ ...f, open: false })) }
    expect(buildRows(shut, {}).filter((r) => r.kind === 'feature').length).toBe(7)
    expect(buildRows(shut, { D1: true }).filter((r) => r.kind === 'feature').length).toBe(9)
  })

  it('keeps a folder open when the rollback bar lies inside it', () => {
    const shut = { ...tree, folders: tree.folders.map((f) => ({ ...f, open: false })), rollback: 4 }
    // F4 is the first member (index 3), F5 the second (index 4): a bar at 4 is between them
    expect(buildRows(shut, { D1: false }).filter((r) => r.kind === 'feature').length).toBe(9)
    expect(buildRows({ ...shut, rollback: 3 }, { D1: false }).filter((r) => r.kind === 'feature').length).toBe(7)
  })
})

describe('where the bar sits', () => {
  it('is before the folder header, not between the header and its first member', () => {
    const rows = buildRows({ ...tree, rollback: 3 }, {})
    expect(barPosition(rows, 3, 9)).toBe(3)
    expect(rows[3]?.kind).toBe('folder')
    expect(slotBefore(rows[4]!, rows, 4)).toBeNull()
    expect(slotBefore(rows[5]!, rows, 5)).toBe(4)
  })

  it('is at the end of the list when nothing is rolled back', () => {
    const rows = buildRows(tree, {})
    expect(barPosition(rows, 9, 9)).toBe(rows.length)
    expect(barPosition(buildRows(tree, {}), 7, 9)).toBe(rows.findIndex((r) => r.kind === 'feature' && r.index === 7))
  })

  it('takes the slot whose line is nearest', () => {
    const lines = [
      { slot: 0, y: 0 },
      { slot: 1, y: 44 },
      { slot: 2, y: 88 },
    ]
    expect(nearestSlot(lines, 50)).toBe(1)
    expect(nearestSlot(lines, 70)).toBe(2)
    expect(nearestSlot(lines, -300)).toBe(0)
  })
})

describe('the icon table', () => {
  it('gives the ten layout tools their own icons and everything else the generic one', () => {
    for (const t of ['sketch', 'extrude', 'revolve', 'fillet', 'chamfer', 'shell', 'hole', 'plane', 'mirror', 'linear_pattern', 'circular_pattern']) expect(hasOwnIcon(t)).toBe(true)
    expect(featureIcon('linear_pattern')).toBe(featureIcon('circular_pattern'))
    for (const t of ['axis', 'loft', 'sm_flange', 'plugin:acme', 'toString', '__proto__']) expect(featureIcon(t)).toBe(GENERIC_ICON)
    expect(new Set(['sketch', 'extrude', 'revolve', 'fillet', 'chamfer', 'shell', 'hole', 'plane', 'mirror'].map(featureIcon)).size).toBe(9)
  })
})
