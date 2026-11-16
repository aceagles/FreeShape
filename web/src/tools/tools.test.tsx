import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { FEATURE_STRIP, groupTools, pinCap, stripWidth, toolIds } from './defs'
import { iconNames } from './icons'
import { picksOf } from './picks'
import { PinStore } from './pins'
import { stateOf, useToolTypes } from './useToolTypes'
import type { ToolState } from './useToolTypes'
import { DESKTOP_ONLY_NOTE, STRIP_FULL_TEXT, ToolStrip } from './ToolStrip'

// The feature toolbar (card I6.2): the strip with its groups, pins and press-move-release lists, in a Shell against a
// scripted server for the tap-to-dialog flow, and on its own (with synthetic pointer events) for the list behaviour.
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

class MemoryStorage {
  data: Record<string, string> = {}
  getItem(k: string) {
    return this.data[k] ?? null
  }
  setItem(k: string, v: string) {
    this.data[k] = v
  }
}

const typeRow = (type: string, extra: Partial<{ standard: boolean; can_start: boolean }> = {}) => ({ type, label: type, standard: true, can_start: true, ...extra })
const ALL = toolIds(FEATURE_STRIP)

function Harness({ states = {}, width = 1024, pins, onStart }: { states?: Record<string, ToolState>; width?: number; pins: PinStore; onStart: (id: string) => void }) {
  return <ToolStrip strip="feature" defs={FEATURE_STRIP} stateOf={(id) => states[id] ?? 'ok'} width={width} pins={pins} onStart={onStart} />
}
const harness = (props: Partial<Parameters<typeof Harness>[0]> = {}) => {
  const onStart = vi.fn()
  const pins = props.pins ?? new PinStore(new MemoryStorage())
  const r = render(<Harness {...props} pins={pins} onStart={onStart} />)
  return { ...r, onStart, pins }
}
const group = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}`) })
const list = () => screen.queryByRole('menu')
const row = (name: string) => within(screen.getByRole('menu')).getByRole('menuitem', { name: new RegExp(`^${name}`) }).closest('.group-row') as HTMLElement
const rowButton = (name: string) => within(row(name)).getByRole('menuitem')

it('the strip has the layout order, the groups show their names, and the pins start empty', () => {
  harness()
  const labels = [...document.querySelectorAll('.tool-strip > .strip-button .strip-label')].map((e) => e.textContent)
  expect(labels).toEqual(['Sketch', 'Create', 'Modify', 'Plane', 'Copy'])
  expect(document.querySelectorAll('[data-pinned]').length).toBe(0)
  expect(screen.queryByRole('button', { name: /More/ })).toBeNull()
})

it('every tool of the strip has an exported icon', () => {
  const have = new Set(iconNames())
  const missing = [...groupTools(FEATURE_STRIP), ...FEATURE_STRIP.flatMap((d) => (d.kind === 'tool' ? [d.tool] : []))].filter((t) => !have.has(t.icon)).map((t) => t.id)
  expect(missing).toEqual([])
})

it('a standard tool is enabled and a custom-field tool is disabled with the desktop-only note, as the types route says', async () => {
  stubFetch((c) =>
    c.url.includes('/features/types')
      ? { json: { document: 'd', tab: 'T1', revision: 1, types: ALL.map((id) => typeRow(id, id === 'sm_base' ? { standard: false } : {})) } }
      : undefined,
  )
  function Live() {
    const types = useToolTypes('d', 'T1', 1, [], false)
    return <ToolStrip strip="feature" defs={FEATURE_STRIP} stateOf={(id) => stateOf(types, id)} width={1024} pins={new PinStore(new MemoryStorage())} onStart={() => {}} />
  }
  render(<Live />)
  await waitFor(() => expect(group('Modify').hasAttribute('disabled')).toBe(false))
  fireEvent.pointerDown(group('Modify'))
  expect(row('Fillet').dataset.state).toBe('ok')
  expect(row('Sheet metal').dataset.state).toBe('desktop')
  expect(within(row('Sheet metal')).getByText(DESKTOP_ONLY_NOTE)).toBeTruthy()
  expect(within(row('Fillet')).queryByText(DESKTOP_ONLY_NOTE)).toBeNull()
})

it('a tool the selection cannot start is dimmed, and a tap on it starts nothing', () => {
  const { onStart } = harness({ states: { revolve: 'dimmed', plane: 'dimmed' } })
  const plane = screen.getByRole('button', { name: /^Plane/ })
  expect(plane.getAttribute('data-state')).toBe('dimmed')
  fireEvent.click(plane)
  expect(onStart).not.toHaveBeenCalled()
})

it('a press on Modify, a move onto Chamfer and a release starts Chamfer', () => {
  const { onStart } = harness()
  fireEvent.pointerDown(group('Modify'), { pointerId: 1, pointerType: 'touch' })
  expect(list()).toBeTruthy()
  fireEvent.pointerEnter(row('Chamfer'), { pointerId: 1 })
  expect(row('Chamfer').hasAttribute('data-aim')).toBe(true)
  expect(row('Fillet').hasAttribute('data-aim')).toBe(false)
  fireEvent.pointerUp(rowButton('Chamfer'), { pointerId: 1 })
  expect(onStart).toHaveBeenCalledExactlyOnceWith('chamfer')
  expect(list()).toBeNull()
})

it('a press and release on the group button leaves the list open, and a tap on a row then starts that tool', () => {
  const { onStart } = harness()
  fireEvent.pointerDown(group('Modify'), { pointerId: 1, pointerType: 'touch' })
  fireEvent.pointerUp(group('Modify'), { pointerId: 1, pointerType: 'touch' })
  expect(list()).toBeTruthy()
  expect(onStart).not.toHaveBeenCalled()
  // nothing is lit once the press is over, and moving over a row does not light it
  fireEvent.pointerEnter(row('Shell'))
  expect(row('Shell').hasAttribute('data-aim')).toBe(false)
  fireEvent.pointerDown(rowButton('Shell'), { pointerId: 2, pointerType: 'touch' })
  fireEvent.pointerUp(rowButton('Shell'), { pointerId: 2, pointerType: 'touch' })
  expect(onStart).toHaveBeenCalledExactlyOnceWith('shell')
  expect(list()).toBeNull()
})

it('a release outside the list closes it and starts nothing', () => {
  const { onStart } = harness()
  fireEvent.pointerDown(group('Modify'), { pointerId: 1, pointerType: 'touch' })
  fireEvent.pointerEnter(row('Chamfer'))
  fireEvent.pointerUp(document.body, { pointerId: 1 })
  expect(list()).toBeNull()
  expect(onStart).not.toHaveBeenCalled()
})

it('a release on a dimmed row closes the list and starts nothing', () => {
  const { onStart } = harness({ states: { chamfer: 'dimmed' } })
  fireEvent.pointerDown(group('Modify'), { pointerId: 1, pointerType: 'touch' })
  fireEvent.pointerEnter(row('Chamfer'))
  expect(row('Chamfer').hasAttribute('data-aim')).toBe(false)
  fireEvent.pointerUp(rowButton('Chamfer'))
  expect(list()).toBeNull()
  expect(onStart).not.toHaveBeenCalled()
})

it('a press elsewhere on the screen closes the list', () => {
  harness()
  fireEvent.pointerDown(group('Create'))
  fireEvent.pointerUp(group('Create'))
  expect(list()).toBeTruthy()
  fireEvent.pointerDown(screen.getByTestId('strip-scrim'))
  expect(list()).toBeNull()
})

it('pinning Fillet adds a Fillet button after the groups, the pin survives a reload, and pinning again removes it', () => {
  const storage = new MemoryStorage()
  const first = harness({ pins: new PinStore(storage) })
  const pinFillet = () => {
    fireEvent.pointerDown(group('Modify'))
    fireEvent.pointerUp(group('Modify'))
    const pin = screen.getByRole('button', { name: /^(Pin|Unpin) Fillet$/ })
    fireEvent.pointerUp(pin)
  }
  const strip = () => [...document.querySelectorAll('.tool-strip > .strip-button')].map((e) => e.getAttribute('data-tool') ?? e.querySelector('.strip-label')!.textContent)
  pinFillet()
  expect(strip().at(-1)).toBe('fillet')
  expect(document.querySelector('[data-pinned]')?.getAttribute('data-tool')).toBe('fillet')
  // starts on a tap
  fireEvent.click(document.querySelector('.tool-strip > [data-tool="fillet"]')!)
  expect(first.onStart).toHaveBeenCalledWith('fillet')
  // a reload: a new page with the same storage
  cleanup()
  harness({ pins: new PinStore(storage) })
  expect(strip().at(-1)).toBe('fillet')
  expect(screen.getByRole('toolbar').querySelectorAll('.strip-button').length).toBe(6)
  // pinning again removes it
  pinFillet()
  expect(strip()).toEqual(['sketch', 'Create', 'Modify', 'plane', 'Copy'])
  cleanup()
  harness({ pins: new PinStore(storage) })
  expect(document.querySelectorAll('[data-pinned]').length).toBe(0)
})

it('with the cap reached, a pin button on an unpinned tool does nothing and the list says the strip is full', () => {
  const storage = new MemoryStorage()
  const six = ['fillet', 'chamfer', 'shell', 'hole', 'extrude', 'revolve']
  const pins = new PinStore(storage)
  six.forEach((id) => pins.toggle('feature', id))
  harness({ pins })
  expect(document.querySelectorAll('.tool-strip > .strip-button').length).toBe(5 + 6)
  fireEvent.pointerDown(group('Modify'))
  fireEvent.pointerUp(group('Modify'))
  expect(screen.getByText(STRIP_FULL_TEXT)).toBeTruthy()
  const pin = screen.getByRole('button', { name: 'Pin Draft' })
  expect(pin.hasAttribute('data-dim')).toBe(true)
  fireEvent.pointerUp(pin)
  expect(document.querySelectorAll('.tool-strip > .strip-button').length).toBe(5 + 6)
  expect(screen.getByRole('button', { name: 'Unpin Fillet' }).hasAttribute('data-dim')).toBe(false)
  // an unpin still works at the cap
  fireEvent.pointerUp(screen.getByRole('button', { name: 'Unpin Fillet' }))
  expect(document.querySelectorAll('.tool-strip > .strip-button').length).toBe(5 + 5)
  expect(screen.queryByText(STRIP_FULL_TEXT)).toBeNull()
})

it('the cap is 6 and 10 on the feature strip, 2 and 6 on the sketch strip', () => {
  expect([pinCap('feature', 1024), pinCap('feature', 1366), pinCap('sketch', 1024), pinCap('sketch', 1366)]).toEqual([6, 10, 2, 6])
})

it('at 1024 x 768 with six pins, and at 1366 with ten, the buttons take less than the strip', () => {
  expect(stripWidth(FEATURE_STRIP, 6)).toBeLessThanOrEqual(1024)
  expect(stripWidth(FEATURE_STRIP, 10)).toBeLessThanOrEqual(1366)
  expect(stripWidth(FEATURE_STRIP, 0)).toBe(2 * 8 + 5 * 72 + 4 * 2 + 20)
})

it('a selection becomes picks with indices from 1, and leaves out what the server has no pick for', () => {
  const item = (kind: 'face' | 'edge' | 'part' | 'plane', part: string | null, index: number) => ({ id: `${kind}${index}`, kind, part, index, name: null, key: null })
  expect(picksOf([item('face', 'F2/1', 0), item('edge', 'F2/1', 4), item('part', 'F2/1', -1), item('plane', null, -1)])).toEqual([
    { part: 'F2/1', kind: 'face', index: 1 },
    { part: 'F2/1', kind: 'edge', index: 5 },
    { part: 'F2/1', kind: 'part' },
  ])
})

// ---- the whole Shell

const created = {
  dialog: 'did9',
  document: 'd',
  tab: 'T1',
  feature: null,
  type: 'extrude',
  name: 'Extrude 2',
  revision: 1,
  description: { type: 'extrude', custom: [], fields: [{ key: 'depth', kind: 'num', standard: true, visible: true, label: 'Depth', unit: 'mm', min: null, signed: false }] },
  values: { depth: { value: 10, text: '10' } },
  fields: { profiles: [{ ok: true, kind: 'face', ref: {}, name: 'Face4' }] },
  active: 'profiles',
}

it('tapping Extrude with a face selected opens the create dialog with the selection as picks, and clears the selection', async () => {
  const { bytes } = fixture('bracket')
  const calls = stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.includes('/features/types')) {
      const picks = c.url.includes('?picks=') ? (JSON.parse(decodeURIComponent(c.url.split('?picks=')[1]!)) as unknown[]) : []
      return { json: { document: 'd', tab: 'T1', revision: 1, types: ALL.map((id) => typeRow(id, { can_start: picks.length === 0 || id !== 'plane' })) } }
    }
    if (c.method === 'POST' && c.url.endsWith('/features/dialog')) return { status: 201, json: created }
    if (c.url.endsWith('/cancel')) return { json: { cancelled: true } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  const part = 'F2/1'
  act(() => window.__freeshape!.shell.selection.set([{ id: 'face|F2/1|Face4', kind: 'face', part, index: 3, name: 'Face4', key: null }]))
  // the types route is asked again with the selection; a tool the selection cannot start is dimmed
  await waitFor(() => expect(document.querySelector('[data-tool="plane"]')?.getAttribute('data-state')).toBe('dimmed'))
  expect(calls.some((c) => c.url.includes('?picks=') && JSON.parse(decodeURIComponent(c.url.split('?picks=')[1]!))[0].index === 4)).toBe(true)
  fireEvent.pointerDown(group('Create'))
  fireEvent.pointerUp(rowButton('Extrude'))
  const open = await waitFor(() => {
    const c = calls.find((x) => x.method === 'POST' && x.url.endsWith('/features/dialog'))
    expect(c).toBeTruthy()
    return c!
  })
  expect(open.body).toEqual({ type: 'extrude', picks: [{ part, kind: 'face', index: 4 }] })
  expect(open.headers['X-Client-Id']).toBe('abcdefgh')
  await screen.findByRole('dialog', { name: 'Extrude 2' })
  expect(window.__freeshape!.shell.selection.getState().items).toEqual([])
  // the strip is off while the dialog is open
  expect(document.querySelector('[data-tool="fillet"]')).toBeNull()
  expect(group('Modify').hasAttribute('disabled')).toBe(true)
  store.stop()
})
