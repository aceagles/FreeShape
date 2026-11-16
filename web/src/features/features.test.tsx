import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { LONG_PRESS_MS } from '../shell/longPress'
import { fixture } from '../test/fixtures'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'

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
  tabs: [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'assembly', 'Assembly 1')],
  read_only: false,
  holder: null,
  ...extra,
})

/** The Shell on a Document whose Part Studio is the recorded fixture; edits are answered {rebuilt: []}. */
async function open(name: 'bracket' | 'tree', extra: object = {}, editReply: { status?: number; json?: unknown } = { json: { rebuilt: ['T1'] } }) {
  const { bytes } = fixture(name)
  const calls = stubFetch((c) => (c.method === 'GET' && c.url.endsWith('/outputs') ? { bytes } : c.url.endsWith('/edit') ? editReply : undefined))
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say(hello(extra))
  await screen.findByRole('list', { name: 'Features' })
  const edits = () => calls.filter((c: Call) => c.url.endsWith('/edit'))
  return { store, sock, calls, edits }
}

const rows = () => [...document.querySelectorAll<HTMLElement>('.panel-row[data-feature]')]
const row = (name: RegExp | string) => screen.getByRole('button', { name })

/** Hold a row for 500 ms, release and click (as a finger does); the menu opens. */
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

it('lists the fixture features in order, with their types, and marks the failed one with its message', async () => {
  await open('tree')
  expect(rows().map((r) => r.dataset.feature)).toEqual(['F1', 'F2', 'F3', 'F4', 'F5', 'F9', 'F6', 'F7', 'F8'])
  expect(rows().map((r) => r.dataset.type)).toEqual(['sketch', 'extrude', 'fillet', 'sketch', 'hole', 'fillet', 'sketch', 'extrude', 'chamfer'])
  const failed = row(/Fillet 2 failed|failed/)
  expect(failed.dataset.state).toBe('failed')
  expect(within(failed).getByText('Select edges to fillet')).toBeTruthy()
  expect(rows().filter((r) => r.dataset.state === 'failed').length).toBe(1)
  expect(rows().find((r) => r.dataset.feature === 'F3')?.dataset.state).toBe('suppressed')
  // the rename shows as the row's text
  expect(row('Body')).toBeTruthy()
  // rows below the bar (index 7 and up) are rolled back
  expect(rows().filter((r) => r.dataset.state === 'rolled-back').map((r) => r.dataset.feature)).toEqual(['F7', 'F8'])
})

it('the bracket lists its eight features with the bar below them all', async () => {
  await open('bracket')
  expect(rows().map((r) => r.dataset.feature)).toEqual(['F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8'])
  const list = screen.getByRole('list', { name: 'Features' })
  expect(list.lastElementChild).toBe(screen.getByTestId('rollback-bar'))
  expect(screen.getByTestId('parts-slot')).toBeTruthy()
})

it('a folder is a group that opens and closes, and the bar sits above it', async () => {
  await open('tree')
  const folder = screen.getByRole('button', { name: 'Holes' })
  expect(folder.getAttribute('aria-expanded')).toBe('true')
  fireEvent.click(folder)
  expect(folder.getAttribute('aria-expanded')).toBe('false')
  expect(rows().map((r) => r.dataset.feature)).toEqual(['F1', 'F2', 'F3', 'F9', 'F6', 'F7', 'F8'])
})

it('a tap selects the feature, and the selection is on the panel and in the shell api', async () => {
  await open('bracket')
  fireEvent.click(row('Extrude 1'))
  expect(row('Extrude 1').getAttribute('aria-current')).toBe('true')
  expect(screen.getByTestId('feature-panel').dataset.selectedFeature).toBe('F2')
  expect(window.__freeshape?.shell.selectedFeature).toBe('F2')
  fireEvent.click(row('Slot'))
  expect(row('Extrude 1').getAttribute('aria-current')).toBeNull()
  expect(window.__freeshape?.shell.selectedFeature).toBe('F6')
})

