import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ConnectionStore, type SocketLike } from '../net/connection'
import { Shell } from './Shell'
import { LOST_AFTER_MS } from './status'
import { columnWidth, viewportWidth } from './layout'

class Sock implements SocketLike {
  readyState = 1
  onopen: SocketLike['onopen'] = null
  onmessage: SocketLike['onmessage'] = null
  onclose: SocketLike['onclose'] = null
  onerror: SocketLike['onerror'] = null
  send() {}
  close() {}
}

const tab = (id: string, kind: string, name: string) => ({ id, kind, name, state: 'ok', error: null, dirty: false, revision: 1 })
const hello = {
  type: 'hello',
  client: 'c',
  document: { id: 'd', name: 'Bracket' },
  tabs: [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'assembly', 'Assembly 1')],
}

function setup(width = 1024) {
  vi.stubGlobal('fetch', async () => new Response('', { status: 404 }))
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
  const sock = new Sock()
  const store = new ConnectionStore({ clientId: 'abcdefgh', url: 'ws://t/ws', createSocket: () => sock, visible: () => true, pingMs: 3_600_000, backoff: () => 3_600_000 })
  store.start()
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  const say = (m: object) => act(() => sock.onmessage?.({ data: JSON.stringify(m) }))
  return { store, sock, say }
}

beforeEach(() => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }))
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const columnPx = () => Number.parseFloat(screen.getByTestId('left-column').style.width)

it('collapsing the panel gives the viewport the freed width', () => {
  for (const [win, panel] of [[1024, 280], [1366, 320]] as const) {
    cleanup()
    const { store } = setup(win)
    expect(columnPx()).toBe(panel)
    const before = win - columnPx()
    act(() => screen.getByRole('button', { name: 'Collapse feature panel' }).click())
    expect(columnPx()).toBe(56)
    expect(win - columnPx()).toBe(before + panel - 56)
    expect(viewportWidth(win, true)).toBe(win - 56)
    expect(screen.getByTestId('panel-rail')).toBeTruthy()
    act(() => screen.getByRole('button', { name: 'Expand feature panel' }).click())
    expect(columnPx()).toBe(panel)
    store.stop()
  }
})

it('opening the dialog area does not change the viewport width', () => {
  setup(1024)
  const before = columnPx()
  act(() => window.__freeshape?.shell.openDialog('Extrude 1'))
  expect(screen.getByRole('dialog', { name: 'Extrude 1' })).toBeTruthy()
  expect(columnPx()).toBe(before)
  expect(columnWidth(1024, false)).toBe(before)
  // the dialog is inside the column, so it takes the panel's box and nothing from the viewport
  expect(screen.getByTestId('left-column').contains(screen.getByRole('dialog'))).toBe(true)
  act(() => screen.getByRole('button', { name: 'Close dialog' }).click())
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(columnPx()).toBe(before)
})

it('opening the dialog on a collapsed panel expands it first', () => {
  setup(1024)
  act(() => screen.getByRole('button', { name: 'Collapse feature panel' }).click())
  act(() => window.__freeshape?.shell.openDialog('Fillet 1'))
  expect(columnPx()).toBe(280)
})

it('shows the reconnecting state when the store reports disconnected, then lost', () => {
  const { say, sock } = setup()
  expect(screen.getByTestId('link-status').getAttribute('data-status')).toBe('reconnecting')
  say(hello)
  expect(screen.getByTestId('link-status').getAttribute('data-status')).toBe('connected')
  act(() => sock.onclose?.({}))
  const status = screen.getByTestId('link-status')
  expect(status.getAttribute('data-status')).toBe('reconnecting')
  expect(status.textContent).toBe('Reconnecting')
  act(() => {
    vi.advanceTimersByTime(LOST_AFTER_MS + 10)
  })
  expect(status.getAttribute('data-status')).toBe('lost')
  expect(status.textContent).toBe('Connection lost')
})

it('names the Document, lists the tabs with the current one marked, and switches', () => {
  const { say } = setup()
  say(hello)
  expect(screen.getByTestId('document-name').textContent).toBe('Bracket')
  const first = screen.getByRole('button', { name: 'Part Studio 1' })
  const second = screen.getByRole('button', { name: 'Assembly 1' })
  expect(first.getAttribute('aria-current')).toBe('page')
  expect(second.getAttribute('aria-current')).toBeNull()
  act(() => second.click())
  expect(second.getAttribute('aria-current')).toBe('page')
  expect(first.getAttribute('aria-current')).toBeNull()
  // the panel lists the open tab's features; an Assembly has its own panel (its outputs have not arrived here)
  expect(screen.getByTestId('assembly-panel').textContent).toContain('Loading the Assembly')
})

it('undo and redo are present and disabled while the tab has nothing to undo or redo; save and add tab are on', () => {
  setup()
  for (const name of ['Undo', 'Redo']) expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true)
  for (const name of ['Save', 'Add tab', 'Back to Documents']) expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(false)
})

it('the connection state opens and closes the debug panel', () => {
  const { say } = setup()
  say(hello)
  expect(screen.queryByTestId('debug-panel')).toBeNull()
  const button = screen.getByTestId('link-status')
  act(() => button.click())
  expect(screen.getByTestId('rtt-median')).toBeTruthy()
  expect(button.getAttribute('aria-expanded')).toBe('true')
  act(() => button.click())
  expect(screen.queryByTestId('debug-panel')).toBeNull()
})

it('has the layout regions', () => {
  setup()
  for (const name of ['top-bar', 'tool-strip', 'left-column', 'viewport', 'view-cube']) {
    expect(document.querySelector(`[data-region="${name}"]`), name).not.toBeNull()
  }
})
