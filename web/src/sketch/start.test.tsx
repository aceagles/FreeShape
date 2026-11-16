import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LONG_PRESS_MS } from '../shell/longPress'
import { Shell } from '../shell/Shell'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { toolIds, FEATURE_STRIP } from '../tools/defs'
import { openedMessage } from './testFixture'
import { sketchSite } from './start'

// Starting a sketch, editing one and leaving it (card I7.13): the Sketch tool in the feature strip, Edit on a sketch
// feature, Done and Cancel. The session itself is the server's; these check what the client sends and how the screen follows.
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

beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} }))
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const typeRow = (type: string) => ({ type, label: type, standard: true, can_start: true })

async function shell(extra: (c: Call) => { status?: number; json?: unknown } | undefined = () => undefined) {
  const { bytes } = fixture('bracket')
  const calls = stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.includes('/features/types')) return { json: { document: 'd', tab: 'T1', revision: 1, types: toolIds(FEATURE_STRIP).map(typeRow) } }
    return extra(c)
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  return { store, sock, calls }
}

const sketchTool = () => document.querySelector<HTMLElement>('[data-tool="sketch"]')!
const planeItem = (key: string) => ({ id: `plane|${key}`, kind: 'plane' as const, part: null, index: -1, name: null, key })
const opened = (sock: Awaited<ReturnType<typeof shell>>['sock'], extra: Record<string, unknown> = {}) => {
  sock.say({ ...openedMessage(), document: 'd', tab: 'T1', ...extra })
  return waitFor(() => expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull())
}

it('with the Top plane selected, the Sketch tool opens a new sketch on that plane', async () => {
  const { store, sock } = await shell()
  act(() => window.__freeshape!.shell.selection.set([planeItem('Top')]))
  fireEvent.click(sketchTool())
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.open', document: 'd', tab: 'T1', plane: { kind: 'std', name: 'Top' } })
  // the selection went with it, and the sketch strip replaces the feature strip once the session answers
  await waitFor(() => expect(window.__freeshape!.shell.selection.getState().items).toEqual([]))
  await opened(sock, { plane: { kind: 'std', name: 'Top' } })
  expect(document.querySelector('[data-strip="feature"]')).toBeNull()
  store.stop()
})

