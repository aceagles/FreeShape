import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { Home } from './Home'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const NOW = Date.UTC(2026, 10, 16, 12, 0, 0)
const ago = (s: number) => NOW / 1000 - s
const rows = [
  { id: 'a', name: 'Bracket', modified: ago(3600), holder: null, open: false, missing: false },
  { id: 'b', name: 'Hinge', modified: ago(86400 * 3), holder: holderOf('desktop'), open: false, missing: false },
  { id: 'c', name: 'Lost', modified: null, holder: null, open: false, missing: true },
]
const reply = (id: string, name: string) => ({ document: { id, name }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: null })

function setup(onOpened = vi.fn(), notice?: string) {
  const { store } = makeStore()
  render(<Home store={store} notice={notice} onOpened={onOpened} now={() => NOW} />)
  return { onOpened, store }
}

it('lists the Documents from /documents with their change time and who holds them, newest first', async () => {
  stubFetch((c) => (c.url === '/documents' ? { json: { documents: rows } } : undefined))
  setup()
  const list = await screen.findByRole('list', { name: 'Documents' })
  const items = within(list).getAllByRole('listitem')
  expect(items.map((i) => i.querySelector('.doc-name')?.textContent)).toEqual(['Bracket', 'Hinge', 'Lost'])
  expect(items[0]!.textContent).toContain('Modified 1 hour ago')
  expect(items[1]!.textContent).toContain('Open in the desktop')
  expect(items[1]!.textContent).toContain('opens read-only here')
  expect(items[2]!.textContent).toContain('Folder missing')
  expect((within(items[2]!).getByRole('button') as HTMLButtonElement).disabled).toBe(true)
})

it('says so when there are no Documents, and shows a notice it is given', async () => {
  stubFetch(() => ({ json: { documents: [] } }))
  setup(vi.fn(), '"Bracket" was closed on the server.')
  expect(await screen.findByTestId('home-empty')).toBeTruthy()
  expect(screen.getByTestId('home-notice').textContent).toContain('was closed on the server')
})

it('opens a Document with POST /documents/<id>/open and hands the answer on', async () => {
  const calls = stubFetch((c) => (c.url === '/documents' ? { json: { documents: rows } } : c.url === '/documents/a/open' ? { json: reply('a', 'Bracket') } : undefined))
  const { onOpened } = setup()
  const row = await screen.findByRole('button', { name: /Bracket/ })
  await act(async () => row.click())
  expect(calls.some((c) => c.method === 'POST' && c.url === '/documents/a/open')).toBe(true)
  await waitFor(() => expect(onOpened).toHaveBeenCalledWith(reply('a', 'Bracket')))
})

