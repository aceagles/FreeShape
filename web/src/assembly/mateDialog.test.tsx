import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { MODE_KEY } from '../shell/mode'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { keyOf } from '../test/keys'
import { idOfItem } from '../selection/store'
import type { SelectionItem, SelectionStore } from '../selection/store'
import { decodeOutputs, isAssembly } from '../wire'
import type { AssemblyOutputs, Vec3 } from '../wire'
import { sidePick, preselection, previewOutputs, pickOf } from './mates'
import type { MateView } from './mates'


// The mate dialog and the mate tools (card I10.6) in the whole Shell on the hinge Assembly, against a scripted server that
// answers the routes of freeshape/serve.py "Mate dialogs" (I10.5).
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

const raw = (): ArrayBuffer => {
  const b = readFileSync(resolve(process.cwd(), 'fixtures', 'hinge.assembly.fsw'))
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
}
const hinge = (): AssemblyOutputs => {
  const o = decodeOutputs(raw())
  if (!isAssembly(o)) throw new Error('not an assembly')
  return o
}

const MATES = [
  ['fastened', 'Fastened', []],
  ['revolute', 'Revolute', ['rz']],
  ['slider', 'Slider', ['tz']],
  ['cylindrical', 'Cylindrical', ['tz', 'rz']],
  ['planar', 'Planar', ['tx', 'ty', 'rz']],
  ['pin_slot', 'Pin slot', ['tx', 'rz']],
  ['parallel', 'Parallel', ['tx', 'ty', 'tz', 'rz']],
  ['ball', 'Ball', []],
] as const
const types = {
  document: 'd',
  tab: 'T2',
  mates: MATES.map(([id, label, freedoms]) => ({ id, label, help: `${label} help`, freedoms })),
  relations: ['linear', 'gear', 'rack_pinion', 'screw'].map((id) => ({ id, label: id, help: 'h' })),
}

const FRAME: { o: Vec3; z: Vec3; x: Vec3 } = { o: [0, 0, 0], z: [0, 0, 1], x: [1, 0, 0] }
type Side = { instance: string; kind: string; index: number | null; connector: string | null; what: string; world: typeof FRAME }

/** The server's side of a mate dialog, as far as the client can tell: the session's state and what each update does to it. */
function scripted(stored?: { id: string; type: string; a: Side; b: Side }) {
  const out = hinge()
  const state = {
    mate: stored?.id ?? 'M9',
    new: stored === undefined,
    type: stored?.type ?? 'fastened',
    name: stored === undefined ? 'Fastened 1' : 'Revolute 1',
    flip: false,
    offset: 0,
    angle: 0,
    a: (stored?.a ?? null) as Side | null,
    b: (stored?.b ?? null) as Side | null,
  }
  const side = (p: Record<string, unknown> | null): Side | null =>
    p === null ? null : 'connector' in p ? { instance: p['instance'] as string, kind: 'named', index: null, connector: p['connector'] as string, what: 'connector', world: FRAME } : { instance: p['instance'] as string, kind: p['kind'] as string, index: p['index'] as number, connector: null, what: p['kind'] === 'edge' ? 'circular edge' : 'flat face', world: FRAME }
  const view = () => ({
    ...state,
    dialog: 'md1',
    document: 'd',
    tab: 'T2',
    revision: out.revision,
    placements: Object.fromEntries(out.instances.map((i) => [i.id, i.T])),
    values: state.type === 'revolute' ? [{ dof: 'rz', label: 'Rotation', value: 0, unit: '°' }] : [],
    error: null,
    dof: 1,
  })
  const handler = (c: Call) => {
    if (c.method === 'GET' && c.url.endsWith('/outputs')) return { bytes: raw() }
    if (c.url.endsWith('/assembly/types')) return { json: types }
    if (c.url.endsWith('/edit')) return { json: { rebuilt: ['T2'] } }
    if (c.method !== 'POST') return undefined
    const body = (c.body ?? {}) as Record<string, unknown>
    if (c.url.endsWith('/mates/dialog')) {
      if (typeof body['type'] === 'string') state.type = body['type']
      const picks = (body['picks'] ?? []) as Record<string, unknown>[]
      if (picks[0] !== undefined) state.a = side(picks[0])
      if (picks[1] !== undefined) state.b = side(picks[1])
      return { status: 201, json: view() }
    }
    if (c.url.endsWith('/md1/update')) {
      for (const [k, v] of Object.entries(body)) {
        if (k === 'a' || k === 'b') state[k] = side(v as Record<string, unknown> | null)
        else if (k === 'turn90') state.angle = (state.angle + 90) % 360
        else (state as Record<string, unknown>)[k] = v
      }
      return { json: view() }
    }
    if (c.url.endsWith('/md1/accept')) return { json: { rebuilt: ['T2'], revision: out.revision + 1, mate: state.mate } }
    if (c.url.endsWith('/md1/cancel')) return { json: { cancelled: true } }
    return undefined
  }
  return { handler, state }
}

