import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ConnectionStore, type SocketLike } from './connection'

class FakeSocket implements SocketLike {
  static all: FakeSocket[] = []
  readyState = 0
  sent: string[] = []
  onopen: SocketLike['onopen'] = null
  onmessage: SocketLike['onmessage'] = null
  onclose: SocketLike['onclose'] = null
  onerror: SocketLike['onerror'] = null
  constructor(readonly url: string) {
    FakeSocket.all.push(this)
  }
  send(data: string) {
    this.sent.push(data)
  }
  close() {
    this.readyState = 3
  }
  open() {
    this.readyState = 1
    this.onopen?.({})
  }
  say(msg: object) {
    this.onmessage?.({ data: JSON.stringify(msg) })
  }
  drop() {
    this.readyState = 3
    this.onclose?.({})
  }
}

const tab = (revision: number | null) => ({
  id: 'T1', kind: 'studio', name: 'bracket', state: 'ok', error: null, dirty: false, revision,
})
const hello = (revision = 3) => ({ type: 'hello', client: 'c', document: { id: 'd', name: 'Bracket' }, tabs: [tab(revision)] })

function make(extra: Partial<ConstructorParameters<typeof ConnectionStore>[0]> = {}) {
  let now = 1000
  const store = new ConnectionStore({
    url: 'ws://test/ws?client=abcdefgh',
    clientId: 'abcdefgh',
    createSocket: (url) => new FakeSocket(url),
    backoff: (n) => 100 * (n + 1),
    now: () => now,
    visible: () => true,
    ...extra,
  })
  return { store, tick: (ms: number) => (now += ms) }
}

beforeEach(() => {
  FakeSocket.all = []
  vi.useFakeTimers()
})
afterEach(() => vi.useRealTimers())

it('connects to /ws with the client id and is connected after hello', () => {
  const { store } = make()
  store.start()
  const s = FakeSocket.all[0]!
  expect(s.url).toBe('ws://test/ws?client=abcdefgh')
  s.open()
  expect(store.getState().connected).toBe(false)
  s.say(hello())
  expect(store.getState()).toMatchObject({ connected: true, document: { id: 'd', name: 'Bracket' }, revisions: { T1: 3 } })
  expect(store.getState().tabs.map((t) => t.id)).toEqual(['T1'])
  store.stop()
})

it('reconnects after the socket closes and reports connected false in between', () => {
  const { store } = make()
  store.start()
  FakeSocket.all[0]!.open()
  FakeSocket.all[0]!.say(hello())
  expect(store.getState().connected).toBe(true)
  FakeSocket.all[0]!.drop()
  expect(store.getState().connected).toBe(false)
  expect(FakeSocket.all.length).toBe(1)
  vi.advanceTimersByTime(99)
  expect(FakeSocket.all.length).toBe(1)
  vi.advanceTimersByTime(1)
  expect(FakeSocket.all.length).toBe(2)
  expect(store.getState().connected).toBe(false)
  FakeSocket.all[1]!.open()
  FakeSocket.all[1]!.say(hello())
  expect(store.getState().connected).toBe(true)
  store.stop()
})

it('backs off while the server stays away and starts over after a connect', () => {
  const { store } = make()
  store.start()
  FakeSocket.all[0]!.drop()
  vi.advanceTimersByTime(100)
  FakeSocket.all[1]!.drop()
  vi.advanceTimersByTime(199)
  expect(FakeSocket.all.length).toBe(2)
  vi.advanceTimersByTime(1)
  expect(FakeSocket.all.length).toBe(3)
  FakeSocket.all[2]!.open()
  FakeSocket.all[2]!.drop()
  vi.advanceTimersByTime(100)
  expect(FakeSocket.all.length).toBe(4)
  store.stop()
})

it('keeps the newest revision per tab and tells the listeners of a tab that moved', () => {
  const { store } = make()
  const seen: number[] = []
  store.onRevision('T1', (r) => seen.push(r))
  store.start()
  const s = FakeSocket.all[0]!
  s.open()
  s.say(hello(3))
  expect(seen).toEqual([])
  s.say({ type: 'changed', client: 'other', rebuilt: ['T1'], revisions: { T1: 4 } })
  s.say({ type: 'changed', client: 'other', rebuilt: ['T1'], revisions: { T1: 2 } })
  expect(seen).toEqual([4])
  expect(store.getState().revisions.T1).toBe(4)
  s.drop()
  vi.advanceTimersByTime(100)
  FakeSocket.all[1]!.open()
  FakeSocket.all[1]!.say(hello(6))                 // edits made while the socket was down
  expect(seen).toEqual([4, 6])
  store.stop()
})

it('pings every 250 ms while visible and records the round trip of each pong', () => {
  const { store, tick } = make()
  store.start()
  const s = FakeSocket.all[0]!
  s.open()
  vi.advanceTimersByTime(250)
  expect(s.sent).toEqual([])                       // no hello yet
  s.say(hello())
  tick(0)
  vi.advanceTimersByTime(250)
  expect(s.sent.length).toBe(1)
  const ping = JSON.parse(s.sent[0]!) as { type: string; t: number }
  expect(ping.type).toBe('ping')
  tick(12)
  s.say({ type: 'pong', t: ping.t })
  expect(store.rttSummary()).toEqual({ count: 1, median: 12, worst: 12 })
  vi.advanceTimersByTime(500)
  expect(s.sent.length).toBe(3)
  store.stop()
})

it('does not ping while the page is hidden', () => {
  let visible = false
  const { store } = make({ visible: () => visible })
  store.start()
  FakeSocket.all[0]!.open()
  FakeSocket.all[0]!.say(hello())
  vi.advanceTimersByTime(1000)
  expect(FakeSocket.all[0]!.sent).toEqual([])
  visible = true
  vi.advanceTimersByTime(250)
  expect(FakeSocket.all[0]!.sent.length).toBe(1)
  store.stop()
})

it('keeps only the last 200 round trips', () => {
  const { store } = make()
  store.start()
  const s = FakeSocket.all[0]!
  s.open()
  s.say(hello())
  for (let i = 0; i < 230; i++) s.say({ type: 'pong', t: 1000 - i })
  expect(store.rttSummary().count).toBe(200)
  store.stop()
})

it('ignores messages that are not JSON objects and stops cleanly', () => {
  const { store } = make()
  store.start()
  const s = FakeSocket.all[0]!
  s.open()
  s.onmessage?.({ data: 'nope' })
  s.onmessage?.({ data: '[1]' })
  s.onmessage?.({ data: new ArrayBuffer(2) })
  expect(store.getState().connected).toBe(false)
  store.stop()
  s.drop()
  vi.advanceTimersByTime(60_000)
  expect(FakeSocket.all.length).toBe(1)
})

it('sends the id kept for this tab on every connect, including after a reload', () => {
  window.sessionStorage.clear()
  const urls: string[] = []
  const create = (url: string) => {
    urls.push(url)
    return new FakeSocket(url)
  }
  const first = new ConnectionStore({ createSocket: create, visible: () => true })
  first.start()
  FakeSocket.all[0]!.drop()
  vi.advanceTimersByTime(600)
  first.stop()
  const reloaded = new ConnectionStore({ createSocket: create, visible: () => true })    // a new page, same tab
  reloaded.start()
  reloaded.stop()
  expect(urls.length).toBe(3)
  const ids = urls.map((u) => new URL(u).searchParams.get('client'))
  expect(ids[0]).toMatch(/^[0-9a-f]{32}$/)
  expect(new Set(ids).size).toBe(1)
  expect(urls[0]).toMatch(/^ws:\/\/[^/]+\/ws\?client=/)
})