it('with nothing selected, the Sketch tool asks for a plane and the default planes are its buttons', async () => {
  const { store, sock } = await shell()
  fireEvent.click(sketchTool())
  expect(sock.sent.filter((m) => m['type'] === 'sketch.open')).toEqual([])
  const dialog = await screen.findByRole('dialog', { name: 'Sketch on which plane?' })
  expect([...dialog.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['Cancel', 'Top', 'Front', 'Right'])
  fireEvent.click(screen.getByRole('button', { name: 'Front' }))
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.open', document: 'd', tab: 'T1', plane: { kind: 'std', name: 'Front' } })
  expect(screen.queryByRole('dialog', { name: 'Sketch on which plane?' })).toBeNull()
  store.stop()
})

it('with a face selected, the Sketch tool asks the refs route for its plane and opens the sketch on it', async () => {
  const face = { part: 'F2/1', kind: 'face', index: 4, want: ['plane'] }
  const ref = { kind: 'plane', face: { part: 'F2/1', name: 'Face4' } }
  const { store, sock, calls } = await shell((c) => (c.url.endsWith('/tabs/T1/refs') ? { json: { items: [{ ok: true, kind: 'plane', ref, name: 'Face4' }] } } : undefined))
  act(() => window.__freeshape!.shell.selection.set([{ id: 'face|F2/1|Face4', kind: 'face', part: 'F2/1', index: 3, name: 'Face4', key: null }]))
  fireEvent.click(sketchTool())
  await waitFor(() => expect(sock.sent.at(-1)).toEqual({ type: 'sketch.open', document: 'd', tab: 'T1', plane: { kind: 'face', face: ref } }))
  expect(calls.find((c) => c.url.endsWith('/tabs/T1/refs'))?.body).toEqual({ picks: [face] })
  store.stop()
})

it('a face that is not flat is refused with the server\'s reason and no sketch opens', async () => {
  const { store, sock } = await shell((c) =>
    c.url.endsWith('/tabs/T1/refs') ? { json: { items: [{ ok: false, code: 'not_flat', reason: 'That face is curved, so a sketch cannot lie on it.' }] } } : undefined,
  )
  act(() => window.__freeshape!.shell.selection.set([{ id: 'face|F2/1|Face4', kind: 'face', part: 'F2/1', index: 3, name: 'Face4', key: null }]))
  fireEvent.click(sketchTool())
  await screen.findByText('That face is curved, so a sketch cannot lie on it.')
  expect(sock.sent.filter((m) => m['type'] === 'sketch.open')).toEqual([])
  store.stop()
})

it('picks of a studio plane and of a face become the sketch\'s plane reference; anything else is not a place', () => {
  const item = (o: object) => ({ id: 'x', kind: 'face', part: null, index: -1, name: null, key: null, ...o }) as never
  expect(sketchSite([item({ kind: 'plane', key: 'Right' })])).toEqual({ plane: { kind: 'std', name: 'Right' } })
  expect(sketchSite([item({ kind: 'plane', key: 'F7' })])).toEqual({ plane: { kind: 'plane', fid: 'F7' } })
  expect(sketchSite([item({ kind: 'face', part: 'F2/1', index: 0 })])).toEqual({ face: { part: 'F2/1', kind: 'face', index: 1, want: ['plane'] } })
  expect(sketchSite([])).toBeNull()
  expect(sketchSite([item({ kind: 'edge', part: 'F2/1', index: 0 }), item({ kind: 'plane', key: 'Top' })])).toBeNull()
  expect(sketchSite([item({ kind: 'axis', key: 'A1' })])).toBeNull()
})

/** Hold a row for 500 ms and release (as a finger does); its menu opens. */
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

it('Edit on a sketch feature opens that sketch with its feature id', async () => {
  const { store, sock } = await shell()
  longPress(screen.getByRole('button', { name: /Bracket profile/ }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Edit' }))
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.open', document: 'd', tab: 'T1', feature: 'F1' })
  await opened(sock, { feature: 'F1', name: 'Bracket profile' })
  store.stop()
})

it('Done sends sketch.commit and leaves sketch mode for the Part Studio', async () => {
  const { store, sock } = await shell()
  fireEvent.click(sketchTool())
  fireEvent.click(await screen.findByRole('button', { name: 'Top' }))
  await opened(sock)
  fireEvent.click(screen.getByTestId('sketch-done'))
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.commit' })
  sock.say({ type: 'sketch.committed', document: 'd', tab: 'T1', feature: 'F9', rebuilt: [], revision: 2 })
  await waitFor(() => expect(document.querySelector('[data-strip="feature"]')).not.toBeNull())
  expect(document.querySelector('[data-strip="sketch"]')).toBeNull()
  expect(window.__freeshapeViewport?.sketch).toBeNull()
  store.stop()
})

it('Cancel on a sketch with changes asks first; Keep sketching stays, Discard sends sketch.cancel', async () => {
  const { store, sock } = await shell()
  fireEvent.click(sketchTool())
  fireEvent.click(await screen.findByRole('button', { name: 'Top' }))
  await opened(sock)
  // nothing drawn yet: Cancel goes straight through
  fireEvent.click(screen.getByTestId('sketch-cancel'))
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.cancel' })
  sock.say({ type: 'sketch.cancelled', document: 'd', tab: 'T1', feature: null })
  await waitFor(() => expect(document.querySelector('[data-strip="feature"]')).not.toBeNull())

  fireEvent.click(sketchTool())
  fireEvent.click(await screen.findByRole('button', { name: 'Top' }))
  await opened(sock)
  // a step was made: the session has something to undo
  sock.say({ ...openedMessage(), type: 'sketch.changed', document: 'd', tab: 'T1', can_undo: true })
  await waitFor(() => expect(window.__freeshapeViewport?.sketch).not.toBeNull())
  const before = sock.sent.length
  fireEvent.click(screen.getByTestId('sketch-cancel'))
  await screen.findByRole('dialog', { name: 'Discard this sketch?' })
  expect(sock.sent.length).toBe(before)
  fireEvent.click(screen.getByRole('button', { name: 'Keep sketching' }))
  expect(screen.queryByRole('dialog', { name: 'Discard this sketch?' })).toBeNull()
  expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull()
  fireEvent.click(screen.getByTestId('sketch-cancel'))
  fireEvent.click(await screen.findByRole('button', { name: 'Discard' }))
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.cancel' })
  sock.say({ type: 'sketch.cancelled', document: 'd', tab: 'T1', feature: null })
  await waitFor(() => expect(document.querySelector('[data-strip="feature"]')).not.toBeNull())
  store.stop()
})

it('after Done or Cancel the view is as it was before the sketch opened, not square to the sketch plane and fitted to it', async () => {
  const { store, sock } = await shell()
  const before = window.__freeshapeViewport!.camera
  for (const end of ['done', 'cancel'] as const) {
    fireEvent.click(sketchTool())
    fireEvent.click(await screen.findByRole('button', { name: 'Front' }))
    await opened(sock)
    await waitFor(() => expect(window.__freeshapeViewport?.sketch?.tilt).toBeLessThan(1e-6))
    expect(window.__freeshapeViewport!.camera.back).not.toEqual(before.back)
    if (end === 'done') {
      fireEvent.click(screen.getByTestId('sketch-done'))
      sock.say({ type: 'sketch.committed', document: 'd', tab: 'T1', feature: 'F9', rebuilt: [], revision: 2 })
    } else {
      fireEvent.click(screen.getByTestId('sketch-cancel'))
      sock.say({ type: 'sketch.cancelled', document: 'd', tab: 'T1', feature: null })
    }
    await waitFor(() => expect(document.querySelector('[data-strip="feature"]')).not.toBeNull())
    const now = window.__freeshapeViewport!.camera
    expect(now.back).toEqual(before.back)
    expect(now.target).toEqual(before.target)
    expect(now.scale).toBe(before.scale)
  }
  store.stop()
})
