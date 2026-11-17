import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { BINDINGS } from '../input/bindings'
import type { Binding } from '../input/bindings'
import { chordText, Shortcuts, shortcutGroups } from './Shortcuts'

afterEach(cleanup)

/** What the list must hold, from the data alone: each binding's chord and label under every workspace it works in. */
const expected = (bindings: readonly Binding[]): string[] =>
  bindings.flatMap((b) => b.where.map((w) => `${w}|${b.chord}|${b.label}`)).sort()
const listed = (bindings: readonly Binding[]): string[] =>
  shortcutGroups(bindings)
    .flatMap((g) => g.rows.flatMap((r) => r.chords.map((c) => `${g.workspace}|${c}|${r.label}`)))
    .sort()

describe('the shortcut list', () => {
  it('holds exactly the bindings of the binding data, under each workspace they work in', () => {
    expect(listed(BINDINGS)).toEqual(expected(BINDINGS))
  })

  it('shows a new binding with no edit to the list', () => {
    const added: Binding = { chord: 'Ctrl+Alt+J', command: 'test.new', label: 'A brand new key', where: ['studio', 'assembly'] }
    const before = listed(BINDINGS)
    const after = listed([...BINDINGS, added])
    expect(after).toEqual(expected([...BINDINGS, added]))
    expect(after.filter((l) => !before.includes(l))).toEqual(['assembly|Ctrl+Alt+J|A brand new key', 'studio|Ctrl+Alt+J|A brand new key'])
    // and in the drawn dialog: the key is in the groups it names, not in the others
    // (the dialog reads BINDINGS itself; the pure function above is what it draws from)
  })

  it('lists P as Point in a sketch and Show planes in a Part Studio, and Undo under Assembly', () => {
    const groups = Object.fromEntries(shortcutGroups().map((g) => [g.workspace, g.rows]))
    expect(groups['sketch']!.find((r) => r.chords.includes('P'))!.label).toBe('Point')
    expect(groups['studio']!.find((r) => r.chords.includes('P'))!.label).toBe('Show planes')
    expect(groups['assembly']!.find((r) => r.chords.includes('Ctrl+Z'))!.label).toBe('Undo')
    expect(groups['assembly']!.find((r) => r.chords.includes('P'))).toBeUndefined()
    expect(groups['studio']!.find((r) => r.label === 'Redo')!.chords).toEqual(['Ctrl+Y', 'Ctrl+Shift+Z'])
  })

  it('lists the search chords, and flags a key that is shown but runs nothing', () => {
    const studio = shortcutGroups().find((g) => g.workspace === 'studio')!
    expect(studio.rows.find((r) => r.label === 'Search tools')!.chords).toEqual(['Ctrl+Space', 'Alt+C', 'Alt+`'])
    expect(studio.rows.find((r) => r.label === 'Quit')!.shownOnly).toBe(true)
    expect(studio.rows.find((r) => r.label === 'Save')!.shownOnly).toBe(false)
  })

  it('draws every chord of the data in the dialog', () => {
    render(<Shortcuts onClose={() => {}} />)
    const dialog = screen.getByRole('dialog', { name: 'Keyboard shortcuts' })
    for (const g of shortcutGroups()) {
      const section = within(within(dialog).getByRole('region', { name: g.title }))
      for (const r of g.rows) for (const c of r.chords) expect(section.getAllByText(chordText(c)).length, `${g.title} ${c}`).toBeGreaterThan(0)
    }
    expect(dialog.querySelectorAll('[data-workspace="sketch"] [data-chord="P"]').length).toBe(1)
  })
})
