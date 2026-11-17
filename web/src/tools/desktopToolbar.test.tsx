import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { MODE_KEY } from '../shell/mode'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { registerView } from '../viewport/viewCommands'
import { readFileSync } from 'node:fs'
import { DESKTOP_TOOLBAR, desktopToolIds } from './defs'
import type { DesktopLayout } from './defs'
import { iconNames } from './icons'
import { DesktopToolbar } from './DesktopToolbar'
import type { ToolbarCommand } from './DesktopToolbar'
import { DESKTOP_ONLY_NOTE } from './ToolStrip'
import type { ToolState } from './useToolTypes'

const css = readFileSync('src/tools/toolbar.css', 'utf8')

// The Desktop-mode feature toolbar (card I9.3): its order, its buttons, and the Shell flows (Extrude, Fit, Iso).
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
  window.localStorage.clear()
})

/**
 * The order of `_build_feature_toolbar` (freeshape/ui/mainwindow.py at cafbd2c), read off its calls: a tool id per button,
 * '|' for each `h.addWidget(_sep())`, '>' where the stretch starts the right end. Left out: the hidden configuration box
 * and `pyfeature_ui.toolbar_button`, which makes one button per installed Plugin at run time.
 */
const MAINWINDOW_AT_CAFBD2C = [
  'undo', 'redo', '|',
  'variable', 'variable_studio_ref', 'configs', 'derived', 'history', 'plugin', '|',
  'sketch', '|',
  'extrude', 'revolve', 'sweep', 'rib', 'loft', 'boundary_surface', 'fillet', 'chamfer', 'draft', 'shell', 'hole', 'thread',
  'delete_face', 'move_face', 'offset_surface', 'thicken', 'enclose', 'fill', 'delete_part', '|',
  'sm_base', 'sm_flange', 'sm_hem', 'sm_jog', 'sm_tab', 'sm_convert', 'flat', 'flat_dxf', 'nest', '|',
  'plane', 'axis', '|',
  'mirror', 'linear_pattern', 'circular_pattern', 'boolean', 'split', 'move', 'mate_connector', '|',
  'import', 'canvas', 'export', // Canvas after Import: card I12.5 (the desktop's own button comes with I12.3)
  '>',
  'section', 'interference', 'fit', 'iso',
]

/** What a rendered row holds, left to right, in the same notation. */
const rowOf = (): string[] =>
  [...document.querySelectorAll('.tb-scroll > *'), null, ...document.querySelectorAll('.tb-end > *')].map((e) => (e === null ? '>' : e.getAttribute('role') === 'separator' ? '|' : e.getAttribute('data-tool')!))

const layoutOk = (): ToolState => 'ok'
const noCommands: Record<string, ToolbarCommand> = {}
const button = (id: string) => document.querySelector(`.desktop-toolbar [data-tool="${id}"]`) as HTMLButtonElement

it('the desktop row lists its tools in the order of _build_feature_toolbar, with a rule where the desktop has a separator', () => {
  render(<DesktopToolbar layout={DESKTOP_TOOLBAR} stateOf={layoutOk} commands={noCommands} onStart={() => {}} />)
  expect(rowOf()).toEqual(MAINWINDOW_AT_CAFBD2C)
  expect(desktopToolIds(DESKTOP_TOOLBAR)).toEqual(MAINWINDOW_AT_CAFBD2C.filter((x) => x !== '|' && x !== '>'))
})

it('every tool of the row has an exported desktop icon', () => {
  const have = new Set(iconNames())
  expect(desktopToolIds(DESKTOP_TOOLBAR).filter((id) => !have.has(DESKTOP_ENTRY_ICON(id)))).toEqual([])
})
const DESKTOP_ENTRY_ICON = (id: string): string => [...DESKTOP_TOOLBAR.items, ...DESKTOP_TOOLBAR.trailing].flatMap((e) => (e.kind === 'separator' ? [] : [e])).find((e) => e.tool.id === id)!.tool.icon

