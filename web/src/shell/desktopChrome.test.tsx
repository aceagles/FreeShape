import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ConnectionStore, type SocketLike } from '../net/connection'
import { MENU_BAR, documentMenu, viewMenu, DESKTOP_ONLY } from './menus'
import type { MenuItem } from './menus'
import { MenuButton } from './Menu'
import { LEFT_WIDTH_KEY } from './layout'
import { MODE_KEY } from './mode'
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
const tab = (id: string, kind: string, name: string) => ({ id, kind, name, state: 'ok', error: null, dirty: false, revision: 1 })
const hello = { type: 'hello', client: 'c', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'assembly', 'Assembly 1')] }

let calls: { method: string; url: string }[] = []
beforeEach(() => {
  window.localStorage.clear()
  window.localStorage.setItem(MODE_KEY, 'desktop')
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 })
  calls = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? 'GET', url: String(url) })
    const mine = init?.method !== undefined && init.method !== 'GET'
    return mine ? new Response(JSON.stringify({ tabs: [], tab: { id: 'T3' }, deleted: 'T1' }), { status: 200, headers: { 'content-type': 'application/json' } }) : new Response('', { status: 404 })
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

function mount() {
  const sock = new Sock()
  const store = new ConnectionStore({ clientId: 'abcdefgh', url: 'ws://t/ws', createSocket: () => sock, visible: () => true, pingMs: 3_600_000, backoff: () => 3_600_000 })
  store.start()
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  act(() => sock.onmessage?.({ data: JSON.stringify(hello) }))
  return store
}
const menuNames = () => within(screen.getByRole('menu')).getAllByRole('menuitem').map((b) => b.textContent!.replace(/(Ctrl|Alt)\+.*|Shift\+\d|^F$|^P$/, ''))

it('Desktop mode: the top bar has the three menu buttons and no tab bar; the tabs are in the bottom strip', () => {
  mount()
  const bar = document.querySelector('[data-region="top-bar"]') as HTMLElement
  for (const n of ['Document', 'View', 'Select']) expect(within(bar).getByRole('button', { name: n })).toBeTruthy()
  expect(bar.querySelector('nav')).toBeNull()
  const strip = document.querySelector('[data-region="tab-strip"]') as HTMLElement
  expect(within(strip).getByRole('button', { name: 'Assembly 1' })).toBeTruthy()
  expect(within(strip).getByRole('button', { name: 'Add tab' })).toBeTruthy()
  expect(strip.textContent).toContain('Units: millimetre')
  expect(within(bar).getByRole('button', { name: 'Search tools' }).hasAttribute('disabled')).toBe(false)
  expect(within(bar).getByRole('button', { name: 'Keyboard shortcuts' }).hasAttribute('disabled')).toBe(false)
})

it('the Document menu lists the desktop items in order; wired items run, others are disabled with the tooltip', () => {
  mount()
  act(() => screen.getByRole('button', { name: 'Document' }).click())
  expect(menuNames().slice(0, 9)).toEqual(['New Part Studio', 'New Assembly', 'New Drawing…', 'Open…', 'New Document folder…', 'Open Document folder…', 'Add files to Document…', 'Open recent', 'Close tab'])
  expect(menuNames().length).toBe(documentMenu.filter((e) => e.kind !== 'separator').length)
  const drawing = screen.getByRole('menuitem', { name: 'New Drawing…' }) as HTMLButtonElement
  expect(drawing.disabled).toBe(true)
  expect(drawing.title).toBe(DESKTOP_ONLY)
  expect((screen.getByRole('menuitem', { name: /^Save(?!\sas)/ }) as HTMLButtonElement).disabled).toBe(false)

  act(() => screen.getByRole('menuitem', { name: /^New Part Studio/ }).click())
  expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/documents/d/tabs'))).toBe(true)
  act(() => screen.getByRole('button', { name: 'Document' }).click())
  act(() => screen.getByRole('menuitem', { name: /^New Assembly/ }).click())
  expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/documents/d/tabs')).length).toBe(2)
  act(() => screen.getByRole('button', { name: 'Document' }).click())
  act(() => screen.getByRole('menuitem', { name: /^SaveCtrl\+S/ }).click())
  expect(calls.some((c) => c.method === 'POST' && c.url.includes('save'))).toBe(true)
  act(() => screen.getByRole('button', { name: 'Document' }).click())
  act(() => screen.getByRole('menuitem', { name: /^Close tab/ }).click())
  act(() => screen.getByRole('button', { name: 'Delete' }).click())
  expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/tabs/T1'))).toBe(true)
})

