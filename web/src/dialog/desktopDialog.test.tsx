import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { MODE_KEY } from '../shell/mode'
import { fixture } from '../test/fixtures'
import { makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import type { Description, FieldSpec, FieldValue, OpenReply, SelItem } from './dialogApi'
import { DesktopNumberRow } from './fields'

// Desktop mode's feature dialog (card I9.16) in the whole Shell, against a scripted server: one test per kind of row
// (number, text, choice, check, selection), the pointer that opens the pad, and the keys inside a focused field.
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

let mode = 'desktop'
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  window.localStorage.clear()
  window.localStorage.setItem(MODE_KEY, mode)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
  mode = 'desktop'
})

const part = fixture('bracket').outputs.parts[0]!
const edgeNames = [...part.names.edge].filter(([, n]) => n !== '')
const stored = (i: number): SelItem => ({ ok: true, kind: 'edge', ref: { part: part.id, name: edgeNames[i]![1], type: 'line' }, name: edgeNames[i]![1] })
const lab = (i: number) => `Edge ${edgeNames[i]![0] + 1} of ${part.name}`

const fields: FieldSpec[] = [
  { key: 'depth', kind: 'num', standard: true, visible: true, label: 'Depth', unit: 'mm', min: 0, signed: false },
  { key: 'end', kind: 'choice', standard: true, visible: true, label: 'End type', options: [['blind', 'Blind'], ['through', 'Through all'], ['symmetric', 'Symmetric']] },
  { key: 'draft', kind: 'check', standard: true, visible: true, label: 'Draft' },
  { key: 'name', kind: 'text', standard: true, visible: true, label: 'Name', placeholder: 'optional' },
  { key: 'edges', kind: 'sel', standard: true, visible: true, label: 'Edges', want: ['edge'], multi: true, number: false, list: false, tip: null },
]
const open = (): OpenReply => ({
  dialog: 'did1',
  document: 'd',
  tab: 'T1',
  feature: 'F2',
  name: 'Extrude 1',
  revision: 1,
  // not a type with drag arrows: those send their first number back on open (useFeatureDialog)
  description: { type: 'fillet', fields, custom: [] } as Description,
  values: { depth: { value: 40, text: '40' }, end: { value: 'blind' }, draft: { value: false }, name: { value: 'Arm' } },
  fields: { edges: [stored(0), stored(1)] },
  active: null,
})

/** What the scripted server evaluates a typed depth to: the server is the only source of the value. */
const EVALUATED: Record<string, number> = { '25/2': 12.5, '1 in': 25.4, '50': 50 }

async function setup() {
  const { bytes } = fixture('bracket')
  const base = open()
  const calls = stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.endsWith('/features/F2/dialog')) return { status: 201, json: base }
    if (c.url.endsWith('/update')) {
      const sent = (c.body as { values: Record<string, unknown> }).values
      const values: Record<string, FieldValue> = { ...base.values }
      const sel: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(sent)) {
        if (k === 'depth') values[k] = { value: EVALUATED[String(v)] ?? null, text: String(v) }
        else if (k === 'edges') sel[k] = v
        else values[k] = { value: v }
      }
      return { json: { ...base, values, fields: { ...base.fields, ...sel }, errors: {}, preview: null } }
    }
    if (c.url.endsWith('/accept')) return { json: { rebuilt: ['T1'], revision: 2 } }
    if (c.url.endsWith('/cancel')) return { json: { cancelled: true } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: null })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  const el = screen.getByRole('button', { name: 'Extrude 1' })
  fireEvent.click(el)
  fireEvent.click(el)
  await screen.findByTestId('dialog-form')
  const posts = () => calls.filter((c: Call) => c.method === 'POST')
  const calling = (suffix: string) => posts().filter((c) => c.url.endsWith(suffix))
  return { calling, posts }
}

const form = () => screen.getByTestId('dialog-form')
const depth = () => within(form()).getByRole('textbox', { name: 'Depth' }) as HTMLInputElement
/** A mouse press and the focus it gives, then the text typed over what was there. */
function typeInto(input: HTMLInputElement, text: string) {
  fireEvent.pointerDown(input, { pointerId: 1, pointerType: 'mouse', button: 0 })
  act(() => input.focus())
  fireEvent.change(input, { target: { value: text } })
}

it('num: 25/2 goes as typed and the field shows the 12.5 the server evaluated; 1 in shows 25.4 with the unit mm', async () => {
  const { calling } = await setup()
  expect(depth().value).toBe('40')
  expect(screen.queryByTestId('pad')).toBeNull() // no pad in Desktop mode
  typeInto(depth(), '25/2')
  fireEvent.keyDown(depth(), { key: 'Tab' })
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/update')[0]!.body).toEqual({ values: { depth: '25/2' } })
  await waitFor(() => expect(depth().value).toBe('12.5'))
  typeInto(depth(), '1 in')
  act(() => depth().blur())
  await waitFor(() => expect(calling('/update').length).toBe(2))
  expect(calling('/update')[1]!.body).toEqual({ values: { depth: '1 in' } })
  await waitFor(() => expect(depth().value).toBe('25.4'))
  expect(screen.getByTestId('inline-depth').querySelector('.inline-number-unit')?.textContent).toBe('mm')
  expect(depth().placeholder).toBe('mm')
  expect(calling('/accept')).toHaveLength(0)
})

