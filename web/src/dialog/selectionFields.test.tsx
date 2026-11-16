import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { bracketWithPart, fixture } from '../test/fixtures'
import { makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { idOfItem } from '../selection/store'
import type { SelectionItem, SelectionStore } from '../selection/store'
import type { Description, FieldSpec, OpenReply, SelItem } from './dialogApi'
import { afterPick, BASE_LOADING, baseIndex, highlightItems, itemLabel, pickKinds, readableError } from './selectionFields'

// Selection fields of the feature dialog (card I5.7) in the whole Shell, against a scripted server: the dialog's edge
// field is active, a Commit in the view goes to the refs route, and the answer joins the field through a dialog update.
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

const sel = (key: string, label: string, extra: Partial<FieldSpec> = {}): FieldSpec => ({ key, kind: 'sel', standard: true, visible: true, label, want: ['edge'], multi: true, number: false, list: false, tip: null, ...extra })

const bracket = fixture('bracket').outputs
const part = bracket.parts[0]!
const edgeNames = [...part.names.edge].filter(([, n]) => n !== '')
const itemAt = (i: number): SelectionItem => {
  const [index, name] = edgeNames[i]!
  return { id: idOfItem('edge', part.id, name, index, null), kind: 'edge', part: part.id, index, name, key: null }
}
const refOf = (i: number) => ({ part: part.id, name: edgeNames[i]![1], type: 'line' })
const stored = (i: number): SelItem => ({ ok: true, kind: 'edge', ref: refOf(i), name: edgeNames[i]![1] })

const open = (fields: FieldSpec[], items: Record<string, SelItem[]> = {}, active: string | null = null): OpenReply => ({
  dialog: 'did1',
  document: 'd',
  tab: 'T1',
  feature: 'F2',
  name: 'Fillet 1',
  revision: 1,
  description: { type: 'fillet', fields, custom: [] } as Description,
  values: { radius: { value: 2, text: '2' } },
  fields: items,
  active,
})

interface Script {
  open: OpenReply
  refs?: (pick: Record<string, unknown>) => object
  /** The dialog's base model (card F3) the server sends; the committed bracket when left out, none for null. */
  base?: ArrayBuffer | null
}

async function setup(script: Script) {
  const { bytes } = fixture('bracket')
  const calls = stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.endsWith('/features/F2/dialog')) return { status: 201, json: script.open }
    if (c.url.endsWith('/update')) {
      const values = (c.body as { values: Record<string, SelItem[]> }).values
      return { json: { ...script.open, fields: { ...script.open.fields, ...values }, errors: {}, preview: null } }
    }
    if (c.url.endsWith('/refs')) return { json: { document: 'd', tab: 'T1', revision: 1, items: [script.refs?.((c.body as { picks: Record<string, unknown>[] }).picks[0]!)] } }
    if (c.url.endsWith('/cancel')) return { json: { cancelled: true } }
    if (c.method === 'GET' && c.url.endsWith('/dialog/did1/base')) return script.base === null ? undefined : { bytes: script.base ?? bytes, headers: { 'X-Revision': String(bracket.revision) } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: null })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(bracket.revision))
  const el = screen.getByRole('button', { name: 'Extrude 1' })
  fireEvent.click(el)
  fireEvent.click(el)
  await screen.findByTestId('dialog-form')
  const calling = (suffix: string) => calls.filter((c: Call) => c.method === 'POST' && c.url.endsWith(suffix))
  const selection = () => window.__freeshape!.shell.selection
  // the dialog has taken the view over and chosen its field
  await waitFor(() => expect(selection().pickFilter).toBeDefined())
  if (script.open.active !== null || Object.values(script.open.fields).every((l) => l.length === 0)) await waitFor(() => expect(selection().getState().field).toBe(true))
  // the view draws the dialog's base once it has arrived: picks resolve against it
  if (script.base !== null) await waitFor(() => expect(screen.getByTestId('shell-viewport').hasAttribute('data-dialog-base')).toBe(true))
  return { calls, calling, selection }
}

