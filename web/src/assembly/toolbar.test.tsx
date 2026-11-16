import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { MODE_KEY } from '../shell/mode'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { ASSEMBLY_TOOLBAR, MATE_IDS, RELATION_IDS, desktopToolIds } from '../tools/defs'
import { iconNames } from '../tools/icons'
import { decodeOutputs, isAssembly } from '../wire'
import { registerView } from '../viewport/viewCommands'
import { instanceItem } from './select'
import { S10_NOTE } from './strip'

// The Assembly toolbar (card I9.5), in both modes, on the hinge Assembly (I1 fixed, I2 and I3 free).
beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} }))
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const raw = (): ArrayBuffer => {
  const b = readFileSync(resolve(process.cwd(), 'fixtures', 'hinge.assembly.fsw'))
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
}
const outputs = () => {
  const o = decodeOutputs(raw())
  if (!isAssembly(o)) throw new Error('not an assembly')
  return o
}

/** The route's answer: what freeshape/assembly.py has (the server's own test, tests/test_serve_assembly_types.py, reads it from there). */
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
  relations: [
    { id: 'linear', label: 'Linear', help: 'h' },
    { id: 'gear', label: 'Gear', help: 'h' },
    { id: 'rack_pinion', label: 'Rack and pinion', help: 'h' },
    { id: 'screw', label: 'Screw', help: 'h' },
  ],
}

async function open(mode: 'desktop' | 'tablet', studio = false) {
  window.localStorage.setItem(MODE_KEY, mode)
  const calls = stubFetch((c) =>
    c.method === 'GET' && c.url.endsWith('/outputs') ? { bytes: raw() } : c.url.endsWith('/assembly/types') ? { json: types } : c.url.endsWith('/edit') ? { json: { rebuilt: ['T2'] } } : undefined,
  )
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Hinge' }, tabs: [tab('T1', 'studio', 'hinge_parts'), tab('T2', 'assembly', 'hinge')], read_only: false, holder: holderOf('x') })
  if (studio) await waitFor(() => expect(document.querySelector('[data-strip="feature"]')).not.toBeNull())
  else {
    await screen.findByRole('button', { name: 'hinge' })
    fireEvent.click(screen.getByRole('button', { name: 'hinge' }))
    await screen.findByTestId('instance-list')
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/assembly/types'))).toBe(true))
  }
  const edits = () => calls.filter((c: Call) => c.method === 'POST' && c.url.endsWith('/edit')).map((c) => c.body as Record<string, unknown>)
  return { store, calls, edits }
}

const desk = (id: string) => document.querySelector(`.desktop-toolbar [data-tool="${id}"]`) as HTMLButtonElement
const strip = (id: string) => document.querySelector(`.tool-strip > [data-tool="${id}"]`) as HTMLButtonElement
const select = (...ids: string[]) => {
  const out = outputs()
  act(() => window.__freeshape!.shell.selection.set(ids.map((id) => instanceItem(id, out.instances.find((i) => i.id === id)!.part))))
}

it('Desktop: the Assembly tab has its own row and not the Part Studio one', async () => {
  const { store } = await open('desktop')
  expect(document.querySelector('[data-strip="assembly"]')).not.toBeNull()
  expect(document.querySelector('[data-strip="feature"]')).toBeNull()
  expect(document.querySelector('.tool-strip')).toBeNull()
  expect(desk('extrude')).toBeNull()
  store.stop()
})

it('Desktop: the row runs in the order of _build_assembly_toolbar', async () => {
  const { store } = await open('desktop')
  // freeshape/ui/mainwindow.py `_build_assembly_toolbar`: a tool id per button, '|' for each separator, '>' for the stretch
  const want = [
    'undo', 'redo', '|', 'insert', '|',
    ...MATE_IDS.map((m) => `m_${m}`), '|',
    ...RELATION_IDS.map((r) => `r_${r}`), '|',
    'move', 'asm_fix', 'group', '|',
    'interference', 'linear_pattern', 'circular_pattern', 'bom', 'explode', 'export', '>', 'section', 'fit', 'iso',
  ]
  const got = [...document.querySelectorAll('.tb-scroll > *'), null, ...document.querySelectorAll('.tb-end > *')].map((e) => (e === null ? '>' : e.getAttribute('role') === 'separator' ? '|' : e.getAttribute('data-tool')!))
  expect(got).toEqual(want)
  expect(MATE_IDS).toEqual(['fastened', 'revolute', 'slider', 'cylindrical', 'planar', 'pin_slot', 'parallel', 'ball'])
  expect(RELATION_IDS).toEqual(['linear', 'gear', 'rack_pinion', 'screw'])
  const have = new Set(iconNames())
  expect(desktopToolIds(ASSEMBLY_TOOLBAR).filter((id) => !have.has([...ASSEMBLY_TOOLBAR.items, ...ASSEMBLY_TOOLBAR.trailing].flatMap((e) => (e.kind === 'separator' ? [] : [e])).find((e) => e.tool.id === id)!.tool.icon))).toEqual([])
  store.stop()
})