it('the View menu keeps its separators; the Select items are wired (I9.14) and the other toggles are disabled', () => {
  expect(viewMenu.filter((e) => e.kind === 'separator').length).toBe(2)
  expect(MENU_BAR.map((m) => m.label)).toEqual(['Document', 'View', 'Select'])
  mount()
  act(() => screen.getByRole('button', { name: 'Select' }).click())
  for (const b of screen.getAllByRole('menuitemcheckbox') as HTMLButtonElement[]) expect(b.disabled).toBe(false)
  act(() => screen.getByRole('button', { name: 'Select' }).click())
  act(() => screen.getByRole('button', { name: 'View' }).click())
  expect((screen.getByRole('menuitem', { name: /^Front/ }) as HTMLButtonElement).disabled).toBe(false)
  expect((screen.getByRole('menuitemcheckbox', { name: /Shiny/ }) as HTMLButtonElement).title).toBe(DESKTOP_ONLY)
})

function standalone() {
  const run = vi.fn()
  const item = (command: string, label: string): MenuItem => ({ command, label })
  render(
    <div>
      <button type="button">outside</button>
      <MenuButton label="Test" entries={[item('t.a', 'A'), { kind: 'separator' }, item('t.off', 'Off'), item('t.b', 'B')]} commands={{ 't.a': () => run('a'), 't.b': () => run('b') }} state={{ readOnly: false, tabKind: null }} />
    </div>,
  )
  return run
}

it('a menu opens on click, moves with the arrows, runs on Enter and closes on Esc and on a click outside', () => {
  const run = standalone()
  expect(screen.queryByRole('menu')).toBeNull()
  act(() => screen.getByRole('button', { name: 'Test' }).click())
  const menu = screen.getByRole('menu')
  expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'A' }))
  fireEvent.keyDown(menu, { key: 'ArrowDown' })
  // the disabled item is skipped
  expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'B' }))
  fireEvent.keyDown(menu, { key: 'ArrowDown' })
  expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: 'A' }))
  fireEvent.keyDown(menu, { key: 'ArrowUp' })
  fireEvent.keyDown(menu, { key: 'Enter' })
  expect(run).toHaveBeenCalledWith('b')
  expect(screen.queryByRole('menu')).toBeNull()

  act(() => screen.getByRole('button', { name: 'Test' }).click())
  fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
  expect(screen.queryByRole('menu')).toBeNull()
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Test' }))

  act(() => screen.getByRole('button', { name: 'Test' }).click())
  fireEvent.pointerDown(screen.getByRole('button', { name: 'outside' }))
  expect(screen.queryByRole('menu')).toBeNull()
  act(() => screen.getByRole('button', { name: 'Test' }).click())
  fireEvent.mouseDown(document.body)
  expect(screen.queryByRole('menu')).toBeNull()
})

const column = () => screen.getByTestId('left-column')

it('dragging the splitter resizes the left column within 200 px and half the window, stores it, and a new shell reads it', () => {
  mount()
  expect(column().style.width).toBe('270px')
  const bar = screen.getByTestId('splitter')
  fireEvent.pointerDown(bar, { pointerId: 1, button: 0, clientX: 270 })
  fireEvent.pointerMove(bar, { pointerId: 1, clientX: 400 })
  expect(column().style.width).toBe('400px')
  expect(window.localStorage.getItem(LEFT_WIDTH_KEY)).toBe('400')
  fireEvent.pointerMove(bar, { pointerId: 1, clientX: 5 })
  expect(column().style.width).toBe('200px')
  fireEvent.pointerMove(bar, { pointerId: 1, clientX: 2000 })
  expect(column().style.width).toBe('720px')
  fireEvent.pointerMove(bar, { pointerId: 1, clientX: 340 })
  fireEvent.pointerUp(bar, { pointerId: 1 })
  fireEvent.pointerMove(bar, { pointerId: 1, clientX: 900 })
  expect(column().style.width).toBe('340px')
  cleanup()
  mount()
  expect(column().style.width).toBe('340px')
})

it('an open feature dialog floats at (12, 12) in the viewport and the left column stays', () => {
  mount()
  act(() => window.__freeshape?.shell.openDialog('Extrude 1'))
  const dialog = screen.getByRole('dialog', { name: 'Extrude 1' })
  expect(screen.getByTestId('shell-viewport').contains(dialog)).toBe(true)
  expect(column().contains(dialog)).toBe(false)
  expect(dialog.style.left).toBe('12px')
  expect(dialog.style.top).toBe('12px')
  expect(dialog.style.width).toBe('300px')
  expect(screen.getByTestId('left-column').querySelector('.panel, [data-testid="panel-rail"]')).not.toBeNull()
})
