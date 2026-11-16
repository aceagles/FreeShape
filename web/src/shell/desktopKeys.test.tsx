import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Vector3 } from 'three'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { suspendKeys } from '../input/keys'
import { openedMessage } from '../sketch/testFixture'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { keyOf } from '../test/keys'
import { toolIds, FEATURE_STRIP } from '../tools/defs'
import { STANDARD_VIEWS } from '../viewport/camera'
import type { ViewportController } from '../viewport/controller'
import { viewOnScreen } from '../viewport/viewCommands'
import { MODE_KEY } from './mode'
import { Shell } from './Shell'

// Desktop mode's keys through the whole Shell (card I9.6): each key reaches what the desktop routes it to, and Tablet mode
// keeps F13's Esc and Delete only.
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

const typeRow = (type: string) => ({ type, label: type, standard: true, can_start: true })
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
    if (c.method === 'POST' && c.url.endsWith('/save')) return { json: { saved: true } }
    if (c.method === 'GET' && c.url.endsWith('/documents/d/tabs')) return { json: { tabs: [tab('T1', 'studio', 'Part Studio 1')] } }
    if (c.method === 'POST' && c.url.endsWith('/documents/d/tabs')) return { json: { tab: { id: 'T2' }, tabs: [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'studio', 'Part Studio 2')] } }
    if (c.method === 'POST' && c.url.endsWith('/close')) return { json: { closed: true } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  const onLeave = vi.fn()
  render(<Shell store={store} documentId="d" onLeave={onLeave} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  expect(screen.getByTestId('shell').getAttribute('data-mode')).toBe(mode)
  return { store, sock, calls, onLeave }
}

const key = (chord: string, target: EventTarget = window) => act(() => void target.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...keyOf(chord) })))
const opened = async (sock: Awaited<ReturnType<typeof shell>>['sock'], extra: Record<string, unknown> = {}) => {
  sock.say({ ...openedMessage(), document: 'd', tab: 'T1', ...extra })
  await waitFor(() => expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull())
}
const planeItem = (k: string) => ({ id: `plane|${k}`, kind: 'plane' as const, part: null, index: -1, name: null, key: k })
const faceItem = { id: 'face|F2/1|Face4', kind: 'face' as const, part: 'F2/1', index: 3, name: 'Face4', key: null }
const selection = () => window.__freeshape!.shell.selection
const topBar = () => within(document.querySelector<HTMLElement>('[data-region="top-bar"]')!)
const sentTypes = (sock: Awaited<ReturnType<typeof shell>>['sock']) => sock.sent.map((m) => m['type'])
const undoSent = (sock: Awaited<ReturnType<typeof shell>>['sock'], calls: Call[]) =>
  sock.sent.some((m) => JSON.stringify(m).includes('"op":"undo"')) || calls.some((c) => JSON.stringify(c.body ?? null).includes('"op":"undo"'))
const camera = () => (viewOnScreen() as unknown as ViewportController).view.state()
const close = (a: readonly number[], b: readonly number[]) => a.every((x, i) => Math.abs(x - b[i]!) < 1e-6)

