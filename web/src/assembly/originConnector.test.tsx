import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { LONG_PRESS_MS } from '../shell/longPress'
import { Shell } from '../shell/Shell'

// The instance's long-press popover in Tablet mode (card I10.4): one item, which sends add_origin_connector.
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const bytes = (): ArrayBuffer => {
  const b = readFileSync(resolve(process.cwd(), 'fixtures', 'hinge.assembly.fsw'))
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
}

async function open(readOnly = false) {
  const calls = stubFetch((c) => (c.method === 'GET' && c.url.endsWith('/outputs') ? { bytes: bytes() } : c.url.endsWith('/edit') ? { json: { rebuilt: ['T2'] } } : undefined))
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Hinge' }, tabs: [tab('T1', 'studio', 'hinge_parts'), tab('T2', 'assembly', 'hinge')], read_only: readOnly, holder: readOnly ? holderOf('desktop') : null })
  await screen.findByRole('button', { name: 'hinge' })
  fireEvent.click(screen.getByRole('button', { name: 'hinge' }))
  await screen.findByTestId('instance-list')
  return { store, edits: () => calls.filter((c: Call) => c.method === 'POST' && c.url.endsWith('/edit')) }
}

function longPress(el: HTMLElement) {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const at = { pointerId: 1, pointerType: 'touch', button: 0, clientX: 10, clientY: 10 }
  fireEvent.pointerDown(el, at)
  act(() => {
    vi.advanceTimersByTime(LONG_PRESS_MS)
  })
  fireEvent.pointerUp(el, at)
  fireEvent.click(el)
  vi.useRealTimers()
}

const row = (id: string) => document.querySelector<HTMLElement>(`[data-instance="${id}"]`)!

it('a long press on an instance row opens a popover whose one item sends add_origin_connector for it', async () => {
  const { store, edits } = await open()
  longPress(row('I2'))
  const menu = await screen.findByRole('menu', { name: /menu$/ })
  const items = within(menu).getAllByRole('menuitem')
  expect(items.map((i) => i.textContent)).toEqual(['Origin connector'])
  fireEvent.click(items[0]!)
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]!.body).toEqual({ op: 'add_origin_connector', instance: 'I2' })
  await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
  store.stop()
})

it('a read-only Document opens no popover', async () => {
  const { store, edits } = await open(true)
  longPress(row('I2'))
  expect(screen.queryByRole('menu')).toBeNull()
  expect(edits().length).toBe(0)
  store.stop()
})
