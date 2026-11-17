import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Description, FieldSpec, OpenReply } from '../dialog/dialogApi'
import { idOfItem } from '../selection/store'
import type { SelectionItem } from '../selection/store'
import { MODE_KEY } from '../shell/mode'
import { Shell } from '../shell/Shell'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { bracketWithConnectors, fixture } from '../test/fixtures'
import { keyOf } from '../test/keys'

// Mate connectors through the whole Shell (card I10.3): K and J show and hide the triads, and a dialog's field that wants a mate
// connector takes one from the view. The server is scripted; the Assembly's bytes are the engine's own (make_connectors_fixture.py).
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
  window.localStorage.setItem(MODE_KEY, 'desktop')
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const key = (chord: string) => act(() => void window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...keyOf(chord) })))
const studioConnectors = () => window.__freeshapeViewport?.connectors
const asm = () => window.__freeshapeAssembly

const CONNECTOR = { id: 'F90', name: 'Pin', o: [10, 5, 3] as [number, number, number] }

async function studioShell(extra: (c: Call) => ReturnType<Parameters<typeof stubFetch>[0]> = () => undefined) {
  const bytes = bracketWithConnectors([CONNECTOR])
  const calls = stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    return extra(c)
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={vi.fn()} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  return { calls, bytes }
}

describe('K and J', () => {
  it('in a Part Studio, a connector draws one triad; K hides it and K again shows it', async () => {
    await studioShell()
    expect(studioConnectors()).toBe(1)
    key('K')
    await waitFor(() => expect(studioConnectors()).toBe(0))
    key('K')
    await waitFor(() => expect(studioConnectors()).toBe(1))
  })

  it('in an Assembly, K hides the instances\' connectors and J the mate ends', async () => {
    const raw = readFileSync(resolve(process.cwd(), 'fixtures', 'hinge.connectors.assembly.fsw'))
    const bytes = raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer
    stubFetch((c) => (c.method === 'GET' && c.url.endsWith('/outputs') ? { bytes } : undefined))
    const { store, sock } = makeStore()
    store.watch('d')
    render(<Shell store={store} documentId="d" onLeave={vi.fn()} />)
    sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Hinge' }, tabs: [tab('T1', 'studio', 'hinge_parts'), tab('T2', 'assembly', 'hinge')], read_only: false, holder: null })
    fireEvent.click(await screen.findByRole('button', { name: 'hinge' }))
    await screen.findByTestId('instance-list')
    await waitFor(() => expect(asm()?.drawn).toBeGreaterThan(0))
    const was = { connectors: asm()!.connectors, mateEnds: asm()!.mateEnds }
    expect(was.connectors).toBe(2)
    expect(was.mateEnds).toBeGreaterThan(0)
    key('J')
    await waitFor(() => expect(asm()?.mateEnds).toBe(0))
    expect(asm()?.connectors).toBe(2)
    key('J')
    await waitFor(() => expect(asm()?.mateEnds).toBe(was.mateEnds))
    key('K')
    await waitFor(() => expect(asm()?.connectors).toBe(0))
    expect(asm()?.mateEnds).toBe(was.mateEnds)
    key('K')
    await waitFor(() => expect(asm()?.connectors).toBe(2))
  })
})

const sel = (k: string, label: string, extra: Partial<FieldSpec> = {}): FieldSpec => ({ key: k, kind: 'sel', standard: true, visible: true, label, want: ['edge'], multi: false, number: false, list: false, tip: null, ...extra })

/** The Plane tool's own selection field: the kinds the desktop's schema gives it, a mate connector (as its Z axis) among them. */
const PLANE_WANT = ['plane', 'face', 'edge', 'vertex', 'sketch_line', 'sketch_point', 'axis_feature', 'axis_connector']

describe('a Plane dialog in its mate connector mode', () => {
  const open = (items: OpenReply['fields'] = {}): OpenReply => ({
    dialog: 'did1',
    document: 'd',
    tab: 'T1',
    feature: 'F2',
    name: 'Plane 1',
    revision: 1,
    description: { type: 'plane', fields: [sel('refs_sel', 'Planes, faces, edges, axes, sketch curves and points', { want: PLANE_WANT })], custom: [] } as Description,
    values: {},
    fields: items,
    active: 'refs_sel',
  })
  const asItem = (): SelectionItem => ({ id: idOfItem('connector', null, null, -1, 'F90'), kind: 'connector', part: null, index: -1, name: null, key: 'F90' })

  it('takes a connector pick from the view into its field, and draws the connector even with K off', async () => {
    const reply = open()
    const bytesOf = bracketWithConnectors([CONNECTOR])
    const { calls } = await studioShell((c) => {
      if (c.url.endsWith('/features/F2/dialog')) return { status: 201, json: reply }
      if (c.url.endsWith('/refs')) return { json: { document: 'd', tab: 'T1', revision: 1, items: [{ ok: true, kind: 'axis', ref: { kind: 'connector', fid: 'F90', axis: 'z' }, name: null }] } }
      if (c.url.endsWith('/update')) return { json: { ...reply, fields: { refs_sel: (c.body as { values: { refs_sel: unknown[] } }).values.refs_sel }, errors: {}, preview: null } }
      if (c.method === 'GET' && c.url.endsWith('/dialog/did1/base')) return { bytes: bytesOf, headers: { 'X-Revision': String(fixture('bracket').outputs.revision) } }
      return undefined
    })
    key('K')
    await waitFor(() => expect(studioConnectors()).toBe(0))
    const el = screen.getByRole('button', { name: 'Extrude 1' })
    fireEvent.click(el)
    fireEvent.click(el)
    await screen.findByTestId('dialog-form')
    const selection = () => window.__freeshape!.shell.selection
    await waitFor(() => expect(selection().getState().field).toBe(true))
    // the field wants a connector: the view may pick one, and shows the triads K had hidden
    expect(selection().pickFilter).toContain('connector')
    await waitFor(() => expect(studioConnectors()).toBe(1))
    await waitFor(() => expect(screen.getByTestId('shell-viewport').hasAttribute('data-dialog-base')).toBe(true))
    act(() => {
      selection().aim(asItem())
      selection().commit(selection().getAimed())
    })
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/update'))).toHaveLength(1))
    const refs = calls.find((c) => c.url.endsWith('/refs'))!
    expect(refs.body).toEqual({ picks: [{ part: 'F90', kind: 'connector', want: PLANE_WANT }], dialog: 'did1' })
    expect(calls.find((c) => c.url.endsWith('/update'))!.body).toEqual({ values: { refs_sel: [{ ok: true, kind: 'axis', ref: { kind: 'connector', fid: 'F90', axis: 'z' }, name: null }] } })
    await waitFor(() => expect(within(screen.getByRole('group', { name: /Planes, faces/ })).queryAllByTestId('sel-chip')).toHaveLength(1))
    // the dialog ends: the field is gone and K's hide is back
    fireEvent.click(within(document.querySelector<HTMLElement>('.dialog-header')!).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(studioConnectors()).toBe(0))
  })
})
