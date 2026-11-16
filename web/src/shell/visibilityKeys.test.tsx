import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openedMessage } from '../sketch/testFixture'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { keyOf } from '../test/keys'
import { MODE_KEY } from './mode'
import { Shell } from './Shell'

// The visibility keys through the whole Shell (card I9.8): P and Y reach the 3D view and the server, the View menu's tick follows,
// and Tablet mode and an open sketch leave the keys alone.
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

async function shell(mode: 'desktop' | 'tablet' = 'desktop') {
  window.localStorage.setItem(MODE_KEY, mode)
  const { bytes, outputs } = fixture('bracket')
  const calls = stubFetch((c: Call) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.method === 'POST' && c.url.endsWith('/tabs/T1/edit')) return { json: { rebuilt: ['T1'] } }
    if (c.method === 'GET' && c.url.endsWith('/documents/d/tabs')) return { json: { tabs: [tab('T1', 'studio', 'Part Studio 1')] } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={vi.fn()} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(outputs.revision))
  return { sock, calls, part: outputs.parts[0]!.id }
}

const key = (chord: string) => act(() => void window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...keyOf(chord) })))
const planes = () => window.__freeshapeViewport?.planes
const edits = (calls: Call[]) => calls.filter((c) => c.method === 'POST' && c.url.endsWith('/tabs/T1/edit')).map((c) => c.body)
const planesTick = (): string | null => {
  fireEvent.click(screen.getByRole('button', { name: 'View' }))
  const item = screen.getByRole('menuitemcheckbox', { name: /Show planes/ })
  const checked = item.getAttribute('aria-checked')
  fireEvent.keyDown(screen.getByRole('menu', { name: 'View' }), { key: 'Escape' })
  return checked
}

describe('the visibility keys in Desktop mode', () => {
  it('P hides the planes and P again shows them; the View menu tick follows', async () => {
    await shell()
    expect(planes()).toBe(3)
    expect(planesTick()).toBe('true')
    key('P')
    await waitFor(() => expect(planes()).toBe(0))
    expect(planesTick()).toBe('false')
    key('P')
    await waitFor(() => expect(planes()).toBe(3))
    expect(planesTick()).toBe('true')
  })

  it('the View menu\'s Show planes item does what P does', async () => {
    await shell()
    fireEvent.click(screen.getByRole('button', { name: 'View' }))
    fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /Show planes/ }))
    await waitFor(() => expect(planes()).toBe(0))
    expect(planesTick()).toBe('false')
  })

  it('Y hides the selected part on the server; the pointer decides when nothing is selected', async () => {
    const { calls, part } = await shell()
    act(() => window.__freeshape!.shell.selection.set([{ id: `part|${part}`, kind: 'part', part, index: -1, name: null, key: null }]))
    key('Y')
    await waitFor(() => expect(edits(calls)).toEqual([{ op: 'set_part_override', part, hidden: true }]))
    act(() => window.__freeshape!.shell.selection.set([]))
    act(() => window.__freeshape!.shell.selection.aim({ id: `part|${part}`, kind: 'part', part, index: -1, name: null, key: null }))
    key('Y')
    await waitFor(() => expect(edits(calls)).toHaveLength(2))
  })

  it('Shift+I and Shift+T act on the aimed part', async () => {
    const { part } = await shell()
    act(() => window.__freeshape!.shell.selection.aim({ id: `part|${part}`, kind: 'part', part, index: -1, name: null, key: null }))
    key('Shift+T')
    await waitFor(() => expect(window.__freeshapeViewport?.transparentParts).toBe(1))
    key('Shift+I')
    await waitFor(() => expect(window.__freeshapeViewport?.visibleParts).toBe(1))
    key('Shift+I')
    key('Shift+T')
    await waitFor(() => expect(window.__freeshapeViewport?.transparentParts).toBe(0))
  })

  it('no key acts with a sketch open', async () => {
    const { sock, calls } = await shell()
    sock.say({ ...openedMessage(), document: 'd', tab: 'T1' })
    await waitFor(() => expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull())
    key('P')
    key('Shift+P')
    key('Y')
    key('Shift+I')
    key('Shift+T')
    expect(planes()).toBe(3)
    expect(edits(calls)).toEqual([])
  })
})

describe('Tablet mode', () => {
  it('does not take the visibility keys', async () => {
    const { calls, part } = await shell('tablet')
    act(() => window.__freeshape!.shell.selection.aim({ id: `part|${part}`, kind: 'part', part, index: -1, name: null, key: null }))
    key('P')
    key('Y')
    key('Shift+T')
    expect(planes()).toBe(3)
    expect(edits(calls)).toEqual([])
    expect(window.__freeshapeViewport?.transparentParts).toBe(0)
  })
})