const group = (name: string) => screen.getByRole('group', { name })
const chips = (name: string) => within(group(name)).queryAllByTestId('sel-chip').map((c) => c.textContent)
const commit = (s: () => SelectionStore, item: SelectionItem | null) =>
  act(() => {
    if (item) s().aim(item)
    s().commit(item === null ? null : s().getAimed())
  })
const lab = (i: number) => `Edge ${edgeNames[i]![0] + 1} of ${part.name}`
const ok = (i: number) => ({ ok: true, kind: 'edge', ref: refOf(i), name: edgeNames[i]![1] })

it('with the edge field active the pick filter handed to the viewport is edges only', async () => {
  const { selection } = await setup({ open: open([sel('edges', 'Edges to fillet')], {}, null) })
  // an empty field is the active one when the dialog opens
  expect(selection().pickFilter).toEqual(['edge'])
  expect(group('Edges to fillet').getAttribute('data-active')).toBe('')
  // a Multi field turns Multi on, and the chip hides Clear
  expect(selection().multi).toBe(true)
  expect(screen.queryByTestId('selection-clear')).toBeNull()
})

it('a Commit on an edge goes to the refs route, adds a chip with the returned name, and the dialog update carries the list', async () => {
  const { calling, selection } = await setup({ open: open([sel('edges', 'Edges to fillet')], { edges: [stored(0)] }, null), refs: () => ok(1) })
  fireEvent.click(within(group('Edges to fillet')).getByRole('button', { name: /Tap, then pick/ }))
  expect(selection().pickFilter).toEqual(['edge'])
  commit(selection, itemAt(1))
  await waitFor(() => expect(calling('/update').length).toBe(1))
  const picked = itemAt(1)
  // the pick carries the dialog (card F3): the server resolves it against the model before the feature, which the view draws
  expect(calling('/refs')[0]!.body).toEqual({ picks: [{ part: part.id, kind: 'edge', index: picked.index + 1, want: ['edge'] }], dialog: 'did1' })
  expect(calling('/refs')[0]!.headers['X-Client-Id']).toBe('abcdefgh')
  expect(calling('/update')[0]!.body).toEqual({ values: { edges: [stored(0), stored(1)] } })
  await waitFor(() => expect(chips('Edges to fillet')).toEqual([lab(0), lab(1)]))
  // the picks went to the field: the view highlights the field's two edges
  expect(selection().getState().items.map((i) => i.name)).toEqual([edgeNames[0]![1], edgeNames[1]![1]])
})

it('a refused pick shows the server reason and adds nothing', async () => {
  const { calling, selection } = await setup({ open: open([sel('edges', 'Edges to fillet')], { edges: [stored(0)] }, 'edges'), refs: () => ({ ok: false, code: 'wrong_kind', reason: 'A vertex cannot be used here; an edge is needed' }) })
  commit(selection, itemAt(1))
  expect((await screen.findAllByRole('alert')).map((a) => a.textContent)).toContain('A vertex cannot be used here; an edge is needed')
  expect(calling('/update').length).toBe(0)
  expect(chips('Edges to fillet')).toEqual([lab(0)])
})

it('removing a chip sends an update without that item', async () => {
  const { calling } = await setup({ open: open([sel('edges', 'Edges to fillet')], { edges: [stored(0), stored(1)] }, null) })
  fireEvent.click(screen.getByRole('button', { name: `Remove ${lab(0)}` }))
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/update')[0]!.body).toEqual({ values: { edges: [stored(1)] } })
  await waitFor(() => expect(chips('Edges to fillet')).toEqual([lab(1)]))
})

