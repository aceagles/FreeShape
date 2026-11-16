import { expect, it } from 'vitest'
import { makeStore } from '../test/fakes'

// What the connection hands a sketch session (I7.3): the messages out, the sketch.* answers in, and the hello after a reconnect.

it('send writes JSON on the open socket and says whether it did', () => {
  const { store, sock } = makeStore()
  expect(store.send({ type: 'sketch.state' })).toBe(true)
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.state' })
  sock.readyState = 3
  expect(store.send({ type: 'sketch.state' })).toBe(false)
  store.stop()
})

it('sketch answers and the errors for sketch messages go to the sketch listeners only', () => {
  const { store, sock } = makeStore()
  store.watch('d')
  const heard: Record<string, unknown>[] = []
  const off = store.onSketch((m) => heard.push(m))
  sock.say({ type: 'sketch.changed', document: 'd', tab: 'T1' })
  sock.say({ type: 'error', for: 'sketch.add', message: 'picks: wrong' })
  sock.say({ type: 'error', for: 'edit', message: 'not mine' })
  sock.say({ type: 'sketch.changed', document: 'other', tab: 'T1' })
  expect(heard.map((m) => m['type'])).toEqual(['sketch.changed', 'error'])
  expect(heard[1]!['for']).toBe('sketch.add')
  off()
  sock.say({ type: 'sketch.cancelled', document: 'd', tab: 'T1' })
  expect(heard).toHaveLength(2)
  store.stop()
})

it('onHello fires for each hello of the watched Document', () => {
  const { store, sock } = makeStore()
  store.watch('d')
  let n = 0
  store.onHello(() => n++)
  const hello = { type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'B' }, tabs: [] }
  sock.say(hello)
  sock.say(hello)
  expect(n).toBe(2)
  store.stop()
})
