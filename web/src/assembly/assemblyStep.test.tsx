import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Explorer } from '../files/Explorer'
import type { StepInsertReply } from '../files/filesApi'
import { MODE_KEY } from '../shell/mode'
import type { Mode } from '../shell/mode'
import { Shell } from '../shell/Shell'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { FakeXhr, installFakeXhr } from '../test/fakeXhr'
import { planDrop } from '../viewport/fileDrop'

// Card I11.11: STEP into an Assembly from the Insert panel's From file… and Component library…, from the explorer, and by a drop.
beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  installFakeXhr()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const fixture = (name: string): ArrayBuffer => {
  const b = readFileSync(resolve(process.cwd(), 'fixtures', name))
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
}
const TABS = [tab('T1', 'studio', 'hinge_parts'), tab('T2', 'assembly', 'hinge')]
const REPLY = (names: string[]): StepInsertReply => ({ document: 'd', tab: { id: 'T2' }, rebuilt: ['T2'], tabs: TABS, instances: names.map((name, i) => ({ id: `I${i}`, name })) })
const ROOTS = { roots: [{ name: 'lib', files: 1, max_upload: 1000 }], export_roots: [] }
const ENTRY = { id: 'e1', name: 'Bolt', file: 'bolt.step', path: 'bolt.step', size: 100, description: '', tags: [], licence: null, authors: null, url: null }
const step = (name = 'box.step', size = 13) => new File(['x'.repeat(size)], name)

async function shell(mode: Mode) {
  window.localStorage.setItem(MODE_KEY, mode)
  const calls = stubFetch((c: Call) => {
    if (c.url === '/files') return { json: ROOTS }
    if (c.url.startsWith('/files/lib')) return { json: { root: 'lib', dir: '', q: '', dirs: [], entries: [ENTRY], total: 1, offset: 0, limit: 200 } }
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes: fixture('hinge.studio.fsw') } as never
    if (c.method === 'GET' && c.url.endsWith('/outputs')) return { bytes: fixture('hinge.assembly.fsw') } as never
    if (c.method === 'POST' && c.url === '/documents/d/tabs/T2/import') return { json: REPLY(['Bolt']) }
    if (c.url.endsWith('/assembly/types')) return { json: { document: 'd', tab: 'T2', mates: [{ id: 'fastened', label: 'Fastened', help: 'h', freedoms: [] }], relations: [{ id: 'linear', label: 'Linear', help: 'h' }] } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Hinge' }, tabs: TABS, read_only: false, holder: holderOf('x') })
  fireEvent.click(await screen.findByRole('button', { name: 'hinge' }))
  await screen.findByTestId('instance-list')
  return { store, calls }
}
const openPanel = async () => {
  fireEvent.click((document.querySelector('.desktop-toolbar [data-tool="insert"]') ?? document.querySelector('.tool-strip > [data-tool="insert"]')) as HTMLButtonElement)
  return screen.findByTestId('insert-panel')
}
const fileInputs = () => document.querySelectorAll('input[type="file"]')

describe('the Insert panel', () => {
  it('From file… makes a STEP-only input on demand, posts the bytes to the Assembly tab and says what went in', async () => {
    const { store } = await shell('tablet')
    const p = await openPanel()
    expect(fileInputs().length).toBe(0)
    const click = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    fireEvent.click(within(p).getByRole('button', { name: 'From file…' }))
    expect(click).toHaveBeenCalled()
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(input.accept).toBe('.step,.stp')
    fireEvent.change(input, { target: { files: [step('two.step')] } })
    expect(fileInputs().length).toBe(0)
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    const x = FakeXhr.all[0]!
    expect(x.url).toBe('/documents/d/tabs/T2/import?name=two.step')
    expect(x.headers['Content-Type']).toBe('application/octet-stream')
    act(() => x.reply(200, REPLY(['A', 'B'])))
    await waitFor(() => expect(screen.getByTestId('insert-said').textContent).toBe('Inserted 2 parts from two.step'))
    store.stop()
  })

  it("shows the server's text for a refusal and leaves no input behind", async () => {
    const { store } = await shell('desktop')
    const p = await openPanel()
    vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    fireEvent.click(within(p).getByRole('button', { name: 'From file…' }))
    fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [step('x.step')] } })
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    act(() => FakeXhr.all[0]!.reply(400, { error: 'insert_step: the file holds no solid' }))
    await waitFor(() => expect(screen.getByTestId('insert-error').textContent).toContain('insert_step: the file holds no solid'))
    expect(fileInputs().length).toBe(0)
    store.stop()
  })

  it('closing the panel with the picker open removes its input', async () => {
    const { store } = await shell('desktop')
    const p = await openPanel()
    vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    fireEvent.click(within(p).getByRole('button', { name: 'From file…' }))
    expect(fileInputs().length).toBe(1)
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Insert parts' })).getByRole('button', { name: /close/i }))
    await waitFor(() => expect(screen.queryByTestId('insert-panel')).toBeNull())
    expect(fileInputs().length).toBe(0)
    store.stop()
  })

  it('Component library… opens the explorer in its Assembly mode; Insert into this Assembly posts {root, entry}; closing it shows the panel with what went in', async () => {
    const { store, calls } = await shell('tablet')
    const p = await openPanel()
    fireEvent.click(within(p).getByRole('button', { name: 'Component library…' }))
    expect(screen.queryByTestId('insert-panel')).toBeNull()
    await screen.findByTestId('explorer')
    fireEvent.click(await screen.findByRole('button', { name: /Bolt/ }))
    expect(screen.queryByRole('button', { name: 'Add to this Part Studio' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Open in a new Part Studio' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Insert into this Assembly' }))
    await screen.findByText('Inserted Bolt')
    const post = calls.find((c: Call) => c.method === 'POST' && c.url === '/documents/d/tabs/T2/import')!
    expect(post.body).toEqual({ root: 'lib', entry: 'e1' })
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Files' })).getByRole('button', { name: /close/i }))
    await waitFor(() => expect(screen.getByTestId('insert-panel')).toBeTruthy())
    expect(screen.getByTestId('insert-said').textContent).toBe('Inserted Bolt')
    store.stop()
  })
})