it('a release over empty space changes nothing; the general selection is untouched while the dialog is open and back after it closes', async () => {
  const { calling, selection } = await setup({ open: open([sel('edges', 'Edges to fillet')], { edges: [stored(0)] }, 'edges'), refs: () => ok(1) })
  commit(selection, null)
  expect(calling('/refs').length).toBe(0)
  expect(chips('Edges to fillet')).toEqual([lab(0)])
  fireEvent.click(within(document.querySelector<HTMLElement>('.dialog-header')!).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(selection().getState().items).toEqual([])
  expect(selection().multi).toBe(false)
  expect(selection().pickFilter).toBeUndefined()
  expect(screen.getByTestId('selection-clear')).toBeTruthy()
})

it('a single-item field replaces its item and does not turn Multi on', async () => {
  const { calling, selection } = await setup({ open: open([sel('path', 'Sweep path', { multi: false })], { path: [stored(0)] }, 'path'), refs: () => ok(1) })
  expect(selection().multi).toBe(false)
  commit(selection, itemAt(1))
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/update')[0]!.body).toEqual({ values: { path: [stored(1)] } })
})

it('a field with a number per item is read-only and cannot be picked into', async () => {
  const { calling, selection } = await setup({ open: open([sel('verts', 'Vertices', { want: ['vertex'], number: true })], { verts: [{ ok: true, kind: 'vertex', ref: { part: part.id }, name: null }] }, null) })
  expect(within(group('Vertices')).getByRole('button', { name: /Vertices/ }).hasAttribute('disabled')).toBe(true)
  expect(screen.getByText(/A number for each item can only be set on the desktop/)).toBeTruthy()
  expect(screen.queryByRole('button', { name: /^Remove/ })).toBeNull()
  expect(selection().pickFilter).toEqual([])
  commit(selection, itemAt(0))
  expect(calling('/refs').length).toBe(0)
})

it('a face picked for an edge field comes back as a list of edges: each becomes its own chip', () => {
  const added: SelItem[] = [1, 2, 3].map((n) => ({ ok: true, kind: 'edge', ref: { part: 'p', name: `E${n}` }, name: `E${n}` }))
  expect(afterPick([], added, true).map((i) => i.name)).toEqual(['E1', 'E2', 'E3'])
  // picking one again takes it out (as the desktop does); a single-item field keeps the first
  expect(afterPick(added, [added[1]!], true).map((i) => i.name)).toEqual(['E1', 'E3'])
  expect(afterPick(added, added, false).map((i) => i.name)).toEqual(['E1'])
})

it('maps a field want to view kinds, names a region from its sketch, and highlights items by Element name', () => {
  expect(pickKinds(['edge'])).toEqual(['edge'])
  expect(pickKinds(['region', 'face'])).toEqual(['face'])
  expect(pickKinds(['region'])).toEqual([])
  expect(pickKinds(['plane', 'face', 'axis'])).toEqual(['face'])
  const sketch = bracket.sketches[0]
  const feature = bracket.features.find((f) => f.id === sketch?.id)
  if (sketch && feature && sketch.regions[0]) {
    const r = sketch.regions[1] ?? sketch.regions[0]
    const n = sketch.regions.indexOf(r) + 1
    const region: SelItem = { ok: true, kind: 'region', ref: { sketch: sketch.id, c: [...r.c], area: r.area, loops: r.loops, edges: r.edges }, name: null }
    expect(itemLabel(region, bracket)).toBe(`Region ${n} of ${feature.name}`)
  }
  expect(itemLabel({ ok: true, kind: 'region', ref: { sketch: 'S9', c: [0, 0, 0], area: 1 }, name: null }, null)).toBe('Region of S9')
  expect(itemLabel({ ok: true, kind: 'edge', ref: {}, name: null }, null)).toBe('Edge')
  expect(itemLabel({ ok: true, kind: 'edge', ref: {}, name: 'NoSuchName' }, bracket)).toBe('Edge')
  const hit = highlightItems([stored(0), { ok: true, kind: 'edge', ref: {}, name: null }, { ok: true, kind: 'edge', ref: {}, name: 'NoSuchName' }], bracket)
  expect(hit.map((h) => [h.part, h.index, h.name])).toEqual([[part.id, edgeNames[0]![0], edgeNames[0]![1]]])
})

it('a pick on the base keeps its index; one made on another model is found in the base by Element name, or refused', () => {
  const drawn = fixture('bracket').outputs // a different object from `bracket`: the view draws another model
  const [index, name] = edgeNames[0]!
  const here = { ...itemAt(0), index: index + 7 } // the other model counts it differently
  expect(baseIndex(here, drawn, bracket)).toBe(index)
  expect(baseIndex({ ...here, name: 'NoSuchName' }, drawn, bracket)).toBeNull()
  // drawn on the base itself: the index stands, with or without a name
  expect(baseIndex(here, bracket, bracket)).toBe(index + 7)
  expect(baseIndex({ ...here, name: null }, bracket, bracket)).toBe(index + 7)
  // off the base and without a name there is nothing to go by; with no base yet there is nothing to resolve against
  expect(baseIndex({ ...here, name: null }, drawn, bracket)).toBeNull()
  expect(baseIndex(here, drawn, null)).toBeUndefined()
  expect(name).not.toBe('')
})

it('in dialog mode the view draws the base model and the chips number its edges; a pick before it arrives is refused', async () => {
  // a base the server sends that differs from the committed model: its part is named "Base model"
  const based = await setup({ open: open([sel('edges', 'Edges to fillet')], { edges: [stored(0)] }, null), refs: () => ok(1), base: bracketWithPart({ name: 'Base model' }) })
  await waitFor(() => expect(chips('Edges to fillet')).toEqual([`Edge ${edgeNames[0]![0] + 1} of Base model`]))
  // the selection the view draws is matched against the base (the drawn model): the field's edge is highlighted there
  fireEvent.click(within(group('Edges to fillet')).getByRole('button', { name: /Tap, then pick/ }))
  await waitFor(() => expect(based.selection().getState().items.map((i) => i.name)).toEqual([edgeNames[0]![1]]))
  commit(based.selection, itemAt(1))
  await waitFor(() => expect(based.calling('/refs').length).toBe(1))
  expect((based.calling('/refs')[0]!.body as { dialog: string }).dialog).toBe('did1')
  cleanup()
  vi.unstubAllGlobals()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  // no base (the server could not send it): nothing goes to the refs route, and the field says why
  const none = await setup({ open: open([sel('edges', 'Edges to fillet')], {}, 'edges'), refs: () => ok(1), base: null })
  commit(none.selection, itemAt(1))
  expect((await screen.findAllByRole('alert')).map((a) => a.textContent)).toContain(BASE_LOADING)
  expect(none.calling('/refs').length).toBe(0)
})

it('the banner drops the Element name line of an engine message and keeps the rest', () => {
  const engine = 'Fillet 1: The edge this feature uses on Part 1 is gone: a change earlier in the model removed or replaced it. Edit this feature and pick the edge again.\nElement: l12;SKT;:HF1:4,E;F2'
  expect(readableError(engine)).toBe('Fillet 1: The edge this feature uses on Part 1 is gone: a change earlier in the model removed or replaced it. Edit this feature and pick the edge again.')
  expect(readableError('Extrude 1: the profile is open')).toBe('Extrude 1: the profile is open')
  expect(readableError(null)).toBeNull()
})

it('a field item with an encoded Element name shows kind, number and part, never the raw name', async () => {
  const raw = edgeNames[0]![1]
  expect(itemLabel(stored(0), bracket)).toBe(`Edge ${edgeNames[0]![0] + 1} of ${part.name}`)
  await setup({ open: open([sel('edges', 'Edges to fillet')], { edges: [stored(0)] }, null) })
  expect(chips('Edges to fillet')).toEqual([`Edge ${edgeNames[0]![0] + 1} of ${part.name}`])
  expect(screen.queryByText(raw)).toBeNull()
  expect(screen.getByRole('button', { name: `Remove Edge ${edgeNames[0]![0] + 1} of ${part.name}` })).toBeTruthy()
})