async function open(mode: 'desktop' | 'tablet', stored?: Parameters<typeof scripted>[0]) {
  window.localStorage.setItem(MODE_KEY, mode)
  const server = scripted(stored)
  const calls = stubFetch(server.handler)
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Hinge' }, tabs: [tab('T1', 'studio', 'hinge_parts'), tab('T2', 'assembly', 'hinge')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('button', { name: 'hinge' })
  fireEvent.click(screen.getByRole('button', { name: 'hinge' }))
  await screen.findByTestId('instance-list')
  await waitFor(() => expect(document.querySelector('[data-tool="m_revolute"], [data-group="mate"]')).not.toBeNull())
  await waitFor(() => expect(window.__freeshapeAssembly?.revision).toBe(hinge().revision))
  const post = (suffix: string) => calls.filter((c: Call) => c.method === 'POST' && c.url.endsWith(suffix))
  const selection = () => window.__freeshape!.shell.selection
  return { store, calls, post, selection, state: server.state }
}

const desk = (id: string) => document.querySelector(`.desktop-toolbar [data-tool="${id}"]`) as HTMLButtonElement
const keyDown = (chord: string) => act(() => void window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...keyOf(chord) })))

/** The first named edge or face of an instance, as a pick of the view makes it. */
function pickItem(instance: string, kind: 'edge' | 'face', point?: readonly [number, number, number]): SelectionItem {
  const out = hinge()
  const inst = out.instances.find((i) => i.id === instance)!
  const part = out.parts[inst.part!]!
  const [index, name] = [...part.names[kind]].find(([, n]) => n !== '')!
  return { id: idOfItem(kind, inst.part, name, index, null, instance), kind, part: inst.part, index, name, key: null, instance, ...(point === undefined ? {} : { point }) }
}
const commit = (s: () => SelectionStore, item: SelectionItem) =>
  act(() => {
    s().aim(item)
    s().commit(s().getAimed())
  })

it('Tablet: Revolute on the strip opens the dialog with Revolute pressed and the First field active; an edge pick on Base plate sends side a and moves to Second', async () => {
  const { post, selection } = await open('tablet')
  fireEvent.click(document.querySelector('[data-group="mate"]') as HTMLButtonElement, { detail: 0 })
  const list = await screen.findByRole('menu', { name: 'Mate' })
  fireEvent.click(within(list).getByRole('menuitem', { name: /Revolute/ }), { detail: 0 })
  const form = await screen.findByTestId('mate-form')
  expect(post('/mates/dialog')[0]!.body).toEqual({ type: 'revolute' })
  expect(post('/mates/dialog')[0]!.headers['X-Client-Id']).toBe('abcdefgh')
  expect(within(form).getByRole('radio', { name: 'Revolute' }).getAttribute('aria-checked')).toBe('true')
  expect(within(form).getByRole('radio', { name: 'Fastened' }).getAttribute('aria-checked')).toBe('false')
  expect(within(form).getAllByRole('radio').length).toBe(8)
  expect(within(form).getByTestId('mate-help').textContent).toBe('Revolute — Revolute help')
  const first = within(form).getByRole('group', { name: 'First mate connector' })
  const second = within(form).getByRole('group', { name: 'Second mate connector' })
  await waitFor(() => expect(first.hasAttribute('data-active')).toBe(true))
  expect(second.hasAttribute('data-active')).toBe(false)
  await waitFor(() => expect(selection().getState().field).toBe(true))
  const edge = pickItem('I1', 'edge')
  commit(selection, edge)
  await waitFor(() => expect(post('/update').length).toBe(1))
  expect(post('/update')[0]!.body).toEqual({ a: { instance: 'I1', kind: 'edge', index: edge.index + 1 } })
  await waitFor(() => expect(second.hasAttribute('data-active')).toBe(true))
  expect(first.hasAttribute('data-active')).toBe(false)
  expect(within(first).getByTestId('sel-chip').textContent).toContain('circular edge')
  // the second pick goes to side b
  commit(selection, pickItem('I2', 'edge'))
  await waitFor(() => expect(post('/update').length).toBe(2))
  expect(Object.keys(post('/update')[1]!.body as object)).toEqual(['b'])
})

