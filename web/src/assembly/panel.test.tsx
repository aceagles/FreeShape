import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { Shell } from '../shell/Shell'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const bytes = (): ArrayBuffer => {
  const b = readFileSync(resolve(process.cwd(), 'fixtures', 'hinge.assembly.fsw'))
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
}

const hello = (extra: object = {}) => ({
  type: 'hello',
  client: 'abcdefgh',
  document: { id: 'd', name: 'Hinge' },
  tabs: [tab('T1', 'studio', 'hinge_parts'), tab('T2', 'assembly', 'hinge')],
  read_only: false,
  holder: null,
  ...extra,
})

/** The Shell on the hinge Document with its Assembly tab on screen (the fixture hides I3). */
async function open(extra: object = {}) {
  const calls = stubFetch((c) =>
    c.method === 'GET' && c.url.endsWith('/outputs') ? { bytes: bytes() } : c.url.endsWith('/edit') ? { json: { rebuilt: ['T2'] } } : undefined,
  )
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say(hello(extra))
  await screen.findByRole('button', { name: 'hinge' })
  fireEvent.click(screen.getByRole('button', { name: 'hinge' }))
  await screen.findByTestId('instance-list')
  const edits = () => calls.filter((c: Call) => c.method === 'POST' && c.url.endsWith('/edit'))
  return { store, sock, calls, edits }
}

it('lists the instances with eye and fixed toggles, and the mates read-only with their types', async () => {
  await open()
  const panel = screen.getByTestId('assembly-panel')
  expect(within(panel).getByText('Instances (3)')).toBeTruthy()
  expect(within(panel).getByText('Mates (2)')).toBeTruthy()
  const rows = [...panel.querySelectorAll<HTMLElement>('[data-instance-row]')]
  expect(rows.map((r) => r.dataset['instanceRow'])).toEqual(['I1', 'I2', 'I3'])
  // I3 is hidden in the fixture: its eye offers Show; the others offer Hide
  expect(within(panel).getByRole('button', { name: 'Show Pin <1>' })).toBeTruthy()
  expect(within(panel).getByRole('button', { name: 'Hide Arm <1>' })).toBeTruthy()
  const mates = [...panel.querySelectorAll<HTMLElement>('[data-mate-row]')]
  expect(mates.map((m) => m.textContent)).toEqual(['Fastened 1Fastened: Base plate <1> and Pin <1>', 'Revolute 1Revolute: Base plate <1> and Arm <1>'])
  expect(panel.querySelectorAll('.mate-row button').length).toBe(0)
})

it('the eye sends set_hidden for that instance, with the client id', async () => {
  const { edits } = await open()
  fireEvent.click(screen.getByRole('button', { name: 'Hide Arm <1>' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]).toMatchObject({ url: '/documents/d/tabs/T2/edit', body: { op: 'set_hidden', instance: 'I2', value: true } })
  expect(edits()[0]?.headers['X-Client-Id']).toBe('abcdefgh')
  // an instance that is hidden is shown again
  fireEvent.click(screen.getByRole('button', { name: 'Show Pin <1>' }))
  await waitFor(() => expect(edits().length).toBe(2))
  expect(edits()[1]?.body).toEqual({ op: 'set_hidden', instance: 'I3', value: false })
})

it('the fixed toggle sends set_fixed for that instance', async () => {
  const { edits } = await open()
  const free = screen.getAllByRole('button', { name: /^Fix / })[0]!
  fireEvent.click(free)
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toMatchObject({ op: 'set_fixed', value: true })
  const fixed = screen.getAllByRole('button', { name: /^Unfix / })[0]!
  fireEvent.click(fixed)
  await waitFor(() => expect(edits().length).toBe(2))
  expect(edits()[1]?.body).toMatchObject({ op: 'set_fixed', value: false })
})

it('Delete is offered for the selected instance, asks first, then sends delete_instances', async () => {
  const { edits } = await open()
  expect(screen.queryByRole('button', { name: /^Delete / })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /^Arm <1>/ }))
  expect(screen.getByTestId('assembly-panel').getAttribute('data-selected-instance')).toBe('I2')
  fireEvent.click(screen.getByRole('button', { name: 'Delete Arm <1>' }))
  const dialog = screen.getByRole('dialog', { name: /Delete "Arm <1>"/ })
  expect(edits().length).toBe(0)
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toEqual({ op: 'delete_instances', ids: ['I2'] })
})

it('with the Document read-only the toggles are off and nothing can be deleted', async () => {
  const { edits } = await open({ read_only: true, holder: holderOf('desktop') })
  for (const b of screen.getAllByRole('button', { name: /^(Hide|Show|Fix|Unfix) / })) expect((b as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: /^Arm <1>/ }))
  expect(screen.queryByRole('button', { name: /^Delete / })).toBeNull()
  act(() => {})
  expect(edits().length).toBe(0)
})