it('Suppress in the long-press menu sends set_suppressed with that feature, and does not select it', async () => {
  const { edits } = await open('bracket')
  longPress(row('Fillet 1'))
  expect(row('Fillet 1').getAttribute('aria-current')).toBeNull()
  fireEvent.click(screen.getByRole('menuitem', { name: 'Suppress' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]).toMatchObject({ method: 'POST', url: '/documents/d/tabs/T1/edit', body: { op: 'set_suppressed', feature: 'F3', value: true } })
  expect(edits()[0]?.headers['X-Client-Id']).toBe('abcdefgh')
})

it('the menu of a suppressed feature offers Unsuppress', async () => {
  const { edits } = await open('tree')
  longPress(row('Fillet 1'))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Unsuppress' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toEqual({ op: 'set_suppressed', feature: 'F3', value: false })
})

it('Delete asks first, and sends delete_features only when confirmed', async () => {
  const { edits } = await open('bracket')
  longPress(row('Chamfer 1'))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }))
  expect(screen.getByRole('dialog', { name: 'Delete "Chamfer 1"?' })).toBeTruthy()
  expect(edits().length).toBe(0)
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(edits().length).toBe(0)
  longPress(row('Chamfer 1'))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Delete' }))
  fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toEqual({ op: 'delete_features', ids: ['F8'] })
})

it('Rename prompts for the name and sends rename_feature on Enter', async () => {
  const { edits } = await open('bracket')
  longPress(row('Slot'))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Rename' }))
  const field = screen.getByRole('textbox', { name: 'Name' }) as HTMLInputElement
  expect(field.value).toBe('Slot')
  fireEvent.change(field, { target: { value: 'Keyhole' } })
  fireEvent.submit(field.closest('form')!)
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toEqual({ op: 'rename_feature', feature: 'F6', name: 'Keyhole' })
})

it('Roll to here rolls back to just after that feature', async () => {
  const { edits } = await open('bracket')
  longPress(row('Hole 1'))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Roll to here' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toEqual({ op: 'set_rollback', index: 5 })
})

it('a refusal from the server is said in plain words', async () => {
  await open('bracket', {}, { status: 400, json: { error: 'no feature F3 in the Part Studio' } })
  longPress(row('Fillet 1'))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Suppress' }))
  const dialog = await screen.findByRole('dialog', { name: 'Could not suppress the feature' })
  expect(dialog.textContent).toContain('no feature F3 in the Part Studio')
})

/** Rows and the bar are 44 px boxes stacked in document order in a tall scrolling body (jsdom has no layout). */
function layOut() {
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const boxes = [...document.querySelectorAll('[data-row], [data-rollback-bar]')]
    const at = boxes.indexOf(this)
    const top = at < 0 ? 0 : at * 44
    const height = at < 0 ? (this.classList.contains('panel-body') ? 2000 : 0) : 44
    return { x: 0, y: top, left: 0, right: 280, top, bottom: top + height, width: 280, height, toJSON: () => ({}) }
  })
}
const touch = (clientY: number) => ({ pointerId: 7, pointerType: 'touch', button: 0, clientX: 250, clientY })

it('dragging the handle past two features sends ONE set_rollback on release with the new position', async () => {
  layOut()
  const { edits } = await open('bracket')
  const handle = screen.getByTestId('rollback-handle')
  const bar = screen.getByTestId('rollback-bar')
  // the bar is under the eighth row: its centre is 8 x 44 + 22
  fireEvent.pointerDown(handle, touch(374))
  fireEvent.pointerMove(handle, touch(330))
  fireEvent.pointerMove(handle, touch(264)) // between the sixth and seventh rows
  // while dragging nothing is sent; the bar follows, and what is below it greys
  expect(edits().length).toBe(0)
  expect(bar.style.transform).toBe('translateY(-110px)')
  expect(rows().filter((r) => r.dataset.state === 'rolled-back').map((r) => r.dataset.feature)).toEqual(['F7', 'F8'])
  fireEvent.pointerUp(handle, touch(264))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toEqual({ op: 'set_rollback', index: 6 })
  // the bar is drawn where it was dropped at once, before the outputs of the edit are back
  expect(screen.getByRole('list', { name: 'Features' }).children[6]).toBe(screen.getByTestId('rollback-bar'))
})

