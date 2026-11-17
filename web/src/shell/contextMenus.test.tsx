import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LONG_PRESS_MS } from './longPress'
import { MODE_KEY } from './mode'
import { DESKTOP_ONLY } from './menus'
import { assemblyViewMenu, featureMenu, instanceMenu, mateMenu, partMenu, sketchViewMenu, studioViewMenu, tabMenu } from './contextMenus'
import type { ContextMenuData } from './contextMenus'
import { Shell } from './Shell'
import { ContextMenu } from './Menu'
import { fixture } from '../test/fixtures'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { openedMessage } from '../sketch/testFixture'

// Right-click context menus (card I9.10): the menus as data, the rows and the views that open them, and the pointer rules.
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
  vi.useRealTimers()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const labels = (m: ContextMenuData): string[] => m.entries.map((e) => (e.kind === 'separator' ? '|' : e.label))

/** A right click: the mouse's right button pressed and released where it was pressed. */
function rightClick(el: Element, x = 120, y = 90) {
  const at = { pointerId: 7, pointerType: 'mouse', button: 2, clientX: x, clientY: y }
  fireEvent.pointerDown(el, at)
  fireEvent.pointerUp(el, at)
}

const hello = (tabs = [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'assembly', 'Assembly 1')]) => ({
  type: 'hello',
  client: 'abcdefgh',
  document: { id: 'd', name: 'Bracket' },
  tabs,
  read_only: false,
  holder: holderOf('x'),
})

async function studio(mode: 'desktop' | 'tablet') {
  window.localStorage.setItem(MODE_KEY, mode)
  const { bytes } = fixture('bracket')
  const calls = stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.includes('/features/types')) return { json: { document: 'd', tab: 'T1', revision: 1, types: [] } }
    if (c.url.endsWith('/edit')) return { json: { rebuilt: ['T1'] } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say(hello([tab('T1', 'studio', 'Part Studio 1')]))
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  const edits = () => calls.filter((c: Call) => c.url.endsWith('/edit'))
  return { store, sock, edits }
}

const extrudeRow = () => document.querySelector<HTMLElement>('.panel-row[data-type="extrude"]')!

it('a right click on a feature row opens its menu at the pointer with the desktop items in order', async () => {
  const { store } = await studio('desktop')
  const row = extrudeRow()
  expect(screen.queryByRole('menu')).toBeNull()
  rightClick(row, 130, 95)
  const menu = await screen.findByRole('menu', { name: /menu$/ })
  expect(menu.style.left).toBe('130px')
  expect(menu.style.top).toBe('95px')
  const items = within(menu).getAllByRole('menuitem')
  expect(items.map((b) => b.textContent)).toEqual(['Edit…', 'Rename…', 'Suppress', 'Roll to here', 'Add to new folder', 'Delete'])
  // an item the client lacks is off, with the tooltip; the ones it has are on
  const folder = items.find((b) => b.textContent === 'Add to new folder') as HTMLButtonElement
  expect(folder.disabled).toBe(true)
  expect(folder.title).toBe(DESKTOP_ONLY)
  expect((items.find((b) => b.textContent === 'Rename…') as HTMLButtonElement).disabled).toBe(false)
  store.stop()
})

it('Rename and Delete in the feature menu rename and delete the feature', async () => {
  const { store, edits } = await studio('desktop')
  rightClick(extrudeRow())
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Rename…' }))
  expect(screen.queryByRole('menu')).toBeNull()
  const field = (await screen.findByRole('textbox')) as HTMLInputElement
  fireEvent.change(field, { target: { value: 'Plate' } })
  fireEvent.click(screen.getByRole('button', { name: 'Rename' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toMatchObject({ op: 'rename_feature', name: 'Plate' })
  await waitFor(() => expect(screen.queryByRole('textbox')).toBeNull())

  rightClick(extrudeRow())
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }))
  const ask = await screen.findByRole('dialog')
  fireEvent.click(within(ask).getByRole('button', { name: 'Delete' }))
  await waitFor(() => expect(edits().length).toBe(2))
  expect(edits()[1]?.body).toMatchObject({ op: 'delete_features' })
  store.stop()
})

it('Suppress in the feature menu sends set_suppressed, and an Unsuppress follows on a suppressed feature', async () => {
  const { store, edits } = await studio('desktop')
  rightClick(extrudeRow())
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Suppress' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toMatchObject({ op: 'set_suppressed', value: true })
  store.stop()
})