describe('Desktop mode', () => {
  it('Ctrl+Z with a sketch open undoes in the sketch, not in the Part Studio; the top bar Undo is on and does the same', async () => {
    const { store, sock, calls } = await shell()
    const undo = topBar().getByRole('button', { name: 'Undo' }) as HTMLButtonElement
    await opened(sock, { can_undo: true, can_redo: true })
    expect(undo.disabled).toBe(false)
    expect((topBar().getByRole('button', { name: 'Redo' }) as HTMLButtonElement).disabled).toBe(false)
    key('Ctrl+Z')
    expect(sock.sent.at(-1)).toEqual({ type: 'sketch.undo' })
    key('Ctrl+Y')
    expect(sock.sent.at(-1)).toEqual({ type: 'sketch.redo' })
    key('Ctrl+Shift+Z')
    expect(sock.sent.at(-1)).toEqual({ type: 'sketch.redo' })
    fireEvent.click(undo)
    expect(sock.sent.at(-1)).toEqual({ type: 'sketch.undo' })
    expect(undoSent(sock, calls)).toBe(false)
    store.stop()
  })

  it('the top bar Undo is off in a sketch with nothing to undo', async () => {
    const { store, sock } = await shell()
    await opened(sock)
    expect((topBar().getByRole('button', { name: 'Undo' }) as HTMLButtonElement).disabled).toBe(true)
    store.stop()
  })

  it('Ctrl+S saves, Alt+N adds a Part Studio, Alt+W asks to close the tab, Ctrl+O goes back to the Documents', async () => {
    const { store, calls, onLeave } = await shell()
    key('Ctrl+S')
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/documents/d/save'))).toBe(true))
    key('Alt+N')
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/documents/d/tabs')).length).toBe(1))
    expect(calls.find((c) => c.method === 'POST' && c.url.endsWith('/documents/d/tabs'))!.body).toEqual({ kind: 'studio' })
    key('Alt+Shift+N')
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/documents/d/tabs')).length).toBe(2))
    expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/documents/d/tabs'))[1]!.body).toEqual({ kind: 'assembly' })
    key('Alt+W')
    expect(await screen.findByRole('dialog', { name: /^Delete "/ })).toBeTruthy()
    // a small dialog over the page has the keyboard: Ctrl+S does nothing behind it, and Esc closes it
    const saves = calls.filter((c) => c.url.endsWith('/save')).length
    key('Ctrl+S')
    expect(calls.filter((c) => c.url.endsWith('/save')).length).toBe(saves)
    key('Escape')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    key('Ctrl+O')
    await waitFor(() => expect(onLeave).toHaveBeenCalled())
    store.stop()
  })

  it('Shift+S with Top selected opens a sketch on it; with nothing selected it asks for a plane', async () => {
    const { store, sock } = await shell()
    act(() => selection().set([planeItem('Top')]))
    key('Shift+S')
    expect(sock.sent.at(-1)).toEqual({ type: 'sketch.open', document: 'd', tab: 'T1', plane: { kind: 'std', name: 'Top' } })
    store.stop()
  })

  it('Shift+S with nothing selected asks for a plane', async () => {
    const { store } = await shell()
    key('Shift+S')
    expect(await screen.findByRole('dialog', { name: 'Sketch on which plane?' })).toBeTruthy()
    store.stop()
  })

  it('Shift+E opens Extrude with the selection, Shift+W Revolve, Shift+F Fillet', async () => {
    for (const [chord, type] of [
      ['Shift+E', 'extrude'],
      ['Shift+W', 'revolve'],
      ['Shift+F', 'fillet'],
    ] as const) {
      const { store, calls } = await shell()
      act(() => selection().set([faceItem]))
      key(chord)
      const open = await waitFor(() => {
        const c = calls.find((x) => x.method === 'POST' && x.url.endsWith('/features/dialog'))
        expect(c).toBeTruthy()
        return c!
      })
      expect(open.body).toEqual({ type, picks: [{ part: 'F2/1', kind: 'face', index: 4 }] })
      store.stop()
      cleanup()
    }
  })

  it('Shift+E in a sketch finishes it first', async () => {
    const { store, sock } = await shell()
    await opened(sock)
    key('Shift+E')
    expect(sock.sent.at(-1)).toEqual({ type: 'sketch.commit' })
    store.stop()
  })

  it('with a feature dialog open Delete changes nothing in the model, Enter accepts and Esc closes the dialog', async () => {
    const { store, sock, calls } = await shell()
    act(() => selection().set([faceItem]))
    key('Shift+E')
    await screen.findByRole('dialog', { name: 'extrude 1' })
    // the dialog's pad has the keys while it edits the first number: close it first
    const before = calls.length
    const sentBefore = sock.sent.length
    for (let i = 0; i < 3 && document.querySelector('[data-testid="pad"]') !== null; i++) key('Escape')
    key('Delete')
    key('Backspace')
    expect(calls.slice(before).filter((c) => c.method !== 'GET' && !c.url.includes('/dialog'))).toEqual([])
    expect(sock.sent.slice(sentBefore).filter((m) => m['type'] === 'edit')).toEqual([])
    if (screen.queryByRole('dialog', { name: 'extrude 1' }) !== null) key('Escape')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'extrude 1' })).toBeNull())
    expect(calls.some((c) => c.url.endsWith('/cancel'))).toBe(true)
    store.stop()
  })

  it('Enter accepts a feature dialog', async () => {
    const { store, calls } = await shell()
    act(() => selection().set([faceItem]))
    key('Shift+E')
    await screen.findByRole('dialog', { name: 'extrude 1' })
    for (let i = 0; i < 3 && document.querySelector('[data-testid="pad"]') !== null; i++) key('Escape')
    ;(document.activeElement as HTMLElement | null)?.blur()
    key('Enter')
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/accept'))).toBe(true))
    store.stop()
  })

  it('Esc with no dialog: a Part Studio drops its selection; a sketch steps back, then leaves (asking when something was drawn)', async () => {
    const { store, sock } = await shell()
    act(() => selection().set([faceItem]))
    key('Escape')
    expect(selection().getState().items).toEqual([])
    await opened(sock, { can_undo: true })
    act(() => window.__freeshape!.shell.selection.clear())
    // nothing left to step back (Select is on, nothing selected): Esc asks before dropping what was drawn
    key('Escape')
    expect(sentTypes(sock)).not.toContain('sketch.cancel')
    const ask = await screen.findByRole('dialog')
    expect(ask.textContent).toMatch(/discard|drop|keep/i)
    store.stop()
  })

  it('Esc in a sketch with nothing drawn leaves it at once', async () => {
    const { store, sock } = await shell()
    await opened(sock)
    key('Escape')
    expect(sock.sent.at(-1)).toEqual({ type: 'sketch.cancel' })
    store.stop()
  })

  it('Space clears the selection; Delete with a part selected opens Delete part', async () => {
    const { store, calls } = await shell()
    act(() => selection().set([faceItem]))
    key('Space')
    expect(selection().getState().items).toEqual([])
    const part = fixture('bracket').outputs.parts[0]!.id
    act(() => window.__freeshape!.shell.selectPart(part))
    key('Delete')
    const open = await waitFor(() => {
      const c = calls.find((x) => x.method === 'POST' && x.url.endsWith('/features/dialog'))
      expect(c).toBeTruthy()
      return c!
    })
    expect(open.body).toEqual({ type: 'delete_part', picks: [{ part, kind: 'part' }] })
    store.stop()
  })

  it('Delete with a feature selected asks before deleting it', async () => {
    const { store } = await shell()
    const feature = fixture('bracket').outputs.features.at(-1)!
    act(() => window.__freeshape!.shell.selectFeature(feature.id))
    key('Delete')
    expect(await screen.findByRole('dialog', { name: `Delete "${feature.name}"?` })).toBeTruthy()
    store.stop()
  })

  it('Shift+1 to Shift+7 set the seven standard views, F fits, an arrow turns 15 degrees, Ctrl+Shift+Right pans a tenth of the width', async () => {
    const { store } = await shell()
    const names = ['front', 'back', 'left', 'right', 'top', 'bottom', 'iso'] as const
    names.forEach((n, i) => {
      key(`Shift+${i + 1}`)
      expect(close(camera().back, new Vector3(...STANDARD_VIEWS[n]).normalize().toArray()), n).toBe(true)
    })
    const fitted = camera()
    act(() => (viewOnScreen() as unknown as ViewportController).view.pan(40, 10))
    key('F')
    expect(close(camera().target, fitted.target)).toBe(true)

    // from the Right view, a Right arrow turns the eye 15 degrees towards the Front view, six of them reach it
    key('Shift+4')
    key('ArrowRight')
    const b = camera().back
    expect(Math.acos(b[0]) * (180 / Math.PI)).toBeCloseTo(15, 6)
    expect(b[1]).toBeLessThan(0)
    for (let i = 0; i < 5; i++) key('ArrowRight')
    expect(close(camera().back, [0, -1, 0])).toBe(true)
    key('ArrowUp')
    expect(Math.asin(-camera().back[2]) * (180 / Math.PI)).toBeCloseTo(15, 6)

    const s = camera()
    key('Ctrl+Shift+ArrowRight')
    const t = camera()
    const right = new Vector3(...t.up).cross(new Vector3(...t.back))
    const moved = new Vector3(...t.target).sub(new Vector3(...s.target))
    // the model moves right on screen: the target moves left by a tenth of the width
    expect(moved.dot(right)).toBeCloseTo(-(s.width / 10) * s.scale, 6)
    store.stop()
  })

  it('N looks straight at the selected plane, from the other side when pressed again', async () => {
    const { store } = await shell()
    act(() => selection().set([planeItem('Top')]))
    key('N')
    expect(close(camera().back, [0, 0, 1])).toBe(true)
    key('N')
    expect(close(camera().back, [0, 0, -1])).toBe(true)
    store.stop()
  })

  it('no key acts while a text field or the pad has the focus', async () => {
    const { store, calls } = await shell()
    act(() => selection().set([faceItem]))
    const field = document.createElement('input')
    document.body.append(field)
    for (const chord of ['Space', 'Escape', 'Ctrl+S', 'Shift+1', 'Shift+E']) key(chord, field)
    const resume = suspendKeys()
    for (const chord of ['Space', 'Escape', 'Ctrl+S', 'Shift+E']) key(chord)
    resume()
    field.remove()
    expect(selection().getState().items).toHaveLength(1)
    expect(calls.some((c) => c.method === 'POST')).toBe(false)
    store.stop()
  })
})

describe('Tablet mode', () => {
  it('keeps F13: only Esc and Delete act; Ctrl+Z, Ctrl+S, Space and the view keys do nothing', async () => {
    const { store, sock, calls } = await shell('tablet')
    act(() => selection().set([faceItem]))
    const before = camera()
    for (const chord of ['Ctrl+S', 'Space', 'Shift+1', 'ArrowRight', 'Shift+E', 'Alt+N', 'Ctrl+Z']) key(chord)
    expect(selection().getState().items).toHaveLength(1)
    expect(close(camera().back, before.back)).toBe(true)
    expect(calls.some((c) => c.method === 'POST')).toBe(false)
    await opened(sock, { can_undo: true })
    key('Ctrl+Z')
    expect(sentTypes(sock)).not.toContain('sketch.undo')
    // Esc steps back in the sketch and never leaves it, as before
    key('Escape')
    key('Escape')
    expect(sentTypes(sock)).not.toContain('sketch.cancel')
    expect(screen.queryByRole('dialog')).toBeNull()
    store.stop()
  })
})
