import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { MODE_KEY } from '../shell/mode'
import { fixture } from '../test/fixtures'
import { makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import type { FieldSpec, OpenReply } from './dialogApi'

// The Derived feature's dialog (card I10.2) in the whole Shell, against a scripted server: the rows are the server's
// description of the session's Document (freeshape/dialog_schema.py `derived_describe`), every one a standard kind.
vi.mock('three', async (orig) => {
  const three = await orig<typeof import('three')>()
  class FakeRenderer {
    setClearColor() {}
    setPixelRatio() {}
    setSize() {}
    render() {}
    dispose() {}
  }
  return { ...three, WebGLRenderer: FakeRenderer }
})

let mode = 'tablet'
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  window.localStorage.clear()
  window.localStorage.setItem(MODE_KEY, mode)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
  mode = 'tablet'
})

const row = (key: string, kind: string, label: string, extra: Partial<FieldSpec> = {}): FieldSpec => ({ key, kind, standard: true, visible: true, label, ...extra })
const DESKTOP_ONLY = 'Desktop only for now'

/** What the server describes for a Derived of the bracket's Part Studio with its part ticked. */
function derived(ticked: { parts: string[]; connectors: string[] }, location = ''): OpenReply {
  const fields: FieldSpec[] = [
    row('document', 'choice', 'Document', {
      options: [
        ['this', 'This Document'],
        ['other', 'Another Document'],
        ['version', 'A Version of this Document'],
      ],
      disabled: { other: DESKTOP_ONLY, version: DESKTOP_ONLY },
    }),
    row('source', 'choice', 'Part Studio', {
      options: [
        ['', '— choose a Part Studio —'],
        ['T2', 'Bracket studio'],
      ],
      picker: 'studio',
    }),
    { key: '_note', kind: 'info', standard: true, visible: true, text: '' },
    row('parts', 'ticks', 'Parts', { options: [['F2/1', 'Bracket']], group: 'derived_tabs' }),
    row('sketches', 'ticks', 'Sketches', { options: [['F1', 'Bracket profile']], group: 'derived_tabs' }),
    row('connectors', 'ticks', 'Mate connectors', { options: [['F9', 'Pin  (Bracket)']], group: 'derived_tabs' }),
    row('location', 'choice', 'Location', {
      options: [
        ['', '— not placed —'],
        ['F3', 'Here'],
      ],
    }),
    row('base', 'choice', 'Placement base', {
      visible: location !== '',
      options: [['origin', 'Base origin'], ...ticked.connectors.map((c): [string, string] => [c, 'Pin  (Bracket)'])],
    }),
  ]
  return {
    dialog: 'did1',
    document: 'd',
    tab: 'T1',
    feature: 'F5',
    name: 'Derived 1',
    revision: 1,
    description: { type: 'derived', fields, custom: [] },
    values: {
      document: { value: 'this' },
      source: { value: 'T2' },
      parts: { value: ticked.parts },
      sketches: { value: [] },
      connectors: { value: ticked.connectors },
      location: { value: location },
      base: { value: 'origin' },
    },
    fields: {},
    active: null,
  }
}

async function setup(open: OpenReply, update: (values: Record<string, unknown>) => object = () => ({})) {
  const { bytes } = fixture('bracket')
  const calls = stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/outputs')) return { bytes }
    if (c.url.endsWith('/features/F2/dialog')) return { status: 201, json: open }
    if (c.url.endsWith('/update')) return { json: { ...open, errors: {}, preview: null, ...update((c.body as { values: Record<string, unknown> }).values) } }
    if (c.url.endsWith('/cancel')) return { json: { cancelled: true } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'studio', 'Bracket studio')], read_only: false, holder: null })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  const el = screen.getByRole('button', { name: 'Extrude 1' })
  fireEvent.click(el)
  fireEvent.click(el)
  await screen.findByRole('dialog', { name: 'Derived 1' })
  const form = await screen.findByTestId('dialog-form')
  const updates = () => calls.filter((c: Call) => c.method === 'POST' && c.url.endsWith('/update')).map((c) => (c.body as { values: Record<string, unknown> }).values)
  return { form, updates }
}

