import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { attachKeys, keymapFor } from '../input/keys'
import { MODE_KEY } from '../shell/mode'
import { assemblyViewMenu } from '../shell/contextMenus'
import { desktopCommands } from '../shell/desktopKeys'
import type { DesktopKeyContext } from '../shell/desktopKeys'
import { Shell } from '../shell/Shell'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { keyOf } from '../test/keys'

// The Insert panel (card I10.1) on the hinge Document: the Part Studio T1 (three parts) and the Assembly T2, in both modes.
beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} }))
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const fixture = (name: string): ArrayBuffer => {
  const b = readFileSync(resolve(process.cwd(), 'fixtures', name))
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
}

async function open(mode: 'desktop' | 'tablet', readOnly = false) {
  window.localStorage.setItem(MODE_KEY, mode)
  const calls = stubFetch((c) =>
    c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')
      ? { bytes: fixture('hinge.studio.fsw') }
      : c.method === 'GET' && c.url.endsWith('/outputs')
        ? { bytes: fixture('hinge.assembly.fsw') }
        : c.url.endsWith('/assembly/types')
          ? { json: { document: 'd', tab: 'T2', mates: [{ id: 'fastened', label: 'Fastened', help: 'h', freedoms: [] }], relations: [{ id: 'linear', label: 'Linear', help: 'h' }] } }
          : c.url.endsWith('/edit')
            ? { json: { rebuilt: ['T2'] } }
            : undefined,
  )
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Hinge' }, tabs: [tab('T1', 'studio', 'hinge_parts'), tab('T2', 'assembly', 'hinge')], read_only: readOnly, holder: holderOf('x') })
  await screen.findByRole('button', { name: 'hinge' })
  fireEvent.click(screen.getByRole('button', { name: 'hinge' }))
  await screen.findByTestId('instance-list')
  const edits = () => calls.filter((c: Call) => c.method === 'POST' && c.url.endsWith('/edit')).map((c) => c.body as Record<string, unknown>)
  return { store, calls, edits }
}

const toolbarInsert = () => (document.querySelector('.desktop-toolbar [data-tool="insert"]') ?? document.querySelector('.tool-strip > [data-tool="insert"]')) as HTMLButtonElement
const panel = () => screen.queryByTestId('insert-panel')
const pressI = () => fireEvent.keyDown(window, keyOf('I'))

it('lists the hinge Document\'s Part Studio with its three parts, each with its colour', async () => {
  const { store } = await open('desktop')
  fireEvent.click(toolbarInsert())
  const list = await screen.findByTestId('part-picker')
  await waitFor(() => expect(list.querySelectorAll('[data-picker-part]').length).toBe(3))
  expect(list.querySelectorAll('[data-picker-studio]').length).toBe(1)
  expect(list.querySelector('[data-picker-studio]')?.textContent).toBe('hinge_parts')
  expect([...list.querySelectorAll('[data-picker-part]')].map((r) => r.textContent)).toEqual(['Base plate', 'Arm', 'Pin'])
  for (const sw of list.querySelectorAll<HTMLElement>('.part-swatch')) expect(sw.style.background).toMatch(/^rgb\(/)
  store.stop()
})

it('Desktop: I opens and closes the panel; the toolbar button follows', async () => {
  const { store } = await open('desktop')
  expect(panel()).toBeNull()
  pressI()
  await screen.findByTestId('insert-panel')
  expect(toolbarInsert().getAttribute('aria-pressed')).toBe('true')
  expect(screen.getByRole('dialog', { name: 'Insert parts' })).toBeTruthy()
  pressI()
  await waitFor(() => expect(panel()).toBeNull())
  fireEvent.click(toolbarInsert())
  await screen.findByTestId('insert-panel')
  fireEvent.keyDown(window, keyOf('Escape'))
  await waitFor(() => expect(panel()).toBeNull())
  store.stop()
})

it('Desktop: a double-click on a part sends insert with its tab and part id; the Part Studio row sends insert with no part', async () => {
  const { store, edits } = await open('desktop')
  pressI()
  const list = await screen.findByTestId('part-picker')
  await waitFor(() => expect(list.querySelectorAll('[data-picker-part]').length).toBe(3))
  const arm = [...list.querySelectorAll<HTMLElement>('[data-picker-part]')].find((r) => r.textContent === 'Arm')!
  fireEvent.click(arm)
  expect(edits()).toEqual([])                                   // a click only selects
  fireEvent.doubleClick(arm)
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]).toEqual({ op: 'insert', tab: 'T1', part: arm.dataset['pickerPart'] })
  await waitFor(() => expect(screen.getByTestId('insert-said').textContent).toBe('Inserted Arm'))
  expect(panel()).not.toBeNull()                                // it stays open
  fireEvent.doubleClick(list.querySelector('[data-picker-studio]')!)
  await waitFor(() => expect(edits().length).toBe(2))
  expect(edits()[1]).toEqual({ op: 'insert', tab: 'T1' })
  await waitFor(() => expect(screen.getByTestId('insert-said').textContent).toBe('Inserted hinge_parts'))
  store.stop()
})

