import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { MODE_KEY } from '../shell/mode'
import { fixture } from '../test/fixtures'
import { makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { idOfItem } from '../selection/store'
import type { SelectionItem, SelectionStore } from '../selection/store'
import { DESKTOP_TOOLBAR, desktopToolIds, FEATURE_STRIP } from '../tools/defs'
import type { Description, FieldSpec, OpenReply } from './dialogApi'
import { refPick } from './selectionFields'

// The Mate connector's dialog (card I10.4) in the whole Shell, against a scripted server: Name, the element, Owner part and
// Flip are standard rows (freeshape/dialog_schema.py `mate_describe`), and a click on a face is sent with its hit point.
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

let mode = 'tablet'
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  window.localStorage.clear()
  window.localStorage.setItem(MODE_KEY, mode)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
  mode = 'tablet'
})

const bracket = fixture('bracket').outputs
const part = bracket.parts[0]!
const faces = [...part.names.face].filter(([, n]) => n !== '')
const edges = [...part.names.edge].filter(([, n]) => n !== '')
const faceItem = (i: number, point?: readonly [number, number, number]): SelectionItem => {
  const [index, name] = faces[i]!
  return { id: idOfItem('face', part.id, name, index, null), kind: 'face', part: part.id, index, name, key: null, ...(point === undefined ? {} : { point }) }
}
const edgeItem = (i: number): SelectionItem => {
  const [index, name] = edges[i]!
  return { id: idOfItem('edge', part.id, name, index, null), kind: 'edge', part: part.id, index, name, key: null, point: [0, 0, 0] }
}

const row = (key: string, kind: string, label: string, extra: Partial<FieldSpec> = {}): FieldSpec => ({ key, kind, standard: true, visible: true, label, ...extra })
const description: Description = {
  type: 'mate_connector',
  custom: [],
  fields: [
    row('name', 'text', 'Name', { placeholder: 'e.g. Hinge pin', expr: false }),
    row('element', 'sel', 'Face or edge (click it in the view)', { want: ['element'], multi: false, number: false, list: false, tip: null }),
    row('owner', 'choice', 'Owner part', {
      options: [
        ['', 'Same as the picked face or edge'],
        [part.id, part.name],
      ],
    }),
    row('flip', 'check', 'Flip (reverse the Z axis)'),
  ],
}
const open = (over: Partial<OpenReply> = {}): OpenReply => ({
  dialog: 'did1',
  document: 'd',
  tab: 'T1',
  feature: 'F2',
  name: 'Mate connector 1',
  revision: 1,
  description,
  values: { name: { value: '', text: '' }, owner: { value: '' }, flip: { value: false } },
  fields: { element: [] },
  active: null,
  ...over,
})

const STAMP = { click: [4, 1, 45], snap: null, x_ref: [1, 0] }

async function setup(refs: (pick: Record<string, unknown>) => object = () => ({ ok: true, kind: 'face', ref: { part: part.id, name: faces[0]![1] }, name: faces[0]![1], stamp: STAMP })) {
  const { bytes } = fixture('bracket')
  const calls = stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.endsWith('/features/F2/dialog')) return { status: 201, json: open() }
    if (c.url.endsWith('/update')) {
      const values = (c.body as { values: Record<string, unknown> }).values
      const element = (values['element'] as Array<Record<string, unknown>> | undefined)?.map((item) => ({ ...item, stamp: undefined }))
      return { json: { ...open(), fields: { element: element ?? [] }, errors: {}, preview: null } }
    }
    if (c.url.endsWith('/refs')) return { json: { document: 'd', tab: 'T1', revision: 1, items: [refs((c.body as { picks: Record<string, unknown>[] }).picks[0]!)] } }
    if (c.url.endsWith('/cancel')) return { json: { cancelled: true } }
    if (c.method === 'GET' && c.url.endsWith('/dialog/did1/base')) return { bytes, headers: { 'X-Revision': String(bracket.revision) } }
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
  const form = await screen.findByTestId('dialog-form')
  const selection = () => window.__freeshape!.shell.selection
  await waitFor(() => expect(selection().getState().field).toBe(true))
  await waitFor(() => expect(screen.getByTestId('shell-viewport').hasAttribute('data-dialog-base')).toBe(true))
  const calling = (suffix: string) => calls.filter((c: Call) => c.method === 'POST' && c.url.endsWith(suffix))
  return { form, calling, selection }
}

const commit = (s: () => SelectionStore, item: SelectionItem) =>
  act(() => {
    s().aim(item)
    s().commit(s().getAimed())
  })