it('every button is 26 px, shows no text and has a tooltip', () => {
  render(<DesktopToolbar layout={DESKTOP_TOOLBAR} stateOf={layoutOk} commands={noCommands} onStart={() => {}} />)
  const rule = /\.tb-button\s*\{[^}]*\}/.exec(css)![0]
  expect(rule).toMatch(/width:\s*26px/)
  expect(rule).toMatch(/height:\s*26px/)
  const buttons = [...document.querySelectorAll<HTMLButtonElement>('.desktop-toolbar button')]
  expect(buttons.length).toBe(desktopToolIds(DESKTOP_TOOLBAR).length)
  for (const b of buttons) {
    expect(b.textContent).toBe('')
    expect(b.title.length).toBeGreaterThan(0)
    expect(b.getAttribute('aria-label')).toBeTruthy()
    expect(b.querySelector('img')?.getAttribute('width')).toBe('26')
  }
})

it('a tooltip is the label and the desktop key', () => {
  render(<DesktopToolbar layout={DESKTOP_TOOLBAR} stateOf={layoutOk} commands={{ fit: { run: () => {} }, undo: { run: () => {} } }} onStart={() => {}} />)
  expect(button('extrude').title).toBe('Extrude (Shift+E)')
  expect(button('sketch').title).toBe('Sketch (Shift+S)')
  expect(button('fit').title).toBe('Zoom to fit (F)')
  expect(button('undo').title).toBe('Undo (Ctrl+Z)')
  expect(button('plane').title).toBe('Plane')
  expect(button('linear_pattern').title).toBe('Linear pattern')
})

it('a desktop or off tool is disabled with the desktop-only note, a dimmed tool is enabled with its reason, an ok tool starts', () => {
  const onStart = vi.fn()
  const states: Record<string, ToolState> = { sm_base: 'desktop', derived: 'off', revolve: 'dimmed' }
  render(<DesktopToolbar layout={DESKTOP_TOOLBAR} stateOf={(id) => states[id] ?? 'ok'} commands={noCommands} onStart={onStart} />)
  for (const id of ['sm_base', 'derived']) {
    expect(button(id).disabled).toBe(true)
    expect(button(id).title).toBe(DESKTOP_ONLY_NOTE)
  }
  expect(button('revolve').disabled).toBe(false)
  expect(button('revolve').title).toMatch(/^Revolve \(Shift\+W\) — /)
  fireEvent.click(button('revolve'))
  expect(onStart).not.toHaveBeenCalled()
  fireEvent.click(button('fillet'))
  expect(onStart).toHaveBeenCalledExactlyOnceWith('fillet')
})

it('a button that is not a feature is disabled with the desktop-only note unless the shell has it', () => {
  const run = vi.fn()
  render(<DesktopToolbar layout={DESKTOP_TOOLBAR} stateOf={layoutOk} commands={{ fit: { run }, redo: { run, enabled: false } }} onStart={() => {}} />)
  for (const id of ['variable', 'configs', 'history', 'plugin', 'import', 'export', 'section', 'interference', 'flat', 'flat_dxf', 'nest']) {
    expect(button(id).disabled).toBe(true)
    expect(button(id).title).toBe(DESKTOP_ONLY_NOTE)
  }
  expect(button('redo').disabled).toBe(true)
  expect(button('fit').disabled).toBe(false)
  fireEvent.click(button('fit'))
  expect(run).toHaveBeenCalledOnce()
})

it('a row is drawn from any list in the layout format', () => {
  const two: DesktopLayout = {
    items: [{ kind: 'feature', tool: { id: 'extrude', label: 'Extrude', icon: 'extrude' }, key: 'Shift+E' }, { kind: 'separator' }],
    trailing: [{ kind: 'command', tool: { id: 'fit', label: 'Zoom to fit', icon: 'fit' } }],
  }
  const onStart = vi.fn()
  const fit = vi.fn()
  render(<DesktopToolbar layout={two} stateOf={() => 'ok'} commands={{ fit: { run: fit } }} onStart={onStart} />)
  expect(rowOf()).toEqual(['extrude', '|', '>', 'fit'])
  fireEvent.click(button('extrude'))
  fireEvent.click(button('fit'))
  expect(onStart).toHaveBeenCalledWith('extrude')
  expect(fit).toHaveBeenCalledOnce()
})