it('num: abc marks the field invalid and sends nothing; Enter then neither sends nor accepts', async () => {
  const { posts } = await setup()
  const before = posts().length
  typeInto(depth(), 'abc')
  expect(depth().getAttribute('aria-invalid')).toBe('true')
  expect(depth().hasAttribute('data-invalid')).toBe(true)
  fireEvent.keyDown(depth(), { key: 'Enter' })
  await act(async () => {})
  expect(posts().length).toBe(before)
  expect(screen.getByRole('dialog', { name: 'Extrude 1' })).toBeTruthy()
  // a well formed text takes the mark off
  fireEvent.change(depth(), { target: { value: '30' } })
  expect(depth().hasAttribute('data-invalid')).toBe(false)
})

it('num: #wall * 2 is kept as text, sent as typed for the server to evaluate, and shown as typed after its answer', () => {
  const sent: string[] = []
  function Harness() {
    const [state, setState] = useState<{ sent?: string; value: FieldValue }>({ value: { value: 40, text: '40' } })
    return (
      <div className="shell" data-mode="desktop">
        <DesktopNumberRow
          spec={fields[0]!}
          value={state.value}
          sent={state.sent}
          error={null}
          variables={['wall']}
          keys={{ onEnter: () => {}, onEscape: () => {} }}
          onCommit={(t) => {
            sent.push(t)
            setState({ sent: t, value: state.value })
          }}
          onPad={() => {}}
        />
        <button type="button" onClick={() => setState({ value: { value: 10, text: '#wall * 2' } })}>
          answer
        </button>
      </div>
    )
  }
  render(<Harness />)
  const input = screen.getByRole('textbox', { name: 'Depth' }) as HTMLInputElement
  typeInto(input, '#wall * 2')
  expect(input.hasAttribute('data-invalid')).toBe(false)
  act(() => input.blur())
  expect(sent).toEqual(['#wall * 2'])
  expect(input.value).toBe('#wall * 2')
  fireEvent.click(screen.getByRole('button', { name: 'answer' })) // the server evaluated it to 10 and keeps the text
  expect(input.value).toBe('#wall * 2')
  // a variable not in scope is marked and not sent
  typeInto(input, '#nope * 2')
  expect(input.hasAttribute('data-invalid')).toBe(true)
  act(() => input.blur())
  expect(sent).toEqual(['#wall * 2'])
})

it('text: a 150 px field at the right of its label; the change goes when Tab leaves it', async () => {
  const { calling } = await setup()
  const name = within(form()).getByRole('textbox', { name: 'Name' }) as HTMLInputElement
  expect(name.closest('.dlg-row-line')).toBeTruthy()
  expect(name.classList.contains('dlg-text-inline')).toBe(true)
  typeInto(name, 'Bracket arm')
  fireEvent.keyDown(name, { key: 'Tab' })
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/update')[0]!.body).toEqual({ values: { name: 'Bracket arm' } })
})

