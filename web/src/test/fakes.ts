import { act } from '@testing-library/react'
import { vi } from 'vitest'
import { ConnectionStore } from '../net/connection'
import type { SocketLike } from '../net/connection'

/** A socket that records what is sent and lets the test say what the server says. */
export class FakeSocket implements SocketLike {
  readyState = 1
  sent: Array<Record<string, unknown>> = []
  onopen: SocketLike['onopen'] = null
  onmessage: SocketLike['onmessage'] = null
  onclose: SocketLike['onclose'] = null
  onerror: SocketLike['onerror'] = null
  send(data: string) {
    this.sent.push(JSON.parse(data) as Record<string, unknown>)
  }
  close() {}
  say(msg: object) {
    act(() => this.onmessage?.({ data: JSON.stringify(msg) }))
  }
}

export const tab = (id: string, kind: string, name: string, dirty = false) => ({ id, kind, name, state: 'ok', error: null, dirty, revision: 1 })

export const holderOf = (program: string, stale = false) => ({ program, instance: 'i', heartbeat: 1, stale })

export function makeStore() {
  const sock = new FakeSocket()
  const store = new ConnectionStore({
    clientId: 'abcdefgh',
    url: 'ws://t/ws',
    createSocket: () => sock,
    visible: () => true,
    pingMs: 3_600_000,
    backoff: () => 3_600_000,
  })
  store.start()
  return { store, sock }
}

export interface Call {
  method: string
  url: string
  body: unknown
  headers: Record<string, string>
}

type Reply = { status?: number; json?: unknown; bytes?: ArrayBuffer }
/** Stubs fetch: every request is recorded, `answer` decides the reply (default 404). */
export function stubFetch(answer: (call: Call) => Reply | undefined): Call[] {
  const calls: Call[] = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const call: Call = {
      method: init?.method ?? 'GET',
      url,
      body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined,
      headers: (init?.headers ?? {}) as Record<string, string>,
    }
    calls.push(call)
    const r = answer(call)
    if (!r) return new Response('', { status: 404 })
    if (r.bytes) return new Response(r.bytes, { status: r.status ?? 200 })
    return new Response(JSON.stringify(r.json ?? {}), { status: r.status ?? 200 })
  })
  return calls
}
