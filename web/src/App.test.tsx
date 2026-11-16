import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { App } from './App'
import { ConnectionStore, type SocketLike } from './net/connection'

class Sock implements SocketLike {
  readyState = 1
  sent: string[] = []
  onopen: SocketLike['onopen'] = null
  onmessage: SocketLike['onmessage'] = null
  onclose: SocketLike['onclose'] = null
  onerror: SocketLike['onerror'] = null
  send(data: string) {
    this.sent.push(data)
  }
  close() {}
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const tab = { id: 'T1', kind: 'studio', name: 'Part Studio 1', state: 'ok', error: null, dirty: false, revision: 1 }
const row = (id: string, name: string, open: boolean) => ({ id, name, modified: 1_700_000_000, holder: null, open, missing: false })

/** A scripted server: /health, GET /documents with `rows`, POST /documents/<id>/open. */
function server(rows: ReturnType<typeof row>[]) {
  const calls: string[] = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? 'GET'} ${url}`)
    if (url === '/health') return new Response(JSON.stringify({ ok: true, version: '9.9.9' }))
    if (url === '/documents') return new Response(JSON.stringify({ documents: rows }))
    const m = /^\/documents\/([^/]+)\/open$/.exec(url)
    if (m) {
      const r = rows.find((x) => x.id === m[1])!
      return new Response(JSON.stringify({ document: { id: r.id, name: r.name }, tabs: [tab], read_only: false, holder: null }))
    }
    return new Response('', { status: 404 })
  })
  return calls
}

function store() {
  const sock = new Sock()
  return { sock, store: new ConnectionStore({ clientId: 'abcdefgh', url: 'ws://t/ws', createSocket: () => sock, visible: () => true, pingMs: 3_600_000 }) }
}

it('starts on the home screen when the server has several Documents, and shows the server version', async () => {
  server([row('a', 'Bracket', false), row('b', 'Hinge', false)])
  render(<App store={store().store} />)
  expect(await screen.findByTestId('home')).toBeTruthy()
  await act(async () => screen.getByTestId('link-status').click())
  expect(await screen.findByText('Server version 9.9.9')).toBeTruthy()
})

it('goes straight into the one Document of a single-Document server, opening it and subscribing to it', async () => {
  const calls = server([row('doc1', 'Bracket', true)])
  const { sock, store: s } = store()
  s.start()
  render(<App store={s} />)
  expect(await screen.findByTestId('shell')).toBeTruthy()
  expect(screen.queryByTestId('home')).toBeNull()
  expect(calls).toContain('POST /documents/doc1/open')
  expect(screen.getByTestId('document-name').textContent).toBe('Bracket')
  expect(sock.sent.map((m) => JSON.parse(m))).toContainEqual({ type: 'subscribe', document: 'doc1' })
  s.stop()
})

it('starts on the home screen when the one Document is not open, and when the list cannot be read', async () => {
  server([row('doc1', 'Bracket', false)])
  render(<App store={store().store} />)
  expect(await screen.findByTestId('home')).toBeTruthy()
  cleanup()
  vi.stubGlobal('fetch', async () => new Response('', { status: 500 }))
  render(<App store={store().store} />)
  expect(await screen.findByTestId('home')).toBeTruthy()
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('could not be listed'))
})

it('resumes the one open Document of several after a reload', async () => {
  server([row('a', 'Bracket', false), row('b', 'Hinge', true)])
  render(<App store={store().store} />)
  expect(await screen.findByTestId('shell')).toBeTruthy()
  expect(screen.getByTestId('document-name').textContent).toBe('Hinge')
})
