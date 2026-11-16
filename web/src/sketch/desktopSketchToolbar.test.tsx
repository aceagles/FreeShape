import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { MODE_KEY } from '../shell/mode'
import { DESKTOP_ONLY } from '../shell/menus'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { desktopTip, desktopToolIds } from '../tools/defs'
import { DesktopToolbar } from '../tools/DesktopToolbar'
import { openedMessage } from './testFixture'
import { DESKTOP_SKETCH_TOOLBAR } from './strip'

// The Desktop-mode sketch toolbar (card I9.4): its order, its states, and the Shell flows (Line, Finish sketch, Cancel sketch).
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
  window.localStorage.clear()
})

/**
 * The order of `_build_sketch_toolbar` (freeshape/ui/mainwindow.py), read off its calls: a tool id per button, '|' for each
 * `h.addWidget(_sep())`, '>' where the stretch starts the right end. The desktop's names are mapped to the client's ids:
 * rect = rectangle, arc3 = arc, sk_fillet = corner_fillet, use = use_edge, import = insert_file, the Sketch's own
 * Construction = construct. Left out: the hidden "Both sides" toggle.
 */
const MAINWINDOW = [
  'undo', 'redo', '|',
  'select', 'line', 'rectangle', 'center_rectangle', 'circle', 'polygon', 'arc', 'arc_center', 'spline', 'ellipse', 'text', 'point', '|',
  'construct', 'dimension', '|',
  'coincident', 'horizontal', 'vertical', 'parallel', 'perpendicular', 'tangent', 'equal', 'midpoint', 'concentric', 'symmetric', 'fix', 'normal', '|',
  'corner_fillet', 'offset', 'slot', 'sk_mirror', 'sk_lpattern', 'sk_cpattern', '|',
  'use_edge', 'trim', 'extend', 'insert_file', 'delete',
  '>',
  'normal_to', 'fit', 'finish',
]
const rowOf = (): string[] =>
  [...document.querySelectorAll('.tb-scroll > *'), null, ...document.querySelectorAll('.tb-end > *')].map((e) => (e === null ? '>' : e.getAttribute('role') === 'separator' ? '|' : e.getAttribute('data-tool')!))
const button = (id: string) => document.querySelector(`.desktop-toolbar [data-tool="${id}"]`) as HTMLButtonElement

const sketchSent = (sock: { sent: unknown[] }) => sock.sent.filter((m) => (m as { type?: string }).type?.startsWith('sketch.'))

async function mountSketch() {
  window.localStorage.setItem(MODE_KEY, 'desktop')
  const { bytes } = fixture('bracket')
  stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.includes('/features/types')) return { json: { document: 'd', tab: 'T1', revision: 1, types: [] } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  act(() => window.__freeshape!.openSketch({ plane: { kind: 'std', name: 'Front' } }))
  sock.say({ ...openedMessage(), document: 'd', tab: 'T1' })
  await waitFor(() => expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull())
  return { store, sock }
}

it('the desktop sketch row lists its tools in the order of _build_sketch_toolbar with its separators, and ends with Normal to, Fit and Finish sketch', () => {
  render(<DesktopToolbar strip="sketch" layout={DESKTOP_SKETCH_TOOLBAR} stateOf={() => 'ok'} commands={{}} onStart={() => {}} />)
  expect(rowOf()).toEqual(MAINWINDOW)
  expect(desktopToolIds(DESKTOP_SKETCH_TOOLBAR)).toEqual(MAINWINDOW.filter((x) => x !== '|' && x !== '>'))
  const end = [...document.querySelectorAll('.tb-end > *')]
  expect(end.map((e) => e.textContent)).toEqual(['', '', '✓  Finish sketch'])
  expect(end[2]!.className).toContain('tb-accent')
})

it('the tooltips carry the desktop sketch keys', () => {
  const tips = Object.fromEntries([...DESKTOP_SKETCH_TOOLBAR.items, ...DESKTOP_SKETCH_TOOLBAR.trailing].flatMap((e) => (e.kind === 'separator' ? [] : [[e.tool.id, desktopTip(e)]])))
  expect(tips).toMatchObject({
    undo: 'Undo (Ctrl+Z)', select: 'Select (S)', line: 'Line (L)', rectangle: 'Corner rectangle (G)', center_rectangle: 'Center rectangle (R)', circle: 'Circle (C)',
    arc: '3-point arc (A)', point: 'Point (P)', construct: 'Construction (Q)', dimension: 'Dimension (D)', coincident: 'Coincident (I)', horizontal: 'Horizontal (H)',
    vertical: 'Vertical (V)', equal: 'Equal (E)', tangent: 'Tangent (T)', offset: 'Offset (O)', use_edge: 'Use (U)', delete: 'Delete (Del)', normal_to: 'Normal to (N)', fit: 'Zoom to fit (F)',
  })
})

