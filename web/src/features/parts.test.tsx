import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { LONG_PRESS_MS } from '../shell/longPress'
import { bracketWithPart, fixture } from '../test/fixtures'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { PART_PALETTE } from './partPalette'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const hello = (extra: object = {}) => ({
  type: 'hello',
  client: 'abcdefgh',
  document: { id: 'd', name: 'Bracket' },
  tabs: [tab('T1', 'studio', 'Part Studio 1')],
  read_only: false,
  holder: null,
  ...extra,
})

/** The Shell on the bracket (one part, "Bracket", F2/1). `outputs` is what the NEXT fetches of the outputs return. */
async function open(extra: object = {}) {
  let bytes = fixture('bracket').bytes
  const calls = stubFetch((c) => (c.method === 'GET' && c.url.endsWith('/outputs') ? { bytes } : c.url.endsWith('/edit') ? { json: { rebuilt: ['T1'] } } : undefined))
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say(hello(extra))
  await screen.findByRole('list', { name: 'Parts (1)' })
  const edits = () => calls.filter((c: Call) => c.url.endsWith('/edit'))
  /** The server applied an edit: the tab's next revision carries `next`. */
  const nextRevision = async (next: ArrayBuffer) => {
    bytes = next
    act(() => sock.say({ type: 'changed', client: 'other', rebuilt: ['T1'], revisions: { T1: 5 } }))
  }
  return { store, sock, calls, edits, nextRevision }
}

const partRow = (name: string | RegExp) => screen.getByRole('button', { name, description: undefined }) as HTMLElement

function longPress(el: HTMLElement) {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const at = { pointerId: 1, pointerType: 'touch', button: 0, clientX: 10, clientY: 10 }
  fireEvent.pointerDown(el, at)
  act(() => {
    vi.advanceTimersByTime(LONG_PRESS_MS)
  })
  fireEvent.pointerUp(el, at)
  fireEvent.click(el)
  vi.useRealTimers()
}

it('lists the studio parts under the features: a heading with the count and a row with the part name, colour and eye', async () => {
  await open()
  expect(screen.getByText('Parts (1)')).toBeTruthy()
  const slot = screen.getByTestId('parts-slot')
  const row = slot.querySelector<HTMLElement>('[data-part="F2/1"]')!
  expect(row.textContent).toContain('Bracket')
  expect((within(slot).getByTestId('part-swatch') as HTMLElement).style.background).toBe('rgb(153, 189, 217)')
  expect(within(slot).getByRole('button', { name: 'Hide Bracket' })).toBeTruthy()
  // under the feature list, in the same scrolling body
  expect(screen.getByTestId('panel-body').contains(slot)).toBe(true)
})

