import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { bracketWithPart, fixture } from '../test/fixtures'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import type { Description, FieldSpec, OpenReply } from './dialogApi'

// The feature dialog (card I5.6) in the whole Shell, against a scripted server. WebGL is not in jsdom: a stand-in
// renderer lets the real viewport controller run, so window.__freeshapeViewport is the real test hook.
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

const stubResize = () => vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
beforeEach(stubResize)
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const num = (key: string, label: string, extra: Partial<FieldSpec> = {}): FieldSpec => ({ key, kind: 'num', standard: true, visible: true, label, unit: 'mm', min: null, signed: false, ...extra })
const choice = (key: string, label: string, options: Array<[string, string]>): FieldSpec => ({ key, kind: 'choice', standard: true, visible: true, label, options })
const check = (key: string, label: string, extra: Partial<FieldSpec> = {}): FieldSpec => ({ key, kind: 'check', standard: true, visible: true, label, ...extra })

const description = (fields: FieldSpec[], custom: string[] = []): Description => ({ type: 'fillet', fields, custom })
const reply = (d: Description, extra: Partial<OpenReply> = {}): OpenReply => ({
  dialog: 'did1',
  document: 'd',
  tab: 'T1',
  feature: 'F2',
  name: 'Extrude 1',
  revision: 1,
  description: d,
  values: { depth: { value: 40, text: '40' }, end: { value: 'blind' }, draft: { value: false }, hidden_one: { value: 1, text: '1' } },
  fields: {},
  active: null,
  ...extra,
})
const standard = description([
  num('depth', 'Depth'),
  choice('end', 'End type', [['blind', 'Blind'], ['through', 'Through all']]),
  check('draft', 'Draft'),
  num('hidden_one', 'Hidden', { visible: false }),
])

interface Script {
  open?: OpenReply
  update?: (body: { values: Record<string, unknown> }) => object
  accept?: { status?: number; json?: unknown }
  preview?: { bytes: ArrayBuffer; number: number } | null
}

async function setup(script: Script, hello: object = {}) {
  const { bytes } = fixture('bracket')
  const calls = stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.endsWith('/features/F2/dialog')) return { status: 201, json: script.open ?? reply(standard) }
    if (c.url.endsWith('/update')) return { json: script.update?.(c.body as { values: Record<string, unknown> }) ?? {} }
    if (c.url.endsWith('/accept')) return script.accept ?? { json: { rebuilt: ['T1'], revision: 2 } }
    if (c.url.endsWith('/cancel')) return { json: { cancelled: true } }
    if (c.method === 'GET' && c.url.endsWith('/preview')) return script.preview ? { bytes: script.preview.bytes, headers: { 'X-Preview': String(script.preview.number) } } : undefined
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: null, ...hello })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  const calling = (suffix: string) => calls.filter((c: Call) => c.method === 'POST' && c.url.endsWith(suffix))
  return { store, sock, calls, calling }
}

/** Cancel and Accept in the dialog's header (the pad has a Cancel key of its own). */
const header = (name: string) => within(document.querySelector<HTMLElement>('.dialog-header')!).getByRole('button', { name })
const queryHeader = (name: string) => within(document.querySelector<HTMLElement>('.dialog-header')!).queryByRole('button', { name })
const openDialog = async () => {
  const el = screen.getByRole('button', { name: 'Extrude 1' })
  fireEvent.click(el)
  fireEvent.click(el)
  await screen.findByRole('dialog', { name: 'Extrude 1' })
  await screen.findByTestId('dialog-form')
}

it('draws a number, a choice and a check, and leaves a hidden field out', async () => {
  await setup({})
  await openDialog()
  const form = screen.getByTestId('dialog-form')
  expect(within(form).getByRole('textbox', { name: /Depth/ })).toBeTruthy() // the first number is on the pad
  expect(within(form).getByRole('radiogroup', { name: 'End type' })).toBeTruthy()
  expect(within(form).getByRole('switch', { name: 'Draft' })).toBeTruthy()
  expect(within(form).queryByText('Hidden')).toBeNull()
  expect(header('Accept')).toBeTruthy()
  expect(screen.getByTestId('pad')).toBeTruthy()
})

it('sends one update for a number committed on the pad, and shows the validation message under the field', async () => {
  const { calling } = await setup({
    update: (b) => ({ ...reply(standard), values: { depth: { value: 40, text: '40', error: 'Depth must be more than 0' } }, errors: { depth: 'Depth must be more than 0' }, preview: null, sentKeys: Object.keys(b.values) }),
  })
  await openDialog()
  const pad = screen.getByTestId('pad')
  for (const k of ['Backspace', '7']) fireEvent.click(within(pad).getByRole('button', { name: k }))
  fireEvent.click(within(pad).getByRole('button', { name: 'Enter' }))
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/update')[0]!.body).toEqual({ values: { depth: '7' } })
  expect(calling('/update')[0]!.headers['X-Client-Id']).toBe('abcdefgh')
  expect((await screen.findByRole('alert')).textContent).toBe('Depth must be more than 0')
  expect(screen.queryByTestId('pad')).toBeNull() // the pad closes after Enter
})

