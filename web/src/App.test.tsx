import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { App } from './App'
import { ConnectionStore, type SocketLike } from './net/connection'

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

function store() {
  return new ConnectionStore({ clientId: 'abcdefgh', url: 'ws://t/ws', createSocket: () => new Sock(), visible: () => true, pingMs: 3_600_000 })
}

it('shows the server version from /health in the debug panel', async () => {
  vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ ok: true, version: '9.9.9' })))
  render(<App store={store()} />)
  await act(async () => screen.getByTestId('link-status').click())
  expect(await screen.findByText('Server version 9.9.9')).toBeTruthy()
})

it('shows the shell when the server does not answer /health', async () => {
  vi.stubGlobal('fetch', async () => new Response('', { status: 404 }))
  render(<App store={store()} />)
  expect(screen.getByTestId('shell')).toBeTruthy()
  await act(async () => screen.getByTestId('link-status').click())
  expect(screen.queryByTestId('version')).toBeNull()
})