it('the eye sends set_part_override with the hidden flag, and shows the part as hidden from the next outputs', async () => {
  const { edits, nextRevision } = await open()
  fireEvent.click(screen.getByRole('button', { name: 'Hide Bracket' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]).toMatchObject({ method: 'POST', url: '/documents/d/tabs/T1/edit', body: { op: 'set_part_override', part: 'F2/1', hidden: true } })
  expect(edits()[0]?.headers['X-Client-Id']).toBe('abcdefgh')
  await nextRevision(bracketWithPart({ hidden: true }))
  const show = await screen.findByRole('button', { name: 'Show Bracket' })
  expect(show.closest('li')?.hasAttribute('data-hidden')).toBe(true)
  fireEvent.click(show)
  await waitFor(() => expect(edits().length).toBe(2))
  expect(edits()[1]?.body).toEqual({ op: 'set_part_override', part: 'F2/1', hidden: false })
})

it('Rename in the part menu sends the new name, and the list shows it from the next outputs', async () => {
  const { edits, nextRevision } = await open()
  longPress(partRow(/^Bracket$/))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Rename' }))
  const field = screen.getByRole('textbox', { name: 'Name' }) as HTMLInputElement
  expect(field.value).toBe('Bracket')
  fireEvent.change(field, { target: { value: 'Base plate' } })
  fireEvent.submit(field.closest('form')!)
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toEqual({ op: 'set_part_override', part: 'F2/1', name: 'Base plate' })
  await nextRevision(bracketWithPart({ name: 'Base plate' }))
  await screen.findByRole('button', { name: /^Base plate$/ })
  expect(screen.queryByRole('button', { name: /^Bracket$/ })).toBeNull()
  expect(screen.getByRole('button', { name: 'Hide Base plate' })).toBeTruthy()
})

it('the colour choice offers the desktop palette, sends the chosen colour, and Default colour sends null', async () => {
  const { edits, nextRevision } = await open()
  longPress(partRow(/^Bracket$/))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Colour' }))
  const group = screen.getByRole('group', { name: 'Colours' })
  expect(within(group).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(PART_PALETTE.map((c) => c.name))
  // the part's own colour is the first palette entry, marked as the current one
  expect(within(group).getByRole('button', { name: 'Steel blue' }).getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(within(group).getByRole('button', { name: 'Apricot' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toEqual({ op: 'set_part_override', part: 'F2/1', color: [0.93, 0.66, 0.45] })
  expect(screen.queryByRole('dialog')).toBeNull()
  await nextRevision(bracketWithPart({ color: [0.93, 0.66, 0.45] }))
  await waitFor(() => expect((screen.getByTestId('part-swatch') as HTMLElement).style.background).toBe('rgb(237, 168, 115)'))
  longPress(partRow(/^Bracket$/))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Colour' }))
  expect(screen.getByRole('button', { name: 'Apricot' }).getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: 'Default colour' }))
  await waitFor(() => expect(edits().length).toBe(2))
  expect(edits()[1]?.body).toEqual({ op: 'set_part_override', part: 'F2/1', color: null })
})

it('the menu offers Hide, or Show for a hidden part', async () => {
  const { edits } = await open()
  longPress(partRow(/^Bracket$/))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Hide' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toEqual({ op: 'set_part_override', part: 'F2/1', hidden: true })
})

it('a tap selects the part, and one thing is selected at a time: a part clears the feature and the reverse', async () => {
  await open()
  const shell = () => window.__freeshape!.shell
  fireEvent.click(screen.getByRole('button', { name: 'Extrude 1' }))
  expect(shell().selectedFeature).toBe('F2')
  fireEvent.click(partRow(/^Bracket$/))
  expect(shell().selectedPart).toBe('F2/1')
  expect(shell().selectedFeature).toBeNull()
  expect(partRow(/^Bracket$/).getAttribute('aria-current')).toBe('true')
  expect(screen.getByRole('button', { name: 'Extrude 1' }).getAttribute('aria-current')).toBeNull()
  expect(screen.getByTestId('feature-panel').dataset.selectedPart).toBe('F2/1')
  fireEvent.click(screen.getByRole('button', { name: 'Extrude 1' }))
  expect(shell().selectedPart).toBeNull()
  expect(shell().selectedFeature).toBe('F2')
  expect(partRow(/^Bracket$/).getAttribute('aria-current')).toBeNull()
  act(() => shell().selectPart('F2/1'))
  expect(shell().selectedFeature).toBeNull()
  act(() => shell().selectPart(null))
  expect(shell().selectedPart).toBeNull()
})

it('with the Document read-only the eye is off and the menu does not open, but a part can still be selected', async () => {
  const { edits } = await open({ read_only: true, holder: holderOf('desktop') })
  expect((screen.getByRole('button', { name: 'Hide Bracket' }) as HTMLButtonElement).disabled).toBe(true)
  longPress(partRow(/^Bracket$/))
  expect(screen.queryByRole('menu')).toBeNull()
  fireEvent.click(partRow(/^Bracket$/))
  expect(partRow(/^Bracket$/).getAttribute('aria-current')).toBe('true')
  expect(edits().length).toBe(0)
})

it('a refusal of the eye is said in plain words', async () => {
  const calls = stubFetch((c) => (c.method === 'GET' && c.url.endsWith('/outputs') ? { bytes: fixture('bracket').bytes } : c.url.endsWith('/edit') ? { status: 400, json: { error: 'no part F2/1 in the Part Studio' } } : undefined))
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say(hello())
  fireEvent.click(await screen.findByRole('button', { name: 'Hide Bracket' }))
  const dialog = await screen.findByRole('dialog', { name: 'Could not hide the part' })
  expect(dialog.textContent).toContain('no part F2/1 in the Part Studio')
  expect(calls.length).toBeGreaterThan(0)
})

