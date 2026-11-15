import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ConnectionStore, type SocketLike } from './connection'
import { ConnectionPanel, useRefetchOnRevision } from './react'

class Sock implements SocketLike {
  readyState = 1
  onopen: SocketLike['onopen'] = null
  onmessage: SocketLike['onmessage'] = null
  onclose: SocketLike['onclose'] = null
  onerror: SocketLike['onerror'] = null
  send() {}
  close() {}
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function started() {
  const sock = new Sock()
  let now = 100
  const store = new ConnectionStore({ clientId: 'abcdefgh', url: 'ws://t/ws', createSocket: () => sock, now: () => now, visible: () => true, pingMs: 3_600_000 })
  store.start()
  const say = (m: object) => act(() => sock.onmessage?.({ data: JSON.stringify(m) }))
  return { store, say, setNow: (n: number) => (now = n) }
}
const helloMsg = { type: 'hello', client: 'c', document: { id: 'd', name: 'Bracket' }, tabs: [{ id: 'T1', kind: 'studio', name: 'b', state: 'ok', error: null, dirty: false, revision: 1 }] }

it('shows the connection and the median and worst round trip, and copies them', async () => {
  const written: string[] = []
  vi.stubGlobal('navigator', { clipboard: { writeText: async (t: string) => void written.push(t) } })
  const { store, say } = started()
  render(<ConnectionPanel store={store} />)
  expect(screen.getByTestId('connection').textContent).toContain('Not connected')
  say(helloMsg)
  expect(screen.getByTestId('connection').textContent).toBe('Connected to Bracket')
  for (const t of [98, 95, 90]) say({ type: 'pong', t })   // round trips 2, 5, 10
  expect(screen.getByTestId('rtt-median').textContent).toBe('5.0 ms')
  expect(screen.getByTestId('rtt-worst').textContent).toBe('10.0 ms')
  await act(async () => screen.getByRole('button', { name: 'Copy round trips' }).click())
  expect(written).toEqual(['round trips: median 5.0 ms, worst 10.0 ms, over the last 3'])
  store.stop()
})

function Probe({ store, onRefetch }: { store: ConnectionStore; onRefetch: (r: number) => void }) {
  useRefetchOnRevision(store, 'T1', onRefetch)
  return null
}

it('a view refetches its outputs when its tab revision changes', () => {
  const { store, say } = started()
  const refetch = vi.fn()
  render(<Probe store={store} onRefetch={refetch} />)
  say(helloMsg)
  expect(refetch).not.toHaveBeenCalled()
  say({ type: 'changed', client: 'x', rebuilt: ['T1'], revisions: { T1: 2 } })
  expect(refetch).toHaveBeenCalledWith(2)
  say({ type: 'changed', client: 'x', rebuilt: ['T1'], revisions: { T1: 2 } })
  expect(refetch).toHaveBeenCalledTimes(1)
  store.stop()
})