it('a drag that ends where it started sends nothing, and a cancelled drag sends nothing', async () => {
  layOut()
  const { edits } = await open('bracket')
  const handle = screen.getByTestId('rollback-handle')
  fireEvent.pointerDown(handle, touch(374))
  fireEvent.pointerMove(handle, touch(380))
  fireEvent.pointerUp(handle, touch(380))
  fireEvent.pointerDown(handle, touch(374))
  fireEvent.pointerMove(handle, touch(200))
  fireEvent.pointerCancel(handle, touch(200))
  expect(edits().length).toBe(0)
  expect(screen.getByTestId('rollback-bar').style.transform).toBe('')
})

it('the bar does not move except by its handle: pressing a row or the line sends nothing', async () => {
  layOut()
  const { edits } = await open('bracket')
  fireEvent.pointerDown(document.querySelector('.rollback-line')!, touch(374))
  fireEvent.pointerMove(document.querySelector('.rollback-line')!, touch(100))
  fireEvent.pointerUp(document.querySelector('.rollback-line')!, touch(100))
  expect(edits().length).toBe(0)
})

it('undo and redo follow can_undo and can_redo of the current tab, and send undo and redo', async () => {
  const bracket = await open('bracket')
  expect((screen.getByRole('button', { name: 'Undo' }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('button', { name: 'Redo' }) as HTMLButtonElement).disabled).toBe(true)
  bracket.store.stop()
  cleanup()
  const tree = await open('tree')
  const undo = screen.getByRole('button', { name: 'Undo' }) as HTMLButtonElement
  const redo = screen.getByRole('button', { name: 'Redo' }) as HTMLButtonElement
  expect([undo.disabled, redo.disabled]).toEqual([false, false])
  fireEvent.click(undo)
  await waitFor(() => expect(tree.edits().length).toBe(1))
  expect(tree.edits()[0]?.body).toEqual({ op: 'undo' })
  await waitFor(() => expect(undo.disabled).toBe(false))
  fireEvent.click(redo)
  await waitFor(() => expect(tree.edits().length).toBe(2))
  expect(tree.edits()[1]?.body).toEqual({ op: 'redo' })
})

it('with the Document read-only every panel action is off: no menu, no handle, no undo', async () => {
  const { edits } = await open('tree', { read_only: true, holder: holderOf('desktop') })
  longPress(row('Body'))
  expect(screen.queryByRole('menu')).toBeNull()
  expect((screen.getByTestId('rollback-handle') as HTMLButtonElement).disabled).toBe(true)
  for (const name of ['Undo', 'Redo']) expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true)
  // selecting is not an edit
  fireEvent.click(row('Body'))
  expect(row('Body').getAttribute('aria-current')).toBe('true')
  expect(edits().length).toBe(0)
})

it('an Assembly tab has no feature list, and a payload from an older server says so', async () => {
  await open('bracket')
  fireEvent.click(screen.getByRole('button', { name: 'Assembly 1' }))
  expect(screen.getByTestId('feature-panel').textContent).toContain('This tab has no feature list.')
  expect((screen.getByRole('button', { name: 'Undo' }) as HTMLButtonElement).disabled).toBe(true)
})

it('the collapsed rail has one icon button per feature, and a tap selects', async () => {
  await open('tree')
  fireEvent.click(screen.getByRole('button', { name: 'Collapse feature panel' }))
  const rail = screen.getByTestId('panel-rail')
  const buttons = [...rail.querySelectorAll<HTMLElement>('.rail-feature')]
  expect(buttons.length).toBe(9)
  expect(buttons.find((b) => b.dataset.state === 'failed')?.getAttribute('aria-label')).toBe('Fillet 2 failed')
  fireEvent.click(buttons[1]!)
  expect(buttons[1]!.getAttribute('aria-current')).toBe('true')
  expect(window.__freeshape?.shell.selectedFeature).toBe('F2')
})