// ---- the whole Shell, in Desktop mode

const typeRow = (type: string, extra: Partial<{ standard: boolean; can_start: boolean }> = {}) => ({ type, label: type, standard: true, can_start: true, ...extra })
const ALL = desktopToolIds(DESKTOP_TOOLBAR)
const created = {
  dialog: 'did9',
  document: 'd',
  tab: 'T1',
  feature: null,
  type: 'extrude',
  name: 'Extrude 2',
  revision: 1,
  description: { type: 'extrude', custom: [], fields: [{ key: 'depth', kind: 'num', standard: true, visible: true, label: 'Depth', unit: 'mm', min: null, signed: false }] },
  values: { depth: { value: 10, text: '10' } },
  fields: { profiles: [{ ok: true, kind: 'face', ref: {}, name: 'Face4' }] },
  active: 'profiles',
}

async function mountDesktop() {
  window.localStorage.setItem(MODE_KEY, 'desktop')
  const { bytes } = fixture('bracket')
  const calls = stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.includes('/features/types')) {
      // the server's rows: the standard types are ok, Sheet metal has a row only the desktop draws, Derived is not offered
      return { json: { document: 'd', tab: 'T1', revision: 1, types: ALL.filter((id) => id !== 'derived').map((id) => typeRow(id, id === 'sm_base' ? { standard: false } : {})) } }
    }
    if (c.method === 'POST' && c.url.endsWith('/features/dialog')) return { status: 201, json: created }
    if (c.url.endsWith('/cancel')) return { json: { cancelled: true } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  return { store, calls }
}

it('Desktop mode draws the toolbar and not the tablet strip, and the types route sets each feature button', async () => {
  const { store } = await mountDesktop()
  expect(document.querySelector('.tool-strip')).toBeNull()
  await waitFor(() => expect(button('extrude').disabled).toBe(false))
  expect(button('sm_base').disabled).toBe(true)
  expect(button('sm_base').title).toBe(DESKTOP_ONLY_NOTE)
  expect(button('derived').disabled).toBe(true)
  expect(button('sketch').disabled).toBe(false)
  store.stop()
})

it('Extrude on the Desktop toolbar opens the same dialog as the tablet strip, with the selection as picks', async () => {
  const { store, calls } = await mountDesktop()
  const part = 'F2/1'
  act(() => window.__freeshape!.shell.selection.set([{ id: 'face|F2/1|Face4', kind: 'face', part, index: 3, name: 'Face4', key: null }]))
  await waitFor(() => expect(calls.some((c) => c.url.includes('?picks='))).toBe(true))
  await waitFor(() => expect(button('extrude').disabled).toBe(false))
  fireEvent.click(button('extrude'))
  const open = await waitFor(() => {
    const c = calls.find((x) => x.method === 'POST' && x.url.endsWith('/features/dialog'))
    expect(c).toBeTruthy()
    return c!
  })
  // the body the tablet strip's Extrude sends (tools.test.tsx)
  expect(open.body).toEqual({ type: 'extrude', picks: [{ part, kind: 'face', index: 4 }] })
  await screen.findByRole('dialog', { name: 'Extrude 2' })
  // the feature buttons are off while the dialog is open
  expect(button('fillet').disabled).toBe(true)
  store.stop()
})

it('Fit calls the viewport fit and Iso its isometric view', async () => {
  const { store } = await mountDesktop()
  const view = { fit: vi.fn(), setStandardView: vi.fn() }
  const unregister = registerView(view)
  fireEvent.click(button('fit'))
  expect(view.fit).toHaveBeenCalledOnce()
  fireEvent.click(button('iso'))
  expect(view.setStandardView).toHaveBeenCalledExactlyOnceWith('iso')
  unregister()
  store.stop()
})