it('Mate connector is after Move in the desktop row and in the Copy group of the Tablet strip', () => {
  const row = desktopToolIds(DESKTOP_TOOLBAR)
  expect(row[row.indexOf('move') + 1]).toBe('mate_connector')
  const copy = FEATURE_STRIP.find((d) => d.kind === 'group' && d.key === 'copy')
  expect(copy?.kind === 'group' && copy.tools.map((t) => t.id).slice(-2)).toEqual(['move', 'mate_connector'])
  expect(copy?.kind === 'group' && copy.tools.find((t) => t.id === 'mate_connector')?.label).toBe('Mate connector')
})

it('Tablet: the dialog has Name, the element field, Owner part and Flip, and no native text input', async () => {
  const { form } = await setup()
  expect(within(form).getByRole('button', { name: 'Name, empty' })).toBeTruthy()
  expect(within(form).getByRole('group', { name: 'Face or edge (click it in the view)' })).toBeTruthy()
  expect(within(form).getByRole('button', { name: /^Owner part, Same as the picked face or edge/ })).toBeTruthy()
  expect(within(form).getByRole('switch', { name: /Flip/ })).toBeTruthy()
  expect(form.querySelector('input, textarea, select, [contenteditable]')).toBeNull()
})

it('Tablet: the Name opens the text prompt and sends the text it takes', async () => {
  const { form, calling } = await setup()
  fireEvent.click(within(form).getByRole('button', { name: 'Name, empty' }))
  const field = (await screen.findByRole('textbox')) as HTMLInputElement
  fireEvent.change(field, { target: { value: 'Hinge pin' } })
  fireEvent.click(screen.getByRole('button', { name: 'Done' }))
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/update')[0]!.body).toEqual({ values: { name: 'Hinge pin' } })
  await waitFor(() => expect(screen.queryByRole('textbox')).toBeNull())
})

it('Tablet: the Owner part choice lists the parts and sends the one chosen', async () => {
  const { form, calling } = await setup()
  fireEvent.click(within(form).getByRole('button', { name: /^Owner part/ }))
  fireEvent.click(within(form).getByRole('radio', { name: part.name }))
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/update')[0]!.body).toEqual({ values: { owner: part.id } })
})

it('a click on a face is sent to the refs route with its hit point, and the answer carries its stamp to the update', async () => {
  const { calling, selection } = await setup()
  commit(selection, faceItem(0, [4, 1, 45]))
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/refs')[0]!.body).toEqual({ picks: [{ part: part.id, kind: 'face', index: faces[0]![0] + 1, want: ['element'], point: [4, 1, 45] }], dialog: 'did1' })
  expect(calling('/update')[0]!.body).toEqual({
    values: { element: [{ ok: true, kind: 'face', ref: { part: part.id, name: faces[0]![1] }, name: faces[0]![1], stamp: STAMP }] },
  })
})

it('the point is the last hit over the same face, not the first', async () => {
  const { calling, selection } = await setup()
  act(() => selection().aim(faceItem(0, [1, 1, 45])))
  act(() => selection().aim(faceItem(0, [9, 2, 45])))
  act(() => selection().commit(selection().getAimed()))
  await waitFor(() => expect(calling('/refs').length).toBe(1))
  expect((calling('/refs')[0]!.body as { picks: Array<{ point: number[] }> }).picks[0]!.point).toEqual([9, 2, 45])
})

it('an edge is sent without a point, and a field that does not want an element sends none for a face', () => {
  expect(refPick(edgeItem(0), ['element'])).toEqual({ part: part.id, kind: 'edge', index: edges[0]![0] + 1, want: ['element'] })
  expect(refPick(faceItem(0, [1, 2, 3]), ['face'])).toEqual({ part: part.id, kind: 'face', index: faces[0]![0] + 1, want: ['face'] })
  expect(refPick(faceItem(0, [1, 2, 3]), ['element'])).toMatchObject({ point: [1, 2, 3] })
  expect(refPick(faceItem(0), ['element'])).not.toHaveProperty('point')
})

it('Desktop mode: Name is typed in the row and the same rows are drawn', async () => {
  mode = 'desktop'
  window.localStorage.setItem(MODE_KEY, mode)
  const { form, calling } = await setup()
  const name = within(form).getByRole('textbox', { name: 'Name' }) as HTMLInputElement
  fireEvent.change(name, { target: { value: 'Pin' } })
  fireEvent.keyDown(name, { key: 'Tab' })
  fireEvent.blur(name)
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/update')[0]!.body).toEqual({ values: { name: 'Pin' } })
  expect(within(form).getByRole('checkbox', { name: /Flip/ })).toBeTruthy()
  expect(within(form).getByText('Owner part')).toBeTruthy()
})