it('a right click on a part row and on a tab opens the desktop menu; a right drag opens nothing', async () => {
  const { store } = await studio('desktop')
  const part = document.querySelector<HTMLElement>('.part-select')!
  rightClick(part)
  const menu = await screen.findByRole('menu')
  expect(within(menu).getAllByRole('menuitem').map((b) => b.textContent)).toEqual([
    'Rename…', 'Change colour…', 'Material…', expect.stringMatching(/^(Hide|Show)$/), 'Isolate', 'Mass properties', 'Create drawing…', 'Export STEP…', 'Export STL…', 'Export BREP…', 'Delete part…',
  ])
  // Export is left for later (d-s11-part-menu): shown, off, with the tooltip
  expect((within(menu).getByRole('menuitem', { name: 'Export STEP…' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.keyDown(menu, { key: 'Escape' })
  expect(screen.queryByRole('menu')).toBeNull()

  // a right press that moves 30 px before it is released is not a click
  fireEvent.pointerDown(part, { pointerId: 7, pointerType: 'mouse', button: 2, clientX: 50, clientY: 50 })
  fireEvent.pointerUp(part, { pointerId: 7, pointerType: 'mouse', button: 2, clientX: 80, clientY: 50 })
  expect(screen.queryByRole('menu')).toBeNull()

  rightClick(screen.getByRole('button', { name: 'Part Studio 1' }))
  const tabs = await screen.findByRole('menu', { name: 'Part Studio 1 menu' })
  expect(within(tabs).getAllByRole('menuitem').map((b) => b.textContent)).toEqual(['Rename…', 'Duplicate', 'Close'])
  store.stop()
})

it('Tablet mode: a right click opens nothing and the long press opens the popover as before; Desktop mode: a long press opens nothing', async () => {
  const { store } = await studio('tablet')
  rightClick(extrudeRow())
  expect(screen.queryByRole('menu')).toBeNull()
  const hold = (el: HTMLElement) => {
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
  hold(extrudeRow())
  const popover = await screen.findByRole('menu')
  expect(within(popover).getAllByRole('menuitem').map((b) => b.textContent)).toContain('Roll to here')
  expect(screen.queryByTestId('context-menu')).toBeNull()
  store.stop()
  cleanup()

  const again = await studio('desktop')
  hold(extrudeRow())
  expect(screen.queryByRole('menu')).toBeNull()
  again.store.stop()
})

it('in a sketch the view menu offers Normal to sketch, Finish sketch and Cancel sketch; Finish sends sketch.commit', async () => {
  const { store, sock } = await studio('desktop')
  act(() => window.__freeshape!.openSketch({ plane: { kind: 'std', name: 'Front' } }))
  sock.say({ ...openedMessage(), document: 'd', tab: 'T1' })
  await waitFor(() => expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull())
  const canvas = document.querySelector('.viewport-canvas')!
  rightClick(canvas, 300, 200)
  const menu = await screen.findByRole('menu', { name: 'Sketch menu' })
  expect(within(menu).getAllByRole('menuitem').map((b) => b.textContent)).toEqual(['Normal to sketch', 'Finish sketch', 'Cancel sketch'])
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Finish sketch' }))
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.commit' })
  sock.say({ type: 'sketch.committed', document: 'd', tab: 'T1', feature: 'F9', rebuilt: [], revision: 2 })
  await waitFor(() => expect(document.querySelector('[data-strip="feature"]')).not.toBeNull())
  store.stop()
})

it('Cancel sketch from the view menu asks first when the sketch has changes, and cancels at once when it has none', async () => {
  const { store, sock } = await studio('desktop')
  act(() => window.__freeshape!.openSketch({ plane: { kind: 'std', name: 'Front' } }))
  sock.say({ ...openedMessage(), document: 'd', tab: 'T1' })
  await waitFor(() => expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull())
  const canvas = document.querySelector('.viewport-canvas')!
  // changed: asks
  const data = openedMessage().sketch
  sock.say({ type: 'sketch.changed', document: 'd', tab: 'T1', feature: null, name: 'Sketch 2', plane: {}, sketch: data, status: openedMessage().status, can_undo: true, can_redo: false })
  await waitFor(() => expect(document.querySelector<HTMLButtonElement>('.desktop-toolbar [data-tool="undo"]')?.disabled).toBe(false))
  rightClick(canvas)
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Cancel sketch' }))
  const ask = await screen.findByText('Discard this sketch?')
  expect(sock.sent.some((m) => m['type'] === 'sketch.cancel')).toBe(false)
  fireEvent.click(within(ask.closest('[role="dialog"]') as HTMLElement).getByRole('button', { name: 'Discard' }))
  expect(sock.sent.at(-1)).toEqual({ type: 'sketch.cancel' })
  store.stop()
})

/** The hinge Assembly with its tab on screen (Desktop mode). */
async function assembly() {
  window.localStorage.setItem(MODE_KEY, 'desktop')
  const raw = readFileSync(resolve(process.cwd(), 'fixtures', 'hinge.assembly.fsw'))
  const bytes = raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer
  const calls = stubFetch((c) => (c.method === 'GET' && c.url.endsWith('/outputs') ? { bytes } : c.url.endsWith('/edit') ? { json: { rebuilt: ['T2'] } } : undefined))
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say(hello([tab('T1', 'studio', 'hinge_parts'), tab('T2', 'assembly', 'hinge')]))
  fireEvent.click(await screen.findByRole('button', { name: 'hinge' }))
  await screen.findByTestId('instance-list')
  const edits = () => calls.filter((c: Call) => c.method === 'POST' && c.url.endsWith('/edit'))
  return { store, edits }
}

it('on an Assembly instance, Fix sends set_fixed and Hide sends set_hidden; Add mate connector sends add_origin_connector (I10.4)', async () => {
  const { store, edits } = await assembly()
  const row = () => document.querySelector<HTMLElement>('[data-instance="I2"]')!
  rightClick(row())
  const menu = await screen.findByRole('menu', { name: /menu$/ })
  expect(within(menu).getAllByRole('menuitem').map((b) => b.textContent)).toEqual([
    'Rename…', 'Fix', 'Hide', 'Isolate', 'Suppress', 'Change colour…', 'Add mate connector to instance origin', 'Move / rotate…', 'Group', 'Duplicate', 'Mass properties', 'Delete',
  ])
  const connector = within(menu).getByRole('menuitem', { name: 'Add mate connector to instance origin' }) as HTMLButtonElement
  expect(connector.disabled).toBe(false)
  expect(connector.title).toBe('')
  expect((within(menu).getByRole('menuitem', { name: 'Group' }) as HTMLButtonElement).title).toBe(DESKTOP_ONLY)
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Fix' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toEqual({ op: 'set_fixed', instance: 'I2', value: true })
  await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
  rightClick(row())
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Hide' }))
  await waitFor(() => expect(edits().length).toBe(2))
  expect(edits()[1]?.body).toEqual({ op: 'set_hidden', instance: 'I2', value: true })
  await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
  rightClick(row())
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Add mate connector to instance origin' }))
  await waitFor(() => expect(edits().length).toBe(3))
  expect(edits()[2]?.body).toEqual({ op: 'add_origin_connector', instance: 'I2' })
  store.stop()
})

it('Rename in the instance menu sends rename, and a mate row has a menu with Edit, Rename, Suppress and Delete on (I10.6)', async () => {
  const { store, edits } = await assembly()
  rightClick(document.querySelector<HTMLElement>('[data-instance="I1"]')!)
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Rename…' }))
  const field = (await screen.findByRole('textbox')) as HTMLInputElement
  fireEvent.change(field, { target: { value: 'Plate' } })
  fireEvent.click(screen.getByRole('button', { name: 'Rename' }))
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toEqual({ op: 'rename', id: 'I1', name: 'Plate' })
  await waitFor(() => expect(screen.queryByRole('textbox')).toBeNull())

  rightClick(document.querySelector<HTMLElement>('[data-mate-row]')!)
  const menu = await screen.findByRole('menu', { name: 'Mate menu' })
  const items = within(menu).getAllByRole('menuitem') as HTMLButtonElement[]
  expect(items.map((b) => b.textContent)).toEqual(['Edit…', 'Rename…', 'Suppress', 'Delete'])
  expect(items.every((b) => !b.disabled)).toBe(true)
  // Suppress and Delete send their edits; Rename opens the prompt
  fireEvent.click(items[2]!)
  await waitFor(() => expect(edits().length).toBe(2))
  expect(edits()[1]?.body).toEqual({ op: 'set_suppressed', mate: 'M4', value: true })
  rightClick(document.querySelector<HTMLElement>('[data-mate-row]')!)
  fireEvent.click(within(await screen.findByRole('menu', { name: 'Mate menu' })).getByRole('menuitem', { name: 'Delete' }))
  await waitFor(() => expect(edits().length).toBe(3))
  expect(edits()[2]?.body).toEqual({ op: 'delete_mates', ids: ['M4'] })
  store.stop()
})

it('the menus as data: the desktop items in the desktop order', () => {
  const f = { id: 'F2', type: 'extrude', label: 'Extrude', name: 'Extrude 1', suppressed: false, error: null, folder: null, hidden: false }
  const a = { rename: () => {}, setHidden: () => {}, setSuppressed: () => {}, rollTo: () => {}, remove: () => {} }
  expect(labels(featureMenu({ ...f, type: 'sketch', folder: 'G1', error: 'bad' }, { index: 0, total: 3, rollback: 2, busy: false, actions: a }))).toEqual([
    'Edit…', 'Rename…', 'Hide', 'Export DXF…', '|', 'Suppress', 'Roll to here', 'Roll to end', '|', 'Add to new folder', 'Remove from folder', '|', 'Delete', '|', '⚠ bad',
  ])
  expect(labels(featureMenu({ ...f, type: 'context' }, { index: 0, total: 3, rollback: 3, busy: false, actions: a }))).toEqual([
    'Edit…', 'Rename…', 'Update context', 'Hide context', '|', 'Suppress', 'Roll to here', '|', 'Add to new folder', '|', 'Delete',
  ])
  expect(labels(partMenu({ name: 'P', hidden: true }, { busy: false, actions: { rename: () => {}, colour: () => {}, setHidden: () => {} } }))[3]).toBe('Show')
  expect(labels(tabMenu({ rename: () => {}, close: () => {} }))).toEqual(['Rename…', 'Duplicate', '|', 'Close'])
  expect(labels(sketchViewMenu({ normalTo: () => {}, finish: () => {}, cancel: () => {} }))).toEqual(['Normal to sketch', 'Finish sketch', 'Cancel sketch'])
  expect(labels(mateMenu({ type: 'revolute', suppressed: true }, 'x'))).toEqual(['Edit…', 'Rename…', 'Unsuppress', 'Set value…', 'Animate…', '|', 'Delete', '|', '⚠ x'])
  expect(labels(instanceMenu({ name: 'I', hidden: true, fixed: true }, { busy: false, actions: { rename: () => {}, setFixed: () => {}, setHidden: () => {}, isolate: () => {}, addConnector: () => {}, move: () => {}, remove: () => {} } }))).toContain('Unfix')
  const view = { fit: () => {}, view: () => {}, showAll: () => {} }
  expect(labels(assemblyViewMenu({ anyHidden: true, readOnly: false, ...view }))).toEqual(['Insert parts…', 'Mass properties', 'Zoom to fit', 'Views', 'Show all'])
  const actions = { setPartsHidden: () => {}, isolate: () => {}, selectPart: () => {}, editSketch: () => {}, hideSketch: () => {}, hidePlane: () => {}, hideAxis: () => {}, showAll: () => {}, fit: () => {}, view: () => {} }
  const empty = { parts: [], anyHidden: false, partCount: 1, readOnly: false, actions }
  expect(labels(studioViewMenu({ ...empty, kind: null }))).toEqual(['Zoom to fit', 'Views'])
  expect(labels(studioViewMenu({ ...empty, kind: null, anyHidden: true, partCount: 2 }))).toEqual(['Zoom to fit', 'Views', 'Show all parts', 'Auto colour all parts'])
  expect(labels(studioViewMenu({ ...empty, kind: 'face', parts: [{ id: 'F2/1', hidden: false }] }))).toEqual([
    'New sketch', 'Normal to', 'Hide part', 'Isolate part', 'Select part', 'Mass properties', 'Delete part…', '|', 'Zoom to fit', 'Views',
  ])
  expect(labels(studioViewMenu({ ...empty, kind: 'region' }))).toEqual(['Edit sketch', 'Hide sketch', '|', 'Zoom to fit', 'Views'])
})

it('a menu opened near the bottom right corner stays inside the window', () => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 })
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 700 })
  const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 240, height: 300, left: 0, top: 0, right: 240, bottom: 300, x: 0, y: 0, toJSON: () => ({}) })
  const data = tabMenu({ rename: () => {}, close: () => {} })
  render(<ContextMenu at={{ x: 990, y: 690, label: 'm', ...data }} onClose={() => {}} />)
  const menu = screen.getByRole('menu')
  const left = parseFloat(menu.style.left)
  const top = parseFloat(menu.style.top)
  expect(left).toBe(1000 - 240 - 4)
  // no room below: it opens over the pointer instead
  expect(top).toBe(690 - 300)
  expect(left + 240).toBeLessThanOrEqual(1000)
  expect(top + 300).toBeLessThanOrEqual(700)
  rect.mockRestore()
})
