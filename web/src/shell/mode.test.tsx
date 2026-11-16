import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ConnectionStore, type SocketLike } from '../net/connection'
import { initialMode, MODE_KEY } from './mode'
import { Shell } from './Shell'

class Sock implements SocketLike {
  readyState = 1
  onopen: SocketLike['onopen'] = null
  onmessage: SocketLike['onmessage'] = null
  onclose: SocketLike['onclose'] = null
  onerror: SocketLike['onerror'] = null
  send() {}
  close() {}
}

const media = (matches: boolean) => vi.fn((q: string) => ({ matches: matches && q === '(pointer: fine) and (hover: hover)' }))

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('fetch', async () => new Response('', { status: 404 }))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

function mount() {
  const store = new ConnectionStore({ clientId: 'abcdefgh', url: 'ws://t/ws', createSocket: () => new Sock(), visible: () => true, pingMs: 3_600_000, backoff: () => 3_600_000 })
  store.start()
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  return store
}

it('with nothing stored, a fine pointer with hover gives desktop, anything else tablet', () => {
  vi.stubGlobal('matchMedia', media(true))
  expect(initialMode()).toBe('desktop')
  vi.stubGlobal('matchMedia', media(false))
  expect(initialMode()).toBe('tablet')
  vi.stubGlobal('matchMedia', undefined)
  expect(initialMode()).toBe('tablet')
})

it('a stored mode wins over the pointer test', () => {
  vi.stubGlobal('matchMedia', media(true))
  window.localStorage.setItem(MODE_KEY, 'tablet')
  expect(initialMode()).toBe('tablet')
  vi.stubGlobal('matchMedia', media(false))
  window.localStorage.setItem(MODE_KEY, 'desktop')
  expect(initialMode()).toBe('desktop')
})

it('a storage that throws leaves the toggle working for the session', () => {
  vi.stubGlobal('matchMedia', media(true))
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked')
  })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked')
  })
  const store = mount()
  expect(screen.getByTestId('shell').dataset.mode).toBe('desktop')
  act(() => screen.getByRole('button', { name: 'Tablet' }).click())
  expect(screen.getByTestId('shell').dataset.mode).toBe('tablet')
  act(() => screen.getByRole('button', { name: 'Desktop' }).click())
  expect(screen.getByTestId('shell').dataset.mode).toBe('desktop')
  store.stop()
})

it('the toggle changes the mode, stores it and keeps the viewport element', () => {
  vi.stubGlobal('matchMedia', media(true))
  const store = mount()
  const shell = screen.getByTestId('shell')
  const viewport = screen.getByTestId('viewport')
  const canvas = viewport.querySelector('canvas')
  expect(shell.dataset.mode).toBe('desktop')
  expect(screen.getByRole('button', { name: 'Desktop' }).getAttribute('aria-pressed')).toBe('true')
  act(() => screen.getByRole('button', { name: 'Tablet' }).click())
  expect(window.localStorage.getItem(MODE_KEY)).toBe('tablet')
  expect(screen.getByTestId('shell')).toBe(shell)
  expect(shell.dataset.mode).toBe('tablet')
  expect(screen.getByTestId('viewport')).toBe(viewport)
  expect(viewport.querySelector('canvas')).toBe(canvas)
  expect(screen.getByRole('button', { name: 'Tablet' }).getAttribute('aria-pressed')).toBe('true')
  store.stop()
})
