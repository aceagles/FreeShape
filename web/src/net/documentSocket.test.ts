import { act } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'

afterEach(() => vi.unstubAllGlobals())

const hello = (doc: string, extra: object = {}) => ({
  type: 'hello',
  client: 'abcdefgh',
  document: { id: doc, name: `Doc ${doc}` },
  tabs: [tab('T1', 'studio', 'Part Studio 1')],
  read_only: false,
  holder: null,
  ...extra,
})

it('subscribes the socket with the Document id, and again after a reconnect', () => {
  const { store, sock } = makeStore()
  store.watch('doc-1')
  expect(sock.sent).toEqual([{ type: 'subscribe', document: 'doc-1' }])
  act(() => sock.onopen?.({}))
  expect(sock.sent.at(-1)).toEqual({ type: 'subscribe', document: 'doc-1' })
  store.unwatch()
  expect(sock.sent.at(-1)).toEqual({ type: 'unsubscribe', document: 'doc-1' })
  store.stop()
})

it('takes the Document, its lock and its tabs from the hello, and ignores messages about another Document', () => {
  const { store, sock } = makeStore()
  store.watch('doc-1')
  sock.say(hello('doc-1', { read_only: true, holder: holderOf('desktop') }))
  expect(store.getState()).toMatchObject({ connected: true, document: { id: 'doc-1' }, readOnly: true, holder: { program: 'desktop' } })
  sock.say({ type: 'tabs', document: 'other', tabs: [] })
  sock.say(hello('other'))
  expect(store.getState().document?.id).toBe('doc-1')
  expect(store.getState().tabs.length).toBe(1)
  store.stop()
})

it('follows lock messages and says when the Document was closed or is not open', () => {
  const { store, sock } = makeStore()
  store.watch('doc-1')
  sock.say(hello('doc-1'))
  sock.say({ type: 'lock', document: 'doc-1', read_only: true, holder: holderOf('desktop') })
  expect(store.getState()).toMatchObject({ readOnly: true, holder: { program: 'desktop' } })
  sock.say({ type: 'lock', document: 'doc-1', read_only: false, holder: null })
  expect(store.getState()).toMatchObject({ readOnly: false, holder: null })
  sock.say({ type: 'closed', document: 'doc-1' })
  expect(store.getState()).toMatchObject({ document: null, ended: 'closed', tabs: [] })
  store.watch('doc-2')
  expect(store.getState().ended).toBeNull()
  sock.say({ type: 'error', document: 'doc-2', message: 'the Document is not open' })
  expect(store.getState().ended).toBe('not-open')
  store.stop()
})

it('tells a client that comes back to a Document the server closed for being idle', () => {
  const { store, sock } = makeStore()
  store.watch('doc-1')
  sock.say({ type: 'closed', document: 'doc-1', reason: 'idle' })
  expect(store.getState()).toMatchObject({ document: null, ended: 'idle' })
  store.stop()
})

it('raises the epoch when a hello shows a revision that differs from the one held', () => {
  const { store, sock } = makeStore()
  store.watch('doc-1')
  sock.say(hello('doc-1'))
  const before = store.getState().epoch
  sock.say(hello('doc-1'))
  expect(store.getState().epoch).toBe(before)
  sock.say(hello('doc-1', { tabs: [{ ...tab('T1', 'studio', 'Part Studio 1'), revision: 5 }] }))
  expect(store.getState().epoch).toBe(before + 1)
  store.stop()
})

it('sends an edit to /documents/<id>/tabs/<tab>/edit with its client id and the revision it was based on', async () => {
  const calls = stubFetch(() => ({ json: { rebuilt: ['T1'] } }))
  const { store, sock } = makeStore()
  store.watch('doc-1')
  sock.say(hello('doc-1'))
  await store.edit('T1', { op: 'noop' })
  expect(calls[0]).toMatchObject({ url: '/documents/doc-1/tabs/T1/edit', headers: { 'X-Client-Id': 'abcdefgh', 'X-Base-Revision': '1' } })
  store.stop()
})
