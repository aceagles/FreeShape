import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { Shell } from './Shell'
import { makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const T1 = tab('T1', 'studio', 'Part Studio 1')
const T2 = tab('T2', 'assembly', 'Assembly 1')
const hello = (tabs: object[]) => ({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs, read_only: false, holder: null })
const writes = (calls: Call[]) => calls.filter((c) => c.method !== 'GET' || !c.url.endsWith('/outputs')).map((c) => `${c.method} ${c.url}`)

function setup(answer: Parameters<typeof stubFetch>[0], tabs: object[] = [T1, T2]) {
  const calls = stubFetch(answer)
  const onLeave = vi.fn()
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={onLeave} />)
  sock.say(hello(tabs))
  return { calls, onLeave, store }
}

const press = (name: string) => {
  const el = screen.getByRole('button', { name })
  fireEvent.pointerDown(el, { pointerId: 1, pointerType: 'touch', button: 0, clientX: 1, clientY: 1 })
  act(() => {
    vi.advanceTimersByTime(500)
  })
  fireEvent.pointerUp(el, { pointerId: 1, pointerType: 'touch', button: 0 })
}

it('adds a tab and switches to it', async () => {
  const T3 = tab('T3', 'assembly', 'Assembly 2')
  const { calls } = setup((c) => (c.url === '/documents/d/tabs' ? { status: 201, json: { tab: T3, tabs: [T1, T2, T3] } } : undefined))
  act(() => screen.getByRole('button', { name: 'Add tab' }).click())
  await act(async () => screen.getByRole('menuitem', { name: 'Assembly' }).click())
  expect(calls.find((c) => c.method === 'POST')!.body).toEqual({ kind: 'assembly' })
  const added = await screen.findByRole('button', { name: 'Assembly 2' })
  expect(added.getAttribute('aria-current')).toBe('page')
})

it('renames a tab from the long-press menu, with the new name typed in the dialog', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const { calls } = setup((c) => (c.url === '/documents/d/tabs/T2/rename' ? { json: { tab: { ...T2, name: 'Frame' }, tabs: [T1, { ...T2, name: 'Frame' }] } } : undefined))
  press('Assembly 1')
  act(() => screen.getByRole('menuitem', { name: 'Rename' }).click())
  vi.useRealTimers()
  const field = screen.getByRole('textbox', { name: 'Name' }) as HTMLInputElement
  expect(field.value).toBe('Assembly 1')
  fireEvent.change(field, { target: { value: 'Frame' } })
  await act(async () => {
    fireEvent.submit(field.closest('form')!)
  })
  expect(calls.find((c) => c.method === 'POST')!.body).toEqual({ name: 'Frame' })
  expect(await screen.findByRole('button', { name: 'Frame' })).toBeTruthy()
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('asks before deleting a tab, and deletes it on Delete', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const { calls } = setup((c) => (c.method === 'DELETE' ? { json: { deleted: 'T2', tabs: [T1] } } : undefined))
  press('Assembly 1')
  act(() => screen.getByRole('menuitem', { name: 'Delete' }).click())
  vi.useRealTimers()
  expect(screen.getByRole('dialog', { name: 'Delete "Assembly 1"?' })).toBeTruthy()
  expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
  await act(async () => screen.getByRole('button', { name: 'Delete' }).click())
  expect(calls.find((c) => c.method === 'DELETE')!.url).toBe('/documents/d/tabs/T2')
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Assembly 1' })).toBeNull())
})

it('explains in plain words which tab links to a tab that cannot be deleted', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  setup((c) => (c.method === 'DELETE' ? { status: 409, json: { error: 'linked', linked_by: [{ id: 'T2', name: 'Assembly 1' }] } } : undefined))
  press('Part Studio 1')
  act(() => screen.getByRole('menuitem', { name: 'Delete' }).click())
  vi.useRealTimers()
  await act(async () => screen.getByRole('button', { name: 'Delete' }).click())
  const dialog = await screen.findByRole('dialog', { name: 'Could not delete "Part Studio 1"' })
  expect(dialog.textContent).toContain('"Assembly 1" uses it')
})