it('Desktop: Shift+R opens a Revolute dialog; Shift+L a Slider; Shift+F a Fastened mate in an Assembly, and Esc cancels', async () => {
  const { post } = await open('desktop')
  for (const [chord, type] of [['Shift+R', 'revolute'], ['Shift+L', 'slider'], ['Shift+F', 'fastened']] as const) {
    keyDown(chord)
    await screen.findByTestId('mate-form')
    expect(post('/mates/dialog').at(-1)!.body).toEqual({ type })
    expect(document.querySelector(`[data-mate-type-button="${type}"]`)!.getAttribute('aria-checked')).toBe('true')
    keyDown('Escape')
    await waitFor(() => expect(screen.queryByTestId('mate-form')).toBeNull())
  }
  expect(post('/cancel').length).toBe(3)
})

it('Desktop: the toolbar button opens the dialog at (12, 12) of the view; a pressed type button changes the open dialog\'s type', async () => {
  const { post } = await open('desktop')
  expect(desk('m_revolute').disabled).toBe(false)
  fireEvent.click(desk('m_revolute'))
  const form = await screen.findByTestId('mate-form')
  const area = document.querySelector<HTMLElement>('.dialog-floating')!
  expect(area.style.left).toBe('12px')
  expect(area.style.top).toBe('12px')
  expect(desk('m_revolute').getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(desk('m_slider'))
  await waitFor(() => expect(post('/update').length).toBe(1))
  expect(post('/update')[0]!.body).toEqual({ type: 'slider' })
  await waitFor(() => expect(form.getAttribute('data-mate-type')).toBe('slider'))
  expect(post('/mates/dialog').length).toBe(1)
})

it('Flip sends flip: true; Rotate 90 sends turn90; an Offset typed in Desktop mode is sent as a number; Enter accepts', async () => {
  const { post, selection } = await open('desktop')
  fireEvent.click(desk('m_fastened'))
  const form = await screen.findByTestId('mate-form')
  commit(selection, pickItem('I1', 'edge'))
  await waitFor(() => expect(post('/update').length).toBe(1))
  commit(selection, pickItem('I2', 'edge'))
  await waitFor(() => expect(post('/update').length).toBe(2))
  fireEvent.click(within(form).getByRole('button', { name: /Flip/ }))
  await waitFor(() => expect(post('/update').length).toBe(3))
  expect(post('/update')[2]!.body).toEqual({ flip: true })
  expect(within(form).getByRole('button', { name: /Flip/ }).getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(within(form).getByRole('button', { name: /Rotate 90/ }))
  await waitFor(() => expect(post('/update').length).toBe(4))
  expect(post('/update')[3]!.body).toEqual({ turn90: true })
  const offset = within(form).getByRole('textbox', { name: 'Offset' }) as HTMLInputElement
  fireEvent.change(offset, { target: { value: '12.5' } })
  fireEvent.keyDown(offset, { key: 'Tab' })
  await waitFor(() => expect(post('/update').length).toBe(5))
  expect(post('/update')[4]!.body).toEqual({ offset: 12.5 })
  const angle = within(form).getByRole('textbox', { name: 'Angle' }) as HTMLInputElement
  expect(angle.value).toBe('90')
  // Enter in the page accepts (no field has the focus)
  ;(document.activeElement as HTMLElement | null)?.blur()
  keyDown('Enter')
  await waitFor(() => expect(post('/accept').length).toBe(1))
  await waitFor(() => expect(screen.queryByTestId('mate-form')).toBeNull())
})

it('Tablet: Offset is set on the pad, not in a native input; Accept with a side empty says so and sends nothing', async () => {
  const { post, selection } = await open('tablet')
  fireEvent.click(document.querySelector('[data-group="mate"]') as HTMLButtonElement, { detail: 0 })
  fireEvent.click(within(await screen.findByRole('menu', { name: 'Mate' })).getByRole('menuitem', { name: /Revolute/ }), { detail: 0 })
  const form = await screen.findByTestId('mate-form')
  expect(form.querySelector('input, textarea, select, [contenteditable]')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Accept' }))
  expect((await screen.findByTestId('dialog-problem')).textContent).toBe('Pick a mate connector on each of the two parts first.')
  expect(post('/accept').length).toBe(0)
  commit(selection, pickItem('I1', 'edge'))
  commit(selection, pickItem('I2', 'edge'))
  await waitFor(() => expect(post('/update').length).toBe(2))
  fireEvent.click(within(form).getByRole('button', { name: /^Offset/ }))
  const pad = await screen.findByTestId('pad')
  fireEvent.click(within(pad).getByRole('button', { name: '7' }))
  fireEvent.click(within(pad).getByRole('button', { name: 'Enter' }))
  await waitFor(() => expect(post('/update').length).toBe(3))
  expect(post('/update')[2]!.body).toEqual({ offset: 7 })
  expect(document.querySelector('input, textarea, select, [contenteditable]')).toBeNull()
})

it('the Mates list: Delete sends delete_mates, Suppress sends set_suppressed, Rename sends rename; Edit opens the dialog for that mate', async () => {
  const stored = { id: 'M4', type: 'revolute', a: { instance: 'I1', kind: 'edge', index: 2, connector: null, what: 'circular edge', world: FRAME }, b: { instance: 'I2', kind: 'edge', index: 3, connector: null, what: 'circular edge', world: FRAME } }
  const { post } = await open('desktop', stored)
  const rows = () => [...document.querySelectorAll<HTMLElement>('[data-mate-row]')]
  const mate = rows().find((r) => r.dataset['mateRow'] === 'M4')!
  expect(mate).toBeTruthy()
  fireEvent.doubleClick(mate)
  const form = await screen.findByTestId('mate-form')
  expect(post('/mates/dialog')[0]!.body).toEqual({ mate: 'M4' })
  expect(form.getAttribute('data-mate-type')).toBe('revolute')
  expect(within(form).getByRole('group', { name: 'First mate connector' }).textContent).toContain('circular edge')
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(screen.queryByTestId('mate-form')).toBeNull())
})

it('Tablet: a long press on a mate row opens its menu: Delete sends delete_mates', async () => {
  const { calls } = await open('tablet')
  const row = document.querySelector<HTMLElement>('[data-mate-row]')!
  fireEvent.pointerDown(row, { pointerType: 'touch', clientX: 10, clientY: 10 })
  // the long press is the component's own 500 ms hold
  await act(async () => {
    await new Promise((r) => setTimeout(r, 650))
  })
  fireEvent.pointerUp(row, { pointerType: 'touch', clientX: 10, clientY: 10 })
  const menu = await screen.findByRole('menu', { name: /menu$/ })
  expect(within(menu).getAllByRole('menuitem').map((b) => b.textContent)).toEqual(['Edit…', 'Rename…', 'Suppress', 'Delete'])
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Delete' }))
  await waitFor(() => expect(calls.some((c) => c.url.endsWith('/edit'))).toBe(true))
  expect(calls.find((c) => c.url.endsWith('/edit'))!.body).toEqual({ op: 'delete_mates', ids: [row.dataset['mateRow']] })
})

it('Shift+F starts Fillet in a Part Studio and no mate dialog', async () => {
  window.localStorage.setItem(MODE_KEY, 'desktop')
  const { bytes } = fixture('bracket')
  const calls = stubFetch((c: Call) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.includes('/features/types')) return { json: { document: 'd', tab: 'T1', revision: 1, types: [{ type: 'fillet', label: 'Fillet', standard: true, can_start: true }] } }
    if (c.method === 'POST' && c.url.endsWith('/features/dialog')) return { status: 201, json: { dialog: 'did9', document: 'd', tab: 'T1', feature: null, type: 'fillet', name: 'Fillet 1', revision: 1, description: { type: 'fillet', custom: [], fields: [] }, values: {}, fields: {}, active: null } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  act(() => window.__freeshape!.shell.selection.set([{ id: 'face|F2/1|Face4', kind: 'face', part: 'F2/1', index: 3, name: 'Face4', key: null }]))
  keyDown('Shift+F')
  await waitFor(() => expect(calls.some((c) => c.url.endsWith('/features/dialog'))).toBe(true))
  expect(calls.find((c) => c.url.endsWith('/features/dialog'))!.body).toMatchObject({ type: 'fillet' })
  expect(calls.some((c) => c.url.includes('/mates/dialog'))).toBe(false)
  store.stop()
})

it('the Insert panel gives way to the mate dialog, and its button is off while the dialog is open', async () => {
  const { post } = await open('desktop')
  fireEvent.click(desk('insert'))
  await screen.findByRole('dialog', { name: /Insert/ })
  fireEvent.click(desk('m_fastened'))
  await screen.findByTestId('mate-form')
  // the Insert panel gave way, and its button is off while the mate dialog has the area
  expect(screen.queryByRole('dialog', { name: /Insert/ })).toBeNull()
  expect(desk('insert').disabled).toBe(true)
  expect(post('/mates/dialog').length).toBe(1)
})

it('pure helpers: a selection becomes picks (a connector comes later), a click point is carried back to the committed placement', () => {
  const edge = pickItem('I1', 'edge')
  const face = pickItem('I2', 'face', [1, 2, 3])
  expect(sidePick({ ...edge, instance: undefined })).toBeNull()
  expect(sidePick(face)).toEqual({ instance: 'I2', kind: 'face', index: face.index + 1, point: [1, 2, 3] })
  expect(preselection([edge, pickItem('I1', 'face'), face])).toEqual({ picks: [{ instance: 'I1', kind: 'edge', index: edge.index + 1 }, { instance: 'I2', kind: 'face', index: face.index + 1, point: [1, 2, 3] }], later: [] })
  const conn = { ...edge, kind: 'connector' as const, key: 'C1', instance: 'I1' }
  expect(preselection([conn, face])).toEqual({ picks: [], later: [{ instance: 'I1', connector: 'C1' }, { instance: 'I2', kind: 'face', index: face.index + 1, point: [1, 2, 3] }] })
  const out = hinge()
  const shifted = out.instances.map((i) => [i.id, i.id === 'I2' ? i.T.map((r, k) => (k === 0 ? [r[0]!, r[1]!, r[2]!, r[3]! + 10] : [...r])) : i.T] as const)
  const moved = pickOf(face, out, Object.fromEntries(shifted.map(([id, t]) => [id, t.map((r) => [...r])])))
  expect(moved !== null && 'point' in moved ? moved.point : null).toEqual([-9, 2, 3])
})

it('the preview draws each instance where the session put it, with the two ends of the mate; the stored mate is replaced by it', () => {
  const out = hinge()
  const T = out.instances[1]!.T.map((r, k) => (k === 0 ? [r[0]!, r[1]!, r[2]!, r[3]! + 5] : [...r]))
  const view: MateView = { mate: out.mates[0]!.id, new: false, type: 'revolute', name: 'R', flip: false, offset: 0, angle: 0, a: { instance: 'I1', kind: 'edge', index: 1, connector: null, what: 'circular edge', world: FRAME }, b: null, placements: { [out.instances[1]!.id]: T }, values: [], error: null, dof: 3 }
  const drawn = previewOutputs(out, view)
  expect(drawn.instances[1]!.T).toEqual(T)
  expect(drawn.instances[0]).toBe(out.instances[0])
  expect(drawn.mates.length).toBe(out.mates.length)
  expect(drawn.mates.at(-1)!.ends[0]).toEqual(FRAME)
  expect(drawn.mates.at(-1)!.ends[1]).toBeNull()
  expect(drawn.dof).toBe(3)
})