it('draws the source chooser, the three tick lists as tabs, Location and the Document chooser with the others off', async () => {
  const { form } = await setup(derived({ parts: ['F2/1'], connectors: [] }))
  // the source chooser is the Part Studio list, with the part under it
  const studios = within(form).getByTestId('part-picker')
  await waitFor(() => expect(within(studios).getByRole('button', { name: 'Bracket studio' }).getAttribute('aria-current')).toBe('true'))
  await waitFor(() => expect(studios.querySelector('[data-picker-part="F2/1"]')).not.toBeNull())
  // the tick lists are the tabs of one row; a title counts its ticks and the row opens on the first tab with one
  const tabs = within(form).getAllByRole('tab')
  expect(tabs.map((t) => t.textContent)).toEqual(['Parts (1)', 'Sketches', 'Mate connectors'])
  expect(tabs[0]!.getAttribute('aria-selected')).toBe('true')
  expect(within(form).getByRole('checkbox', { name: 'Bracket' }).getAttribute('aria-checked')).toBe('true')
  fireEvent.click(tabs[2]!)
  expect(within(form).getByRole('checkbox', { name: /^Pin\s+\(Bracket\)$/ }).getAttribute('aria-checked')).toBe('false')
  // Location is a choice (two short options: segments); Placement base is left out until a Location is chosen
  const location = within(form).getByRole('radiogroup', { name: 'Location' })
  expect(within(location).getAllByRole('radio').map((r) => [r.textContent, r.getAttribute('aria-checked')])).toEqual([
    ['— not placed —', 'true'],
    ['Here', 'false'],
  ])
  expect(within(form).queryByText('Placement base')).toBeNull()
  // the Document chooser: This Document, the others off with the reason
  fireEvent.click(within(form).getByRole('button', { name: /^Document, This Document/ }))
  const other = within(form).getByRole('radio', { name: /Another Document/ })
  expect(other.hasAttribute('disabled')).toBe(true)
  expect(other.getAttribute('title')).toBe(DESKTOP_ONLY)
  expect(other.textContent).toContain(DESKTOP_ONLY)
  expect(within(form).getByRole('radio', { name: /A Version of this Document/ }).hasAttribute('disabled')).toBe(true)
})

it('a tick sends the list as it would be, and a refusal shows under the list', async () => {
  const { form, updates } = await setup(derived({ parts: [], connectors: [] }), (values) =>
    'connectors' in values ? { errors: { connectors: '⚠ Tick part F2/1 first: a connector comes in with its part.' } } : {},
  )
  fireEvent.click(within(form).getByRole('checkbox', { name: 'Bracket' }))
  await waitFor(() => expect(updates()).toEqual([{ parts: ['F2/1'] }]))
  fireEvent.click(within(form).getByRole('tab', { name: /Mate connectors/ }))
  fireEvent.click(within(form).getByRole('checkbox', { name: /^Pin\s+\(Bracket\)$/ }))
  await waitFor(() => expect(updates()).toEqual([{ parts: ['F2/1'] }, { connectors: ['F9'] }]))
  await waitFor(() => expect(within(form).getByRole('alert').textContent).toBe('⚠ Tick part F2/1 first: a connector comes in with its part.'))
})

it('with a Location the Placement base is drawn with Base origin and the ticked connectors', async () => {
  const { form } = await setup(derived({ parts: ['F2/1'], connectors: ['F9'] }, 'F3'))
  expect(within(within(form).getByRole('radiogroup', { name: 'Location' })).getByRole('radio', { name: 'Here' }).getAttribute('aria-checked')).toBe('true')
  const base = within(form).getByRole('radiogroup', { name: 'Placement base' })
  expect(within(base).getAllByRole('radio').map((r) => [r.textContent, r.getAttribute('aria-checked')])).toEqual([
    ['Base origin', 'true'],
    ['Pin  (Bracket)', 'false'],
  ])
})

it('Desktop mode: checkboxes in the tabs, and the Document combo lists the others off with the reason', async () => {
  mode = 'desktop'
  window.localStorage.setItem(MODE_KEY, mode)
  const { form, updates } = await setup(derived({ parts: ['F2/1'], connectors: [] }))
  expect(within(form).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Parts (1)', 'Sketches', 'Mate connectors'])
  const box = within(form).getByRole('checkbox', { name: 'Bracket' }) as HTMLInputElement
  expect(box.tagName).toBe('INPUT')
  expect(box.checked).toBe(true)
  fireEvent.click(box)
  await waitFor(() => expect(updates()).toEqual([{ parts: [] }]))
  fireEvent.click(within(form).getByRole('button', { name: /^Document, This Document/ }))
  const other = await screen.findByRole('menuitemcheckbox', { name: /Another Document/ })
  expect(other.hasAttribute('disabled')).toBe(true)
  expect(other.getAttribute('title')).toBe(DESKTOP_ONLY)
  expect(within(form).getByTestId('part-picker')).toBeTruthy()
})