it('choice: the dropdown lists the options with the current one checked, and sends the chosen one', async () => {
  const { calling } = await setup()
  const button = within(screen.getByTestId('combo-end')).getByRole('button', { name: 'End type, Blind' })
  fireEvent.click(button)
  const menu = screen.getByRole('menu', { name: 'End type' })
  const items = within(menu).getAllByRole('menuitemcheckbox')
  expect(items.map((i) => i.textContent)).toEqual(['Blind', 'Through all', 'Symmetric'])
  expect(items.map((i) => i.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false'])
  fireEvent.click(items[1]!)
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/update')[0]!.body).toEqual({ values: { end: 'through' } })
  expect(screen.queryByRole('menu')).toBeNull()
  await waitFor(() => expect(within(screen.getByTestId('combo-end')).getByRole('button').getAttribute('aria-label')).toBe('End type, Through all'))
})

it('check: one checkbox with its label; a click sends the change', async () => {
  const { calling } = await setup()
  const box = within(form()).getByRole('checkbox', { name: 'Draft' }) as HTMLInputElement
  expect(box.checked).toBe(false)
  fireEvent.click(box)
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/update')[0]!.body).toEqual({ values: { draft: true } })
})

it('sel: compact chips, one per item; the cross clears one', async () => {
  const { calling } = await setup()
  const group = screen.getByRole('group', { name: 'Edges' })
  const chips = within(group).getAllByTestId('sel-chip')
  expect(chips.map((c) => c.textContent)).toEqual([lab(0), lab(1)])
  expect(chips.every((c) => c.classList.contains('dlg-chip'))).toBe(true)
  fireEvent.click(within(group).getByRole('button', { name: `Remove ${lab(0)}` }))
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/update')[0]!.body).toEqual({ values: { edges: [stored(1)] } })
  await waitFor(() => expect(within(group).getAllByTestId('sel-chip').map((c) => c.textContent)).toEqual([lab(1)]))
})

it('a mouse click focuses the typed field and no pad opens; a pen tap opens the pad on it', async () => {
  await setup()
  typeInto(depth(), '41')
  expect(document.activeElement).toBe(depth())
  expect(screen.queryByTestId('pad')).toBeNull()
  act(() => depth().blur())
  fireEvent.pointerDown(depth(), { pointerId: 2, pointerType: 'pen', button: 0 })
  expect(await screen.findByTestId('pad')).toBeTruthy()
  expect(screen.getByRole('textbox', { name: 'Depth, editing on the pad' })).toBeTruthy()
})

it('a finger tap in Desktop mode opens the pad too', async () => {
  await setup()
  fireEvent.pointerDown(depth(), { pointerId: 3, pointerType: 'touch', button: 0 })
  expect(await screen.findByTestId('pad')).toBeTruthy()
})

it('Tablet mode is as before: the pad opens with the dialog on its first number, and no field is typed in', async () => {
  mode = 'tablet'
  window.localStorage.setItem(MODE_KEY, 'tablet')
  await setup()
  expect(screen.getByTestId('pad')).toBeTruthy()
  expect(within(form()).getByRole('textbox', { name: 'Depth, editing on the pad' })).toBeTruthy()
  expect(form().querySelector('.inline-number')).toBeNull()
  expect(within(form()).getByRole('switch', { name: 'Draft' })).toBeTruthy()
})

// The keys against the real page routing of card I9.6 (input/keys.ts attachKeys with shell/desktopKeys.ts): it makes no key
// act while a field has the focus and takes Enter and Esc when none has; a field takes its own and stops them there.
it('Enter in a focused field commits it, then accepts the dialog exactly once', async () => {
  const { calling, posts } = await setup()
  typeInto(depth(), '50')
  fireEvent.keyDown(depth(), { key: 'Enter' })
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Extrude 1' })).toBeNull())
  const order = posts().map((c) => c.url.split('/').pop())
  expect(order.indexOf('update')).toBeLessThan(order.indexOf('accept'))
  expect(calling('/update').map((c) => c.body)).toEqual([{ values: { depth: '50' } }])
  await act(async () => {})
  expect(calling('/accept')).toHaveLength(1) // not twice: the page binding never saw the field's Enter
})

it('Enter in the dropdown button and in the checkbox accepts once too', async () => {
  const { calling } = await setup()
  const combo = within(screen.getByTestId('combo-end')).getByRole('button')
  act(() => combo.focus())
  fireEvent.keyDown(combo, { key: 'Enter' })
  await waitFor(() => expect(calling('/accept').length).toBe(1))
  await act(async () => {})
  expect(calling('/accept')).toHaveLength(1)
  expect(screen.queryByRole('menu')).toBeNull() // Enter accepted; it did not open the list
})

it('Esc in a focused field cancels the dialog once and sends nothing typed', async () => {
  const { calling } = await setup()
  typeInto(depth(), '77')
  fireEvent.keyDown(depth(), { key: 'Escape' })
  await waitFor(() => expect(calling('/cancel').length).toBe(1))
  await act(async () => {})
  expect(calling('/cancel')).toHaveLength(1)
  expect(calling('/update')).toHaveLength(0)
  expect(screen.queryByRole('dialog', { name: 'Extrude 1' })).toBeNull()
})

it('with no field focused, Enter accepts and Esc cancels through the page binding (card I9.6)', async () => {
  const first = await setup()
  act(() => (document.activeElement as HTMLElement | null)?.blur())
  fireEvent.keyDown(document.body, { key: 'Enter' })
  await waitFor(() => expect(first.calling('/accept').length).toBe(1))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Extrude 1' })).toBeNull())
})

it('with no field focused, Esc cancels through the page binding (card I9.6)', async () => {
  const { calling } = await setup()
  fireEvent.keyDown(document.body, { key: 'Escape' })
  await waitFor(() => expect(calling('/cancel').length).toBe(1))
  expect(calling('/accept')).toHaveLength(0)
})

it('Tab and Shift+Tab move between the fields, round the ends', async () => {
  await setup()
  typeInto(depth(), '40')
  fireEvent.keyDown(depth(), { key: 'Tab' })
  expect(document.activeElement).toBe(within(screen.getByTestId('combo-end')).getByRole('button'))
  fireEvent.keyDown(document.activeElement!, { key: 'Tab' })
  expect(document.activeElement).toBe(within(form()).getByRole('checkbox', { name: 'Draft' }))
  fireEvent.keyDown(document.activeElement!, { key: 'Tab' })
  expect(document.activeElement).toBe(within(form()).getByRole('textbox', { name: 'Name' }))
  fireEvent.keyDown(document.activeElement!, { key: 'Tab' })
  expect(document.activeElement).toBe(depth())
  fireEvent.keyDown(depth(), { key: 'Tab', shiftKey: true })
  expect(document.activeElement).toBe(within(form()).getByRole('textbox', { name: 'Name' }))
})