it('saves with the client id, then shows the dirty dot gone', async () => {
  const { calls } = setup(
    (c) => {
      if (c.url === '/documents/d/save') return { json: { saved: ['T1'] } }
      if (c.url === '/documents/d/tabs') return { json: { document: { id: 'd', name: 'Bracket' }, tabs: [T1, T2], read_only: false, holder: null } }
      return undefined
    },
    [tab('T1', 'studio', 'Part Studio 1', true), T2],
  )
  expect(screen.getByTestId('dirty-dot')).toBeTruthy()
  await act(async () => screen.getByRole('button', { name: 'Save, unsaved changes' }).click())
  expect(calls.find((c) => c.url === '/documents/d/save')).toMatchObject({ method: 'POST', headers: { 'X-Client-Id': 'abcdefgh' } })
  await waitFor(() => expect(screen.queryByTestId('dirty-dot')).toBeNull())
})

it('says what to do when saving is refused because the desktop holds the Document', async () => {
  setup((c) =>
    c.url === '/documents/d/save'
      ? { status: 423, json: { error: 'locked', document: 'd', holder: { program: 'desktop', instance: 'i', heartbeat: 1, stale: false } } }
      : undefined,
  )
  await act(async () => screen.getByRole('button', { name: 'Save' }).click())
  const dialog = await screen.findByRole('dialog', { name: 'Could not save' })
  expect(dialog.textContent).toContain('"Bracket" is being edited in the desktop')
  expect(dialog.textContent).toContain('Take over')
})

it('back with nothing unsaved closes the Document and goes home', async () => {
  const { calls, onLeave } = setup((c) => (c.url === '/documents/d/close' ? { json: { closed: true } } : undefined))
  await act(async () => screen.getByRole('button', { name: 'Back to Documents' }).click())
  expect(writes(calls)).toEqual(['POST /documents/d/close'])
  expect(onLeave).toHaveBeenCalled()
})

it('back on a server that keeps its one Document open still goes home', async () => {
  const { onLeave } = setup((c) => (c.url === '/documents/d/close' ? { status: 409, json: { error: 'single' } } : undefined))
  await act(async () => screen.getByRole('button', { name: 'Back to Documents' }).click())
  expect(onLeave).toHaveBeenCalled()
})

const dirtyTabs = [tab('T1', 'studio', 'Part Studio 1', true)]

it('back with unsaved changes asks: Stay does nothing', () => {
  const { calls, onLeave } = setup(() => undefined, dirtyTabs)
  act(() => screen.getByRole('button', { name: 'Back to Documents' }).click())
  expect(screen.getByRole('dialog', { name: 'Save changes to "Bracket"?' })).toBeTruthy()
  act(() => screen.getByRole('button', { name: 'Stay' }).click())
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(writes(calls)).toEqual([])
  expect(onLeave).not.toHaveBeenCalled()
})

it('back with unsaved changes: Discard closes without saving', async () => {
  const { calls, onLeave } = setup((c) => (c.url === '/documents/d/close' ? { json: { closed: true } } : undefined), dirtyTabs)
  act(() => screen.getByRole('button', { name: 'Back to Documents' }).click())
  await act(async () => screen.getByRole('button', { name: 'Discard' }).click())
  expect(writes(calls)).toEqual(['POST /documents/d/close'])
  expect(onLeave).toHaveBeenCalled()
})

it('back with unsaved changes: Save saves, then closes', async () => {
  const { calls, onLeave } = setup((c) => {
    if (c.url === '/documents/d/save') return { json: { saved: ['T1'] } }
    if (c.url === '/documents/d/tabs') return { json: { document: { id: 'd', name: 'Bracket' }, tabs: [T1], read_only: false, holder: null } }
    if (c.url === '/documents/d/close') return { json: { closed: true } }
    return undefined
  }, dirtyTabs)
  act(() => screen.getByRole('button', { name: 'Back to Documents' }).click())
  await act(async () => screen.getByRole('button', { name: 'Save' }).click())
  await waitFor(() => expect(onLeave).toHaveBeenCalled())
  expect(writes(calls)).toEqual(['POST /documents/d/save', 'GET /documents/d/tabs', 'POST /documents/d/close'])
})