it('a choice and a check each send one update', async () => {
  const { calling } = await setup({ update: () => ({ ...reply(standard), errors: {}, preview: null }) })
  await openDialog()
  fireEvent.click(screen.getByRole('radio', { name: 'Through all' }))
  await waitFor(() => expect(calling('/update').length).toBe(1))
  expect(calling('/update')[0]!.body).toEqual({ values: { end: 'through' } })
  fireEvent.click(screen.getByRole('switch', { name: 'Draft' }))
  await waitFor(() => expect(calling('/update').length).toBe(2))
  expect(calling('/update')[1]!.body).toEqual({ values: { draft: true } })
})

it('draws the pushed preview tinted, and Cancel brings the committed revision back with nothing tinted', async () => {
  const committed = fixture('bracket').outputs.revision
  const previewBytes = bracketWithPart({ name: 'Previewed' })
  const { sock, calling } = await setup({
    update: () => ({ ...reply(standard), errors: {}, preview: 1 }),
    preview: { bytes: previewBytes, number: 1 },
  })
  expect(window.__freeshapeViewport?.preview).toBeNull()
  await openDialog()
  fireEvent.click(screen.getByRole('switch', { name: 'Draft' }))
  await waitFor(() => expect(calling('/update').length).toBe(1))
  sock.say({ type: 'preview', document: 'd', tab: 'T1', dialog: 'did1', feature: 'F2', preview: 1, revision: committed, ok: true, outputs: true, error: null })
  await waitFor(() => expect(window.__freeshapeViewport?.preview).toBe(1))
  expect(window.__freeshapeViewport?.tintedFeature).toBe('F2')
  expect(window.__freeshapeViewport?.tintedParts).toBeGreaterThan(0)
  fireEvent.click(header('Cancel'))
  await waitFor(() => expect(window.__freeshapeViewport?.preview).toBeNull())
  expect(window.__freeshapeViewport?.tintedFeature).toBeNull()
  expect(window.__freeshapeViewport?.tintedParts).toBe(0)
  expect(window.__freeshapeViewport?.revision).toBe(committed)
  expect(calling('/cancel').length).toBe(1)
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('a preview that failed shows the message, and an older preview arriving late is not drawn', async () => {
  const { sock, calling } = await setup({
    update: () => ({ ...reply(standard), errors: {}, preview: 2 }),
    preview: { bytes: bracketWithPart({ name: 'Newest' }), number: 2 },
  })
  await openDialog()
  fireEvent.click(screen.getByRole('switch', { name: 'Draft' }))
  await waitFor(() => expect(calling('/update').length).toBe(1))
  sock.say({ type: 'preview', document: 'd', tab: 'T1', dialog: 'did1', feature: 'F2', preview: 2, revision: 1, ok: false, outputs: true, error: 'Extrude 1: the profile is open' })
  await waitFor(() => expect(window.__freeshapeViewport?.preview).toBe(2))
  expect((await screen.findByTestId('preview-error')).textContent).toBe('Extrude 1: the profile is open')
  sock.say({ type: 'preview', document: 'd', tab: 'T1', dialog: 'did1', feature: 'F2', preview: 1, revision: 1, ok: true, outputs: true, error: null })
  await act(async () => {})
  expect(window.__freeshapeViewport?.preview).toBe(2)
})

it('Accept sends accept and closes; a read-only Document refuses it and the dialog keeps its values', async () => {
  const refused = await setup({ accept: { status: 423, json: { error: 'locked', document: 'd', holder: holderOf('desktop') } } })
  await openDialog()
  fireEvent.click(header('Accept'))
  expect((await screen.findByTestId('dialog-problem')).textContent).toMatch(/open read-only here/)
  expect(refused.calling('/accept').length).toBe(1)
  expect(screen.getByRole('dialog', { name: 'Extrude 1' })).toBeTruthy()
  cleanup()
  vi.unstubAllGlobals()
  stubResize()
  const ok = await setup({})
  await openDialog()
  fireEvent.click(header('Accept'))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(ok.calling('/accept').length).toBe(1)
})

it('a feature with a custom field says it is edited on the desktop and has no Accept button', async () => {
  const custom = description([num('depth', 'Depth'), { key: 'split_tools', kind: 'split_tools', standard: false, visible: true }], ['split_tools'])
  await setup({ open: reply(custom) })
  await openDialog().catch(() => {})
  expect((await screen.findByTestId('desktop-only')).textContent).toMatch(/only be edited on the desktop/)
  expect(queryHeader('Accept')).toBeNull()
  expect(header('Cancel')).toBeTruthy()
})