describe('the explorer on an Assembly', () => {
  const target = { document: 'd', tab: 'T2', studio: false, assembly: true, readOnly: false }
  it('From this device… is STEP only and posts to the Assembly tab without new_tab', async () => {
    stubFetch((c) => (c.url === '/files' ? { json: ROOTS } : undefined))
    const onInserted = vi.fn()
    render(<Explorer mode="add" placement="float" uiMode="desktop" target={target} onOpenDocument={vi.fn()} onImported={vi.fn()} onInserted={onInserted} onClose={vi.fn()} />)
    expect((screen.getByTestId('ex-device-input') as HTMLInputElement).accept).toBe('.step,.stp')
    expect(screen.getAllByRole('button', { name: /From this device/ }).length).toBe(1)
    fireEvent.change(screen.getByTestId('ex-device-input'), { target: { files: [step('box.step')] } })
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    expect(FakeXhr.all[0]!.url).toBe('/documents/d/tabs/T2/import?name=box.step')
    act(() => FakeXhr.all[0]!.reply(200, REPLY(['box'])))
    await screen.findByText('Inserted box')
    expect(onInserted).toHaveBeenCalledWith(expect.objectContaining({ document: 'd' }), 'Inserted box')
  })
})

describe('planDrop on an Assembly', () => {
  const f = (n: string) => new File(['x'], n)
  const ctx = { desktop: true, onView: true, blocked: null, assembly: true }
  it('inserts STEP files in order, names a BREP with why, and refuses elsewhere', () => {
    expect(planDrop([f('a.STEP'), f('b.stp')], ctx)).toEqual({ import: [expect.anything(), expect.anything()], say: null })
    const mixed = planDrop([f('a.step'), f('b.brep'), f('c.png')], ctx)
    expect(mixed.import.map((x) => x.name)).toEqual(['a.step'])
    expect(mixed.say).toContain('“b.brep” is a BREP file. An Assembly takes STEP files only')
    expect(mixed.say).toContain('“c.png” is not a STEP file')
    expect(planDrop([f('b.brp')], ctx).import).toEqual([])
    expect(planDrop([f('a.step')], { ...ctx, desktop: false }).say).toMatch(/Desktop mode/)
    expect(planDrop([f('a.step')], { ...ctx, onView: false }).say).toMatch(/Drop a STEP file on the view/)
    expect(planDrop([f('a.step')], { ...ctx, blocked: 'read-only' })).toEqual({ import: [], say: 'read-only' })
    expect(planDrop([f('a.brep')], { ...ctx, assembly: false }).import.length).toBe(1)
  })
})

describe('a drop on an Assembly view', () => {
  it('Desktop: a STEP inserts into the tab (no new_tab); a BREP says why and uploads nothing', async () => {
    const { store } = await shell('desktop')
    const view = screen.getByTestId('shell-viewport')
    fireEvent.drop(view, { dataTransfer: { types: ['Files'], files: [step('p.brep')] } })
    await screen.findByText(/“p.brep” is a BREP file/)
    expect(FakeXhr.all.length).toBe(0)
    fireEvent.drop(view, { dataTransfer: { types: ['Files'], files: [step('box.step')] } })
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    expect(FakeXhr.all[0]!.url).toBe('/documents/d/tabs/T2/import?name=box.step')
    act(() => FakeXhr.all[0]!.reply(200, REPLY(['box'])))
    await screen.findByText('Inserted box')
    store.stop()
  })
})