it('Tablet: the strip\'s Insert toggles the panel over the column; a tap selects and Insert inserts', async () => {
  const { store, edits } = await open('tablet')
  const button = toolbarInsert()
  expect(button.disabled).toBe(false)
  fireEvent.click(button)
  const list = await screen.findByTestId('part-picker')
  expect(screen.getByRole('dialog', { name: 'Insert parts' }).classList.contains('dialog-floating')).toBe(false)
  await waitFor(() => expect(list.querySelectorAll('[data-picker-part]').length).toBe(3))
  const go = document.querySelector<HTMLButtonElement>('[data-insert-go]')!
  expect(go.disabled).toBe(true)                                // nothing selected yet
  const pin = [...list.querySelectorAll<HTMLElement>('[data-picker-part]')].find((r) => r.textContent === 'Pin')!
  fireEvent.doubleClick(pin)
  expect(edits()).toEqual([])                                   // a tap does not double-click insert in Tablet mode
  fireEvent.click(pin)
  expect(go.disabled).toBe(false)
  fireEvent.click(go)
  await waitFor(() => expect(edits()).toEqual([{ op: 'insert', tab: 'T1', part: pin.dataset['pickerPart'] }]))
  await waitFor(() => expect(screen.getByTestId('insert-said').textContent).toBe('Inserted Pin'))
  fireEvent.click(toolbarInsert())
  await waitFor(() => expect(panel()).toBeNull())
  store.stop()
})

it('shows From file, Other Document, Component library and Standard parts off, saying Desktop only for now', async () => {
  const { store } = await open('desktop')
  pressI()
  const p = await screen.findByTestId('insert-panel')
  for (const label of ['From file…', 'Other Document…', 'Component library…', 'Standard parts']) {
    const b = within(p).getByRole('button', { name: label }) as HTMLButtonElement
    expect(b.disabled).toBe(true)
    expect(b.title).toBe('Desktop only for now')
  }
  store.stop()
})

it('a read-only Document cannot insert: the button is off and I does nothing', async () => {
  const { store } = await open('desktop', true)
  expect(toolbarInsert().disabled).toBe(true)
  pressI()
  expect(panel()).toBeNull()
  store.stop()
})

it('the Assembly\'s empty-view menu opens the panel through its Insert parts… item', () => {
  const insert = vi.fn()
  const menu = assemblyViewMenu({ anyHidden: false, readOnly: false, insert, fit: () => {}, view: () => {}, showAll: () => {} })
  menu.commands['view.insert']!()
  expect(insert).toHaveBeenCalledOnce()
  const off = assemblyViewMenu({ anyHidden: false, readOnly: true, insert, fit: () => {}, view: () => {}, showAll: () => {} })
  const item = off.entries.find((e) => 'command' in e && e.command === 'view.insert')!
  expect('enabled' in item && item.enabled?.({} as never)).toBe(false)
})

it('the I key: Insert on an Assembly tab, left to the sketch\'s Coincident in a sketch and to the browser elsewhere', () => {
  window.localStorage.setItem(MODE_KEY, 'desktop')
  const toggleInsert = vi.fn()
  const run = (over: Partial<DesktopKeyContext>): { taken: boolean } => {
    const ctx = { readOnly: false, tabKind: 'assembly', sketch: null, dialog: () => null, closeArea: null, modalOpen: false, toggleInsert, ...over } as unknown as DesktopKeyContext
    const off = attachKeys(() => desktopCommands(ctx), () => keymapFor('desktop'))
    const e = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...keyOf('I') })
    window.dispatchEvent(e)
    off()
    return { taken: e.defaultPrevented }
  }
  expect(keymapFor('desktop')['I']).toBe('sketch.tool.coincident assembly.insert')
  expect(run({})).toEqual({ taken: true })
  expect(toggleInsert).toHaveBeenCalledTimes(1)
  expect(run({ readOnly: true })).toEqual({ taken: true })
  expect(run({ modalOpen: true })).toEqual({ taken: false })      // a small dialog over the page has the keyboard
  expect(toggleInsert).toHaveBeenCalledTimes(1)                 // neither of those acted
  expect(run({ tabKind: 'studio' })).toEqual({ taken: false })
  expect(toggleInsert).toHaveBeenCalledTimes(1)
})
