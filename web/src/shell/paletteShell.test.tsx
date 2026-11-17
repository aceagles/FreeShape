import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openedMessage } from '../sketch/testFixture'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { keyOf } from '../test/keys'
import { FEATURE_STRIP, toolIds } from '../tools/defs'
import { MODE_KEY } from './mode'
import { Shell } from './Shell'

// The Search tools palette through the whole Shell (card I9.11).
vi.mock('three', async (orig) => {
  const three = await orig<typeof import('three')>()
  class FakeRenderer {
    setClearColor() {}
    setPixelRatio() {}
    setSize() {}
    render() {}
    dispose() {}
  }
  return { ...three, WebGLRenderer: FakeRenderer }
})

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

// every type can start but Sweep, which waits for a selection (dimmed)
const typeRow = (type: string) => ({ type, label: type, standard: true, can_start: type !== 'sweep' })
const created = (type: string) => ({
  dialog: 'did9',
  document: 'd',
  tab: 'T1',
  feature: null,
  type,
  name: `${type} 1`,
  revision: 1,
  description: { type, custom: [], fields: [{ key: 'depth', kind: 'num', standard: true, visible: true, label: 'Depth', unit: 'mm', min: null, signed: false }] },
  values: { depth: { value: 10, text: '10' } },
  fields: {},
  active: null,
})

async function shell(mode: 'desktop' | 'tablet' = 'desktop') {
  window.localStorage.setItem(MODE_KEY, mode)
  const { bytes } = fixture('bracket')
  const calls = stubFetch((c: Call) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.includes('/features/types')) return { json: { document: 'd', tab: 'T1', revision: 1, types: toolIds(FEATURE_STRIP).map(typeRow) } }
    if (c.method === 'POST' && c.url.endsWith('/features/dialog')) return { status: 201, json: created((c.body as { type: string }).type) }
    if (c.url.endsWith('/cancel')) return { json: { cancelled: true } }
    if (c.method === 'GET' && c.url.endsWith('/documents/d/tabs')) return { json: { tabs: [tab('T1', 'studio', 'Part Studio 1')] } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={vi.fn()} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  if (mode === 'desktop') await waitFor(() => expect(document.querySelector('[data-tool="extrude"]')?.getAttribute('data-state')).toBe('ok'))
  return { store, sock, calls }
}

const key = (chord: string, target: EventTarget = window) => act(() => void target.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...keyOf(chord) })))
const palette = () => screen.queryByTestId('palette')
const box = () => screen.getByRole('combobox', { name: 'Search tools' }) as HTMLInputElement
const type = (text: string) => fireEvent.change(box(), { target: { value: text } })
const names = () => within(screen.getByTestId('palette-list')).getAllByRole('option').map((o) => o.querySelector('.palette-name')!.textContent)
const dialogPosts = (calls: Call[]) => calls.filter((c) => c.method === 'POST' && c.url.endsWith('/features/dialog'))
const sketchOpened = async (sock: Awaited<ReturnType<typeof shell>>['sock']) => {
  sock.say({ ...openedMessage(), document: 'd', tab: 'T1' })
  await waitFor(() => expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull())
}

