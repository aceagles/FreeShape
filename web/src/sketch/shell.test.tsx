import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { openedMessage } from './testFixture'
import { stripWidth } from '../tools/defs'
import { SKETCH_STRIP, sketchToolState } from './strip'

// The sketch in the whole Shell (card I7.7): the sketch strip replaces the feature strip, the sketch is drawn from the
// session's answer, and the 2D / 3D toggle, Normal to, Cancel and Done talk to the server's sketch session.
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

async function shell() {
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
  return { store, sock }
}

const openSketch = async (sock: Awaited<ReturnType<typeof shell>>['sock']) => {
  act(() => window.__freeshape!.openSketch({ plane: { kind: 'std', name: 'Front' } }))
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.open', document: 'd', tab: 'T1', plane: { kind: 'std', name: 'Front' } })
  sock.say({ ...openedMessage(), document: 'd', tab: 'T1' })
  await waitFor(() => expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull())
}

it('opening a sketch swaps the feature strip for the sketch strip and draws the sketch over the view', async () => {
  const { store, sock } = await shell()
  expect(document.querySelector('[data-strip="feature"]')).not.toBeNull()
  await openSketch(sock)
  expect(document.querySelector('[data-strip="feature"]')).toBeNull()
  const labels = [...document.querySelectorAll('[data-strip="sketch"] > .strip-button .strip-label')].map((e) => e.textContent)
  expect(labels).toEqual(['Select', 'Draw', 'Dimension', 'Constrain', 'Construct', 'Delete'])
  // Select is on; Dimension, the drawing tools, Construct and Delete have their cards
  expect(document.querySelector('[data-tool="select"]')?.getAttribute('data-state')).toBe('ok')
  expect(document.querySelector('[data-tool="dimension"]')?.getAttribute('data-state')).toBe('ok')
  // Construct and Delete act on the selection (I7.9)
  expect(document.querySelector('[data-tool="construct"]')?.getAttribute('data-state')).toBe('ok')
  // the right end: 2D / 3D toggle (2D on), Normal to, Cancel, Done
  expect(screen.getByTestId('nav-2d').getAttribute('aria-pressed')).toBe('true')
  expect(screen.getByTestId('nav-3d').getAttribute('aria-pressed')).toBe('false')
  expect(screen.getByTestId('normal-to')).toBeTruthy()
  // the chip beside the selection chip says where the solve stands
  expect(screen.getByTestId('sketch-status').textContent).toContain('Sketch 2')
  expect(screen.getByTestId('sketch-status').textContent).toContain('6 items free')
  // the sketch is drawn over the canvas, and the view reports it
  await waitFor(() => expect(document.querySelectorAll('.sketch-layer path[data-kind="line"]').length).toBe(5))
  expect(window.__freeshapeViewport?.sketch?.nav).toBe('2d')
  expect(window.__freeshapeViewport?.sketch?.tilt).toBeLessThan(1e-6)
  store.stop()
})

it('the 2D / 3D toggle and Normal to steer the view; Cancel and Done send the session messages', async () => {
  const { store, sock } = await shell()
  await openSketch(sock)
  fireEvent.click(screen.getByTestId('nav-3d'))
  await waitFor(() => expect(window.__freeshapeViewport?.sketch?.nav).toBe('3d'))
  expect(screen.getByTestId('nav-3d').getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(screen.getByTestId('nav-2d'))
  await waitFor(() => expect(window.__freeshapeViewport?.sketch?.nav).toBe('2d'))
  fireEvent.click(screen.getByTestId('normal-to'))
  fireEvent.click(screen.getByTestId('sketch-done'))
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.commit' })
  sock.say({ type: 'sketch.committed', document: 'd', tab: 'T1', feature: 'F9', rebuilt: [], revision: 2 })
  await waitFor(() => expect(document.querySelector('[data-strip="feature"]')).not.toBeNull())
  // the sketch is gone from the view, and the part selection's pointer wiring is back
  expect(document.querySelector('.sketch-layer')).toBeNull()
  expect(window.__freeshapeViewport?.sketch).toBeNull()
  await openSketch(sock)
  fireEvent.click(screen.getByTestId('sketch-cancel'))
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.cancel' })
  sock.say({ type: 'sketch.cancelled', document: 'd', tab: 'T1', feature: null })
  await waitFor(() => expect(document.querySelector('[data-strip="feature"]')).not.toBeNull())
  store.stop()
})

it('the sketch strip and its right end fit a 1024 pixel window with the two pins the cap allows', () => {
  // buttons and pins at their layout widths, then the toggle (106), Normal to (64), Cancel and Done (about 76 and 84 with their margins)
  expect(stripWidth(SKETCH_STRIP, 2) + 106 + 2 + 64 + 2 + 6 + 76 + 4 + 84).toBeLessThanOrEqual(1024)
})

it('the Draw group ends with Mirror and the two patterns, all three wired (d-s14-strip, I14.3)', () => {
  const draw = SKETCH_STRIP.find((d) => d.kind === 'group' && d.key === 'draw')
  const ids = draw?.kind === 'group' ? draw.tools.map((t) => t.id) : []
  expect(ids.slice(-3)).toEqual(['sk_mirror', 'sk_lpattern', 'sk_cpattern'])
  expect([sketchToolState('sk_mirror'), sketchToolState('sk_lpattern'), sketchToolState('sk_cpattern')]).toEqual(['ok', 'ok', 'ok'])
})

it('Construct with nothing selected shows pressed on the strip, and the mode ends when the sketch closes (F20)', async () => {
  const { store, sock } = await shell()
  await openSketch(sock)
  const construct = () => document.querySelector('[data-strip="sketch"] [data-tool="construct"]')!
  expect(construct().getAttribute('aria-pressed')).toBe('false')
  fireEvent.click(construct())
  expect(construct().getAttribute('aria-pressed')).toBe('true')
  expect(construct().hasAttribute('data-active')).toBe(true)
  // nothing was sent: a mode is the client's
  expect(sock.sent.some((m) => m['type'] === 'sketch.construction')).toBe(false)
  // the mode stays while the tool changes
  fireEvent.click(document.querySelector('[data-strip="sketch"] [data-tool="select"]')!)
  expect(construct().getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(construct())
  expect(construct().getAttribute('aria-pressed')).toBe('false')
  fireEvent.click(construct())
  sock.say({ type: 'sketch.cancelled', document: 'd', tab: 'T1', feature: null })
  await waitFor(() => expect(document.querySelector('[data-strip="feature"]')).not.toBeNull())
  await openSketch(sock)
  expect(construct().getAttribute('aria-pressed')).toBe('false')
  store.stop()
})

it('the Select button on the strip and the lit tool pressed again both go back to Select (F20)', async () => {
  const { store, sock } = await shell()
  await openSketch(sock)
  const strip = () => document.querySelector('[data-strip="sketch"]')!
  const lit = () => [...strip().querySelectorAll('[data-active]')].map((e) => e.getAttribute('data-tool') ?? e.getAttribute('data-group'))
  fireEvent.click(strip().querySelector('[data-group="draw"]')!)
  fireEvent.pointerUp(document.querySelector('.group-row[data-tool="line"] .group-tool')!)
  expect(lit()).toContain('draw')
  fireEvent.pointerDown(strip().querySelector('[data-group="draw"]')!)
  fireEvent.pointerUp(document.querySelector('.group-row[data-tool="line"] .group-tool')!)
  expect(lit()).toEqual(['select'])
  store.stop()
})