it('Polygon, Spline and Insert DXF/SVG are disabled with the desktop-only note; Line, Corner rectangle and Circle choose their tool and show pressed', async () => {
  const { store, sock } = await mountSketch()
  for (const id of ['polygon', 'spline', 'insert_file', 'arc_center', 'ellipse', 'text', 'offset', 'slot', 'sk_lpattern', 'sk_cpattern', 'extend']) {
    expect(button(id).disabled).toBe(true)
    expect(button(id).title).toBe(DESKTOP_ONLY)
  }
  // every tool the client has is on
  for (const id of ['select', 'line', 'rectangle', 'center_rectangle', 'circle', 'arc', 'point', 'construct', 'dimension', 'coincident', 'normal', 'corner_fillet', 'use_edge', 'trim', 'sk_mirror', 'delete', 'normal_to', 'fit', 'finish']) {
    expect(button(id).disabled, id).toBe(false)
  }
  expect(button('select').getAttribute('aria-pressed')).toBe('true')
  for (const id of ['line', 'rectangle', 'circle']) {
    fireEvent.click(button(id))
    expect(button(id).getAttribute('aria-pressed')).toBe('true')
    expect(button('select').getAttribute('aria-pressed')).toBe('false')
  }
  // the tool that is on, pressed again, goes back to Select (F20); the Center rectangle is its own button
  fireEvent.click(button('rectangle'))
  expect(button('rectangle').getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(button('rectangle'))
  expect(button('rectangle').getAttribute('aria-pressed')).toBe('false')
  expect(button('select').getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(button('center_rectangle'))
  expect(button('center_rectangle').getAttribute('aria-pressed')).toBe('true')
  expect(button('rectangle').getAttribute('aria-pressed')).toBe('false')
  expect(sketchSent(sock)).toEqual([{ type: 'sketch.open', document: 'd', tab: 'T1', plane: { kind: 'std', name: 'Front' } }])
  store.stop()
})

it('Mirror is enabled and pressed while it is the tool; pressed again it goes back to Select; the two patterns stay disabled (I14.2)', async () => {
  const { store } = await mountSketch()
  expect(button('sk_mirror').disabled).toBe(false)
  expect(button('sk_mirror').getAttribute('aria-pressed')).toBe('false')
  fireEvent.click(button('sk_mirror'))
  expect(button('sk_mirror').getAttribute('aria-pressed')).toBe('true')
  expect(button('select').getAttribute('aria-pressed')).toBe('false')
  fireEvent.click(button('sk_mirror'))
  expect(button('sk_mirror').getAttribute('aria-pressed')).toBe('false')
  expect(button('select').getAttribute('aria-pressed')).toBe('true')
  for (const id of ['sk_lpattern', 'sk_cpattern']) {
    expect(button(id).disabled).toBe(true)
    expect(button(id).title).toBe(DESKTOP_ONLY)
  }
  store.stop()
})

it('Finish sketch sends sketch.commit', async () => {
  const { store, sock } = await mountSketch()
  fireEvent.click(button('finish'))
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.commit' })
  sock.say({ type: 'sketch.committed', document: 'd', tab: 'T1', feature: 'F9', rebuilt: [], revision: 2 })
  await waitFor(() => expect(document.querySelector('[data-strip="feature"]')).not.toBeNull())
  store.stop()
})

const openDocumentMenu = () => act(() => screen.getByRole('button', { name: 'Document' }).click())

it('Cancel sketch in the Document menu closes a sketch with no changes at once', async () => {
  const { store, sock } = await mountSketch()
  openDocumentMenu()
  const item = screen.getByRole('menuitem', { name: 'Cancel sketch' }) as HTMLButtonElement
  expect(item.disabled).toBe(false)
  act(() => item.click())
  expect(screen.queryByText('Discard this sketch?')).toBeNull()
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.cancel' })
  sock.say({ type: 'sketch.cancelled', document: 'd', tab: 'T1', feature: null })
  await waitFor(() => expect(document.querySelector('[data-strip="feature"]')).not.toBeNull())
  // with no sketch open the item is there and off
  openDocumentMenu()
  expect((screen.getByRole('menuitem', { name: 'Cancel sketch' }) as HTMLButtonElement).disabled).toBe(true)
  store.stop()
})

it('Cancel sketch asks first when the sketch has changes, and sends nothing until Discard', async () => {
  const { store, sock } = await mountSketch()
  const data = openedMessage().sketch
  sock.say({ type: 'sketch.changed', document: 'd', tab: 'T1', feature: null, name: 'Sketch 2', plane: {}, sketch: data, status: openedMessage().status, can_undo: true, can_redo: false })
  await waitFor(() => expect(button('undo').disabled).toBe(false))
  openDocumentMenu()
  act(() => screen.getByRole('menuitem', { name: 'Cancel sketch' }).click())
  const ask = await screen.findByText('Discard this sketch?')
  expect(sketchSent(sock).some((m) => (m as { type: string }).type === 'sketch.cancel')).toBe(false)
  act(() => within(ask.closest('[role="dialog"]') as HTMLElement).getByRole('button', { name: 'Discard' }).click())
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.cancel' })
  store.stop()
})

it('Undo sends sketch.undo, and a Constrain tool starts tool-first as the tablet strip does', async () => {
  const { store, sock } = await mountSketch()
  expect(button('undo').disabled).toBe(true)
  fireEvent.click(button('horizontal'))
  expect(button('horizontal').getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(button('horizontal'))
  expect(button('horizontal').getAttribute('aria-pressed')).toBe('false')
  expect(sock.sent.at(-1)).not.toEqual({ type: 'sketch.commit' })
  store.stop()
})