describe('the Search tools palette in Desktop mode', () => {
  it('Ctrl+Space opens it with the box focused; ext lists Extrude first; Enter starts Extrude', async () => {
    const { store, calls } = await shell()
    expect(palette()).toBeNull()
    key('Ctrl+Space')
    expect(palette()).not.toBeNull()
    expect(document.activeElement).toBe(box())
    type('ext')
    expect(names()[0]).toBe('Extrude')
    fireEvent.keyDown(box(), { key: 'Enter' })
    expect(palette()).toBeNull()
    await waitFor(() => expect(dialogPosts(calls).map((c) => (c.body as { type: string }).type)).toEqual(['extrude']))
    store.stop()
  })

  it('Alt+C, Alt+` and the Search tools button open it too, and the button closes it again', async () => {
    const { store } = await shell()
    key('Alt+C')
    expect(palette()).not.toBeNull()
    fireEvent.keyDown(box(), { key: 'Escape' })
    expect(palette()).toBeNull()
    key('Alt+`')
    expect(palette()).not.toBeNull()
    fireEvent.keyDown(box(), { key: 'Escape' })
    const button = within(document.querySelector<HTMLElement>('[data-region="top-bar"]')!).getByRole('button', { name: 'Search tools' })
    fireEvent.click(button)
    expect(palette()).not.toBeNull()
    fireEvent.click(button)
    expect(palette()).toBeNull()
    store.stop()
  })

  it('Esc closes it without running anything; the keys stand aside while the box has the focus', async () => {
    const { store, calls } = await shell()
    key('Ctrl+Space')
    type('ext')
    key('Shift+E', box())
    expect(palette()).not.toBeNull()
    key('Escape', box())
    expect(palette()).toBeNull()
    expect(dialogPosts(calls)).toEqual([])
    store.stop()
  })

  it('Up and Down move the choice and wrap; Enter runs the one chosen', async () => {
    const { store, calls } = await shell()
    key('Ctrl+Space')
    type('fil')
    expect(names().slice(0, 2)).toEqual(['Fillet', 'Fill'])
    fireEvent.keyDown(box(), { key: 'ArrowDown' })
    expect(screen.getByRole('option', { selected: true }).textContent).toContain('Fill')
    fireEvent.keyDown(box(), { key: 'ArrowUp' })
    fireEvent.keyDown(box(), { key: 'ArrowUp' })
    expect(screen.getByRole('option', { selected: true }).textContent).toContain(names().at(-1)!)
    fireEvent.keyDown(box(), { key: 'ArrowDown' })
    fireEvent.keyDown(box(), { key: 'ArrowDown' })
    fireEvent.keyDown(box(), { key: 'Enter' })
    await waitFor(() => expect(dialogPosts(calls).map((c) => (c.body as { type: string }).type)).toEqual(['fill']))
    store.stop()
  })

  it('lists each entry with its chord, and a menu command beside the tools', async () => {
    const { store } = await shell()
    key('Ctrl+Space')
    type('extrude')
    expect(within(screen.getByRole('option', { name: /^Extrude/ })).getByText('Shift+E')).toBeTruthy()
    type('save')
    expect(screen.getByRole('option', { name: /^Save(?! Document archive)\b/ }).textContent).toContain('Ctrl+S')
    type('xyzzy')
    expect(screen.getByTestId('palette-about').textContent).toContain('No tool found')
    store.stop()
  })

  it('with a sketch open it lists the sketch tools, not the feature tools', async () => {
    const { store, sock } = await shell()
    await sketchOpened(sock)
    key('Ctrl+Space')
    type('')
    const all = names()
    expect(all.slice(0, 3)).toEqual(['Undo', 'Redo', 'Select'])
    expect(all).toContain('Line')
    expect(all).toContain('Finish sketch')
    expect(all).not.toContain('Extrude')
    type('lin')
    expect(names()[0]).toBe('Line')
    store.stop()
  })

  it('Esc closes the palette and leaves the sketch open; the next Esc steps back in it', async () => {
    const { store, sock } = await shell()
    await sketchOpened(sock)
    key('Ctrl+Space')
    const sent = sock.sent.length
    key('Escape', box())
    expect(palette()).toBeNull()
    expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull()
    expect(sock.sent.slice(sent)).toEqual([])
    // with the box gone the same key steps back in the sketch (an empty sketch is left)
    key('Escape')
    await waitFor(() => expect(sock.sent.slice(sent).length).toBeGreaterThan(0))
    store.stop()
  })

  it('a sketch tool picked in the palette becomes the sketch tool', async () => {
    const { store, sock } = await shell()
    await sketchOpened(sock)
    key('Ctrl+Space')
    type('circle')
    fireEvent.keyDown(box(), { key: 'Enter' })
    expect(palette()).toBeNull()
    await waitFor(() => expect(document.querySelector('[data-strip="sketch"] [data-tool="circle"]')?.getAttribute('aria-pressed')).toBe('true'))
    store.stop()
  })

  it('a tool that cannot run is listed dimmed with its reason, and Enter on it does nothing', async () => {
    const { store, calls } = await shell()
    key('Ctrl+Space')
    // Derived is a dialog the client cannot draw: the toolbar's "Desktop only for now"
    type('derived')
    const row = screen.getByRole('option', { name: /^Derived/ })
    expect(row.getAttribute('aria-disabled')).toBe('true')
    expect(row.getAttribute('title')).toBe('Desktop only for now')
    expect(screen.getByTestId('palette-about').textContent).toContain('Desktop only for now')
    fireEvent.keyDown(box(), { key: 'Enter' })
    expect(palette()).not.toBeNull()
    fireEvent.click(row)
    // Sweep waits for a selection: the toolbar's dimmed state, with its reason
    type('sweep')
    expect(screen.getByRole('option', { name: /^Sweep/ }).getAttribute('aria-disabled')).toBe('true')
    fireEvent.keyDown(box(), { key: 'Enter' })
    expect(palette()).not.toBeNull()
    // a menu command the client does not have carries the menu's own tooltip
    type('new drawing')
    expect(screen.getByRole('option', { name: /^New Drawing/ }).getAttribute('title')).toBe('Desktop only for now')
    expect(dialogPosts(calls)).toEqual([])
    store.stop()
  })

  it('the feature tools are listed as off, with the reason, while a feature dialog is open', async () => {
    const { store, calls } = await shell()
    key('Shift+E')
    await waitFor(() => expect(dialogPosts(calls).length).toBe(1))
    key('Ctrl+Space')
    type('revolve')
    const row = screen.getByRole('option', { name: /^Revolve/ })
    expect(row.getAttribute('aria-disabled')).toBe('true')
    expect(row.getAttribute('title')).toContain('Finish or cancel')
    store.stop()
  })
})

describe('Tablet mode', () => {
  it('has no Search tools button and Ctrl+Space, Alt+C and Alt+` open nothing', async () => {
    const { store } = await shell('tablet')
    for (const chord of ['Ctrl+Space', 'Alt+C', 'Alt+`']) key(chord)
    expect(palette()).toBeNull()
    expect(screen.queryByRole('button', { name: 'Search tools' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Keyboard shortcuts' })).toBeNull()
    store.stop()
  })
})

describe('the shortcut list', () => {
  it('the ? button and the Document menu item open it; Close and Esc close it', async () => {
    const { store } = await shell()
    const bar = within(document.querySelector<HTMLElement>('[data-region="top-bar"]')!)
    fireEvent.click(bar.getByRole('button', { name: 'Keyboard shortcuts' }))
    const dialog = screen.getByRole('dialog', { name: 'Keyboard shortcuts' })
    expect(within(dialog).getByRole('region', { name: 'Part Studio' })).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog', { name: 'Keyboard shortcuts' })).toBeNull()
    fireEvent.click(bar.getByRole('button', { name: 'Document' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Keyboard shortcuts' }))
    expect(screen.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeTruthy()
    key('Escape')
    expect(screen.queryByRole('dialog', { name: 'Keyboard shortcuts' })).toBeNull()
    store.stop()
  })
})