it('makes a new Document from the name typed, then opens it; Enter submits', async () => {
  const calls = stubFetch((c) => {
    if (c.url === '/documents' && c.method === 'GET') return { json: { documents: [] } }
    if (c.url === '/documents' && c.method === 'POST') return { status: 201, json: { id: 'n1', name: 'Gearbox' } }
    if (c.url === '/documents/n1/open') return { json: reply('n1', 'Gearbox') }
    return undefined
  })
  const { onOpened } = setup()
  await screen.findByTestId('home-empty')
  act(() => screen.getByRole('button', { name: 'New Document' }).click())
  const field = screen.getByRole('textbox', { name: 'Name' })
  expect((screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(field, { target: { value: '  Gearbox ' } })
  await act(async () => {
    fireEvent.submit(field.closest('form')!)
  })
  expect(calls.find((c) => c.method === 'POST' && c.url === '/documents')!.body).toEqual({ name: 'Gearbox' })
  await waitFor(() => expect(onOpened).toHaveBeenCalledWith(reply('n1', 'Gearbox')))
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('keeps the dialog open and shows the server\'s words when a new Document is refused', async () => {
  stubFetch((c) =>
    c.url === '/documents' && c.method === 'GET'
      ? { json: { documents: [] } }
      : c.url === '/documents'
        ? { status: 400, json: { error: 'the name must not be empty' } }
        : undefined,
  )
  setup()
  await screen.findByTestId('home-empty')
  act(() => screen.getByRole('button', { name: 'New Document' }).click())
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'x' } })
  await act(async () => screen.getByRole('button', { name: 'Create' }).click())
  expect((await screen.findByRole('alert')).textContent).toBe('the name must not be empty')
  expect(screen.getByRole('dialog', { name: 'New Document' })).toBeTruthy()
})

it('when another Document is open, says which and offers to close it, then opens the one asked for', async () => {
  let closed = false
  const calls = stubFetch((c) => {
    if (c.url === '/documents') return { json: { documents: rows } }
    if (c.url === '/documents/a/open' && !closed) {
      return { status: 409, json: { error: 'another Document is open', open: { id: 'z', name: 'Hinge' } } }
    }
    if (c.url === '/documents/z/close') {
      closed = true
      return { json: { closed: true } }
    }
    if (c.url === '/documents/a/open') return { json: reply('a', 'Bracket') }
    return undefined
  })
  const { onOpened } = setup()
  const row = await screen.findByRole('button', { name: /Bracket/ })
  await act(async () => row.click())
  const dialog = await screen.findByRole('dialog', { name: 'Another Document is open' })
  expect(dialog.textContent).toContain('"Hinge" is still open')
  expect(dialog.textContent).toContain('open "Bracket"')
  expect(onOpened).not.toHaveBeenCalled()
  await act(async () => within(dialog).getByRole('button', { name: 'Close "Hinge"' }).click())
  await waitFor(() => expect(onOpened).toHaveBeenCalledWith(reply('a', 'Bracket')))
  expect(calls.map((c) => c.url)).toContain('/documents/z/close')
})

const offer = (extra: object = {}) => ({
  time: ago(300),
  count: 1,
  tabs: [{ id: 'T1', name: 'Plate' }],
  deleted: [],
  moved_on: [],
  list_moved_on: false,
  writable: true,
  ...extra,
})
const withAutosave = [{ ...rows[0]!, autosave: { time: ago(300), count: 1 } }, rows[1]!]
const openBracket = async () => {
  const row = await screen.findByRole('button', { name: /Bracket/ })
  await act(async () => row.click())
}
const openReply = (autosave: object | null, readOnly = false) => ({ ...reply('a', 'Bracket'), autosave, read_only: readOnly })

it('marks a Document that has an autosave to recover', async () => {
  stubFetch((c) => (c.url === '/documents' ? { json: { documents: withAutosave } } : undefined))
  setup()
  const marks = await screen.findAllByTestId('recover-mark')
  expect(marks).toHaveLength(1)
  expect(marks[0]!.textContent).toBe('Unsaved edits can be recovered')
  expect(marks[0]!.closest('li')!.textContent).toContain('Bracket')
})

it('opening a Document with an autosave asks Recover or Discard and says when it was written and what each does', async () => {
  const calls = stubFetch((c) => (c.url === '/documents' ? { json: { documents: withAutosave } } : c.url === '/documents/a/open' ? { json: openReply(offer()) } : undefined))
  const { onOpened } = setup()
  await openBracket()
  const dialog = await screen.findByRole('dialog', { name: 'Recover unsaved changes to "Bracket"?' })
  expect(onOpened).not.toHaveBeenCalled()
  expect(dialog.textContent).toContain('written 5 minutes ago')
  for (const [button, effect] of [['Recover', 'puts them back into the open Document'], ['Discard', 'deletes them'], ['Decide later', 'opens the Document as it was last saved and keeps the changes']] as const) {
    expect(within(dialog).getByRole('button', { name: button })).toBeTruthy()
    expect(dialog.textContent).toContain(`${button} ${effect}`)
  }
  expect(screen.queryByTestId('autosave-moved')).toBeNull()
  expect(calls.filter((c) => c.method === 'POST').map((c) => c.url)).toEqual(['/documents/a/open'])
})

it('Recover asks the server to recover and opens the Document with the recovered tabs', async () => {
  const recovered = [{ ...tab('T1', 'studio', 'Plate'), dirty: true }]
  const calls = stubFetch((c) => {
    if (c.url === '/documents') return { json: { documents: withAutosave } }
    if (c.url === '/documents/a/open') return { json: openReply(offer()) }
    if (c.url === '/documents/a/recover') return { json: { recovered: ['T1'], moved_on: [], list_moved_on: false, skipped: [], unreadable: 0, tabs: recovered } }
    return undefined
  })
  const { onOpened } = setup()
  await openBracket()
  await act(async () => screen.getByRole('button', { name: 'Recover' }).click())
  expect(calls.some((c) => c.method === 'POST' && c.url === '/documents/a/recover')).toBe(true)
  await waitFor(() => expect(onOpened).toHaveBeenCalled())
  expect(onOpened.mock.calls[0]![0]).toMatchObject({ document: { id: 'a' }, tabs: recovered, autosave: null })
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('Discard deletes the autosave and opens the Document as saved; Decide later opens it and deletes nothing', async () => {
  const calls = stubFetch((c) => {
    if (c.url === '/documents') return { json: { documents: withAutosave } }
    if (c.url === '/documents/a/open') return { json: openReply(offer()) }
    if (c.url === '/documents/a/discard-autosave') return { json: { discarded: 1 } }
    return undefined
  })
  const { onOpened } = setup()
  await openBracket()
  await act(async () => screen.getByRole('button', { name: 'Discard' }).click())
  expect(calls.some((c) => c.url === '/documents/a/discard-autosave')).toBe(true)
  await waitFor(() => expect(onOpened).toHaveBeenCalledTimes(1))
  expect(onOpened.mock.calls[0]![0]).toMatchObject({ tabs: [{ id: 'T1' }], autosave: null })
  // the other way
  calls.length = 0
  await openBracket()
  await act(async () => screen.getByRole('button', { name: 'Decide later' }).click())
  expect(calls.map((c) => c.url)).toEqual(['/documents/a/open'])
  await waitFor(() => expect(onOpened).toHaveBeenCalledTimes(2))
})

it('warns that Recover replaces work another program saved since, naming the tabs, and says nothing is saved yet', async () => {
  stubFetch((c) => (c.url === '/documents' ? { json: { documents: withAutosave } } : c.url === '/documents/a/open' ? { json: openReply(offer({ moved_on: ['T1'] })) } : undefined))
  setup()
  await openBracket()
  const warning = await screen.findByTestId('autosave-moved')
  expect(warning.textContent).toContain('"Plate" was saved by another program after these changes were written')
  expect(warning.textContent).toContain('replaces that saved work in the open Document')
  expect(warning.textContent).toContain('stays on disk until you press Save')
})

it('opens a Document read-only without the question when another program holds the lock', async () => {
  stubFetch((c) => (c.url === '/documents' ? { json: { documents: withAutosave } } : c.url === '/documents/a/open' ? { json: openReply(offer({ writable: false }), true) } : undefined))
  const { onOpened } = setup()
  await openBracket()
  await waitFor(() => expect(onOpened).toHaveBeenCalled())
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('shows why a recover failed and does not open the Document', async () => {
  stubFetch((c) => {
    if (c.url === '/documents') return { json: { documents: withAutosave } }
    if (c.url === '/documents/a/open') return { json: openReply(offer()) }
    if (c.url === '/documents/a/recover') return { status: 423, json: { error: 'the Document is being edited by desktop' } }
    return undefined
  })
  const { onOpened } = setup()
  await openBracket()
  await act(async () => screen.getByRole('button', { name: 'Recover' }).click())
  const dialog = await screen.findByRole('dialog', { name: 'Could not recover "Bracket"' })
  expect(dialog.textContent).toContain('being edited by desktop')
  expect(onOpened).not.toHaveBeenCalled()
})
