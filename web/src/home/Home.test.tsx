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