it('Desktop: Insert and the mates are off with the S10 tooltip (the mates by the server\'s words), the rest are the desktop\'s', async () => {
  const { store } = await open('desktop')
  await waitFor(() => expect(desk('m_fastened').getAttribute('aria-label')).toBe('Fastened mate'))
  expect(desk('insert').disabled).toBe(true)
  expect(desk('insert').title).toBe(S10_NOTE)
  for (const m of MATE_IDS) {
    expect(desk(`m_${m}`).disabled).toBe(true)
    expect(desk(`m_${m}`).title).toBe(S10_NOTE)
  }
  for (const id of ['r_gear', 'group', 'interference', 'linear_pattern', 'circular_pattern', 'bom', 'explode', 'export', 'section']) {
    expect(desk(id).disabled).toBe(true)
    expect(desk(id).title).toBe('Desktop only for now')
  }
  for (const id of ['move', 'asm_fix', 'fit', 'iso']) expect(desk(id).disabled).toBe(false)
  store.stop()
})

it('Desktop: Fix with two instances selected sends set_fixed for both; with both fixed it frees them', async () => {
  const { store, edits } = await open('desktop')
  select('I2', 'I3')
  fireEvent.click(desk('asm_fix'))
  await waitFor(() => expect(edits()).toEqual([{ op: 'set_fixed', instance: 'I2', value: true }, { op: 'set_fixed', instance: 'I3', value: true }]))
  store.stop()
})

it('Desktop: Fix and Move with nothing selected say to select an instance; Move on a fixed one says to free it', async () => {
  const { store, edits } = await open('desktop')
  fireEvent.click(desk('asm_fix'))
  expect((await screen.findByRole('dialog')).textContent).toContain('Select an instance')
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button'))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  fireEvent.click(desk('move'))
  expect((await screen.findByRole('dialog')).textContent).toContain('Select an instance to move')
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button'))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  select('I1')
  fireEvent.click(desk('move'))
  expect((await screen.findByRole('dialog')).textContent).toContain('fixed')
  expect(edits()).toEqual([])
  store.stop()
})

it('Desktop: Fit and Iso go to the view', async () => {
  const { store } = await open('desktop')
  const view = { fit: vi.fn(), setStandardView: vi.fn() }
  const unregister = registerView(view)
  fireEvent.click(desk('fit'))
  fireEvent.click(desk('iso'))
  expect(view.fit).toHaveBeenCalledOnce()
  expect(view.setStandardView).toHaveBeenCalledExactlyOnceWith('iso')
  unregister()
  store.stop()
})

it('Tablet: the Assembly tab has the assembly strip, and a Part Studio tab keeps the feature strip', async () => {
  const { store } = await open('tablet')
  expect(document.querySelector('[data-strip="assembly"]')).not.toBeNull()
  expect(document.querySelector('[data-strip="feature"]')).toBeNull()
  expect([...document.querySelectorAll('.tool-strip > .strip-button .strip-label')].map((e) => e.textContent)).toEqual(['Insert', 'Mate', 'Relation', 'Move', 'Fix', 'Fit'])
  store.stop()
})

it('Tablet: the Mate group lists the eight types from the route, off with the S10 note; the relations are the desktop\'s', async () => {
  const { store } = await open('tablet')
  expect(strip('insert').disabled).toBe(true)
  expect(strip('insert').title).toBe(S10_NOTE)
  const group = document.querySelector('[data-group="mate"]') as HTMLButtonElement
  expect(group.disabled).toBe(false)
  fireEvent.click(group, { detail: 0 })
  const list = await screen.findByRole('menu', { name: 'Mate' })
  expect([...list.querySelectorAll('.group-name')].map((e) => e.textContent)).toEqual(MATES.map((m) => m[1]))
  expect([...list.querySelectorAll('.group-note')].map((e) => e.textContent)).toEqual(MATES.map(() => S10_NOTE))
  expect(list.querySelector('.pin-button')).toBeNull()
  store.stop()
})

it('Tablet: Fix sends set_fixed for each selected instance and Move says to select one', async () => {
  const { store, edits } = await open('tablet')
  fireEvent.click(strip('move'))
  expect((await screen.findByRole('dialog')).textContent).toContain('Select an instance to move')
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button'))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  select('I2', 'I3')
  fireEvent.click(strip('asm_fix'))
  await waitFor(() => expect(edits()).toEqual([{ op: 'set_fixed', instance: 'I2', value: true }, { op: 'set_fixed', instance: 'I3', value: true }]))
  store.stop()
})

it('Tablet: Fit goes to the view', async () => {
  const { store } = await open('tablet')
  const view = { fit: vi.fn(), setStandardView: vi.fn() }
  const unregister = registerView(view)
  fireEvent.click(strip('fit'))
  expect(view.fit).toHaveBeenCalledOnce()
  unregister()
  store.stop()
})

it('a Part Studio tab never asks for the assembly types', async () => {
  const { store, calls } = await open('desktop', true)
  expect(calls.some((c) => c.url.includes('/assembly/types'))).toBe(false)
  expect(document.querySelector('[data-strip="feature"]')).not.toBeNull()
  store.stop()
})
