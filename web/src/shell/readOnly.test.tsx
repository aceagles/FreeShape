import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { Shell } from './Shell'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const hello = (extra: object) => ({
  type: 'hello',
  client: 'abcdefgh',
  document: { id: 'd', name: 'Bracket' },
  tabs: [tab('T1', 'studio', 'Part Studio 1', true), tab('T2', 'assembly', 'Assembly 1')],
  read_only: false,
  holder: null,
  ...extra,
})

function setup(extra: object) {
  stubFetch(() => undefined)
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say(hello(extra))
  return { store, sock }
}

const button = (name: string) => screen.getByRole('button', { name }) as HTMLButtonElement

it('with the Document read-only, save and add tab are off and the banner names the holder', () => {
  setup({ read_only: true, holder: holderOf('desktop') })
  expect(button('Save, unsaved changes').disabled).toBe(true)
  expect(button('Add tab').disabled).toBe(true)
  expect(screen.getByTestId('read-only-banner').textContent).toContain('"Bracket" is being edited in the desktop, so it is open read-only here.')
  expect(screen.getByRole('button', { name: 'Take over' })).toBeTruthy()
})

it('a writable Document has no banner, and the save button shows the dot while a tab is unsaved', () => {
  setup({})
  expect(screen.queryByTestId('read-only-banner')).toBeNull()
  expect(button('Save, unsaved changes').disabled).toBe(false)
  expect(screen.getByTestId('dirty-dot')).toBeTruthy()
  expect(button('Add tab').disabled).toBe(false)
})

it('the save button has no dot when nothing is unsaved', () => {
  setup({ tabs: [tab('T1', 'studio', 'Part Studio 1')] })
  expect(button('Save').disabled).toBe(false)
  expect(screen.queryByTestId('dirty-dot')).toBeNull()
})

it('the banner appears when a lock message takes the Document away, and goes when it is handed back', () => {
  const { sock } = setup({})
  sock.say({ type: 'lock', document: 'd', read_only: true, holder: holderOf('desktop') })
  expect(screen.getByTestId('read-only-banner')).toBeTruthy()
  sock.say({ type: 'lock', document: 'd', read_only: false, holder: null })
  expect(screen.queryByTestId('read-only-banner')).toBeNull()
})

it('Take over asks first, and only then calls the server and makes the Document editable', async () => {
  const calls = stubFetch((c) => {
    if (c.url === '/documents/d/take-over') return { json: { read_only: false, holder: null } }
    if (c.url === '/documents/d/tabs') {
      return { json: { document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: null } }
    }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say(hello({ read_only: true, holder: holderOf('desktop') }))
  act(() => screen.getByRole('button', { name: 'Take over' }).click())
  expect(screen.getByRole('dialog', { name: 'Take over "Bracket"?' })).toBeTruthy()
  expect(calls.filter((c) => !c.url.endsWith('/outputs') && !c.url.includes('/features/types'))).toEqual([])
  await act(async () => screen.getAllByRole('button', { name: 'Take over' }).at(-1)!.click())
  await waitFor(() => expect(screen.queryByTestId('read-only-banner')).toBeNull())
  expect(calls.filter((c) => !c.url.endsWith('/outputs') && !c.url.includes('/features/types')).map((c) => `${c.method} ${c.url}`)).toEqual(['POST /documents/d/take-over', 'GET /documents/d/tabs'])
})
