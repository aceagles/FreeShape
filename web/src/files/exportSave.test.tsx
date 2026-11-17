import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { MODE_KEY } from '../shell/mode'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { EXPORT_KEY } from './ExportDialog'

// Save an export to a server folder from the Export dialog (card I11.14), through the whole Shell, in both modes.
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

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  URL.createObjectURL = (b: Blob | MediaSource) => `blob:${(b as Blob).size}`
  URL.revokeObjectURL = () => {}
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const FILE = new Uint8Array([1, 2, 3, 4]).buffer
const ROOTS = [{ name: 'shared', max_bytes: 268435456 }]
type Post = (c: Call) => { status?: number; json?: unknown }

/** The Shell on the bracket, a server that lists `roots` and answers a save with `onPost`. */
async function open(mode: 'desktop' | 'tablet', roots: Array<{ name: string; max_bytes: number }>, onPost: Post = () => ({ status: 201, json: { root: 'shared', name: 'bracket.stl', bytes: 9, replaced: false, skipped: [] } })) {
  window.localStorage.setItem(MODE_KEY, mode)
  const { bytes, outputs } = fixture('bracket')
  const clicks: string[] = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    clicks.push(this.download)
  })
  const calls = stubFetch((c: Call) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.method === 'GET' && c.url === '/files') return { json: { roots: [], export_roots: roots } }
    if (c.method === 'GET' && c.url.includes('/export?')) return { bytes: FILE, headers: { 'Content-Disposition': 'attachment; filename="bracket.stl"' } }
    if (c.method === 'POST' && c.url.endsWith('/tabs/T1/export')) return onPost(c)
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={vi.fn()} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'bracket')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(outputs.revision))
  return { calls, clicks, store, part: outputs.parts[0]!.id }
}
const posts = (calls: Call[]) => calls.filter((c) => c.method === 'POST' && c.url.endsWith('/export'))
const dialog = () => screen.getByRole('dialog', { name: 'Export' })
const menuItem = (name: string) => {
  fireEvent.click(screen.getByRole('button', { name: 'Document' }))
  fireEvent.click(screen.getByRole('menuitem', { name }))
}
const pick = (group: string, option: string) => {
  const g = within(dialog())
  const radio = g.queryByRole('radio', { name: option })
  if (radio !== null) return fireEvent.click(radio)
  fireEvent.click(g.getByRole('button', { name: new RegExp(`^${group}`) }))
  fireEvent.click(screen.getByRole('menuitemcheckbox', { name: option }))
}
const press = async (name: string) => {
  await act(async () => fireEvent.click(within(dialog()).getByRole('button', { name })))
}

it('with no export root there is no Destination row and Export downloads as before', async () => {
  const { calls, clicks, store } = await open('desktop', [])
  menuItem('Export STL…')
  await waitFor(() => expect(calls.some((c) => c.url === '/files')).toBe(true))
  expect(within(dialog()).queryByRole('button', { name: /^Destination/ })).toBeNull()
  expect(within(dialog()).queryByRole('textbox', { name: 'File name' })).toBeNull()
  await press('Export')
  expect(clicks).toEqual(['bracket.stl'])
  expect(posts(calls)).toEqual([])
  store.stop()
})

it('with one root the Destination row offers Download and Save to shared, and Download is chosen when nothing is stored', async () => {
  const { store } = await open('tablet', ROOTS)
  fireEvent.click(screen.getByRole('button', { name: 'Export' }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Export STEP / STL…' }))
  const d = await within(dialog()).findByRole('radiogroup', { name: 'Destination' })
  expect(within(d).getByRole('radio', { name: 'Download' }).getAttribute('aria-checked')).toBe('true')
  expect(within(d).getByRole('radio', { name: 'Save to shared' }).getAttribute('aria-checked')).toBe('false')
  expect(within(dialog()).queryByRole('button', { name: /^File name/ })).toBeNull()
  store.stop()
})

it('Desktop: Save to shared with STL, Fine and Binary posts the root, the name, the part and the options, downloads nothing and says where it went', async () => {
  const { calls, clicks, part, store } = await open('desktop', ROOTS)
  act(() => window.__freeshape!.shell.selectPart(part))
  menuItem('Export STL…')
  await within(dialog()).findByRole('button', { name: /^Destination, Download/ })
  pick('Destination', 'Save to shared')
  const name = within(dialog()).getByRole('textbox', { name: 'File name' }) as HTMLInputElement
  expect(name.value).not.toBe('')
  fireEvent.change(name, { target: { value: 'bracket' } })
  expect(within(dialog()).getByText('.stl')).toBeTruthy()
  expect(within(dialog()).getByRole('button', { name: 'Save to shared' })).toBeTruthy()
  await press('Save to shared')
  expect(posts(calls).map((c) => c.body)).toEqual([{ root: 'shared', name: 'bracket', format: 'stl', parts: [part], preset: 'fine', ascii: false }])
  expect(calls.some((c) => c.url.includes('/export?'))).toBe(false)
  expect(clicks).toEqual([])
  expect(screen.queryByRole('dialog', { name: 'Export' })).toBeNull()
  expect((await screen.findByTestId('export-saved')).textContent).toContain('Saved bracket.stl to shared')
  expect(JSON.parse(window.localStorage.getItem(EXPORT_KEY)!)).toMatchObject({ dest: 'shared' })
  store.stop()
})

it('the file name starts from the part\'s name when one part is selected, else the tab\'s', async () => {
  const { part, store } = await open('desktop', ROOTS)
  window.localStorage.setItem(EXPORT_KEY, JSON.stringify({ format: 'stl', dest: 'shared' }))
  menuItem('Export STL…')
  const field = async () => (await within(dialog()).findByRole('textbox', { name: 'File name' })) as HTMLInputElement
  expect((await field()).value).toBe('bracket')
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Cancel' }))
  act(() => window.__freeshape!.shell.selectPart(part))
  menuItem('Export STL…')
  const partName = (await field()).value
  expect(partName).not.toBe('')
  store.stop()
})

it('Tablet: the name opens the text prompt, and what is typed there is what is posted', async () => {
  const { calls, store } = await open('tablet', ROOTS)
  fireEvent.click(screen.getByRole('button', { name: 'Export' }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Export STEP / STL…' }))
  fireEvent.click(await within(dialog()).findByRole('radio', { name: 'Save to shared' }))
  fireEvent.click(within(dialog()).getByRole('button', { name: /^File name, bracket\.step/ }))
  const prompt = screen.getByRole('dialog', { name: 'File name' })
  fireEvent.change(within(prompt).getByRole('textbox'), { target: { value: 'Base plate' } })
  fireEvent.click(within(prompt).getByRole('button', { name: 'OK' }))
  expect(screen.queryByRole('dialog', { name: 'File name' })).toBeNull()
  fireEvent.click(within(dialog()).getByRole('button', { name: /^File name, Base plate\.step/ }))
  fireEvent.click(within(screen.getByRole('dialog', { name: 'File name' })).getByRole('button', { name: 'Cancel' }))
  await press('Save to shared')
  expect(posts(calls).map((c) => c.body)).toEqual([{ root: 'shared', name: 'Base plate', format: 'step' }])
  store.stop()
})

it('a 409 exists asks to replace: Replace posts again with replace true, Cancel posts nothing more and keeps the dialog', async () => {
  let n = 0
  const { calls, store } = await open('desktop', ROOTS, () => {
    n += 1
    return n === 1 || n === 2 ? { status: 409, json: { error: 'exists', name: 'bracket.stl' } } : { status: 201, json: { root: 'shared', name: 'bracket.stl', bytes: 9, replaced: true, skipped: [] } }
  })
  window.localStorage.setItem(EXPORT_KEY, JSON.stringify({ format: 'stl', dest: 'shared' }))
  menuItem('Export STL…')
  await within(dialog()).findByRole('textbox', { name: 'File name' })
  await press('Save to shared')
  const ask = await screen.findByRole('dialog', { name: 'Replace file?' })
  expect(ask.textContent).toContain('Replace bracket.stl in shared?')
  fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' }))
  expect(posts(calls)).toHaveLength(1)
  expect(screen.queryByRole('dialog', { name: 'Replace file?' })).toBeNull()
  expect(screen.getByRole('dialog', { name: 'Export' })).toBeTruthy()
  await press('Save to shared')
  fireEvent.click(within(await screen.findByRole('dialog', { name: 'Replace file?' })).getByRole('button', { name: 'Replace' }))
  await waitFor(() => expect(posts(calls)).toHaveLength(3))
  expect(posts(calls)[0]!.body).not.toHaveProperty('replace')
  expect(posts(calls)[2]!.body).toMatchObject({ root: 'shared', replace: true })
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Export' })).toBeNull())
  store.stop()
})

it.each([
  [413, 'the export is 300 MB; the most that can be saved is 256 MB'],
  [507, 'the folder is full'],
  [409, 'bracket.stl is a folder'],
  [404, 'no export root named shared'],
])('a %i shows the server\'s reason and keeps the dialog open', async (status, error) => {
  const { store } = await open('desktop', ROOTS, () => ({ status, json: { error } }))
  window.localStorage.setItem(EXPORT_KEY, JSON.stringify({ format: 'stl', dest: 'shared' }))
  menuItem('Export STL…')
  await within(dialog()).findByRole('textbox', { name: 'File name' })
  await press('Save to shared')
  expect((await within(dialog()).findByTestId('export-error')).textContent).toBe(error)
  expect(screen.queryByRole('dialog', { name: 'Replace file?' })).toBeNull()
  store.stop()
})

it('a stored destination the server does not list falls back to Download', async () => {
  const { calls, clicks, store } = await open('desktop', ROOTS)
  window.localStorage.setItem(EXPORT_KEY, JSON.stringify({ format: 'stl', dest: 'gone' }))
  menuItem('Export STL…')
  await within(dialog()).findByRole('button', { name: /^Destination, Download/ })
  expect(within(dialog()).queryByRole('textbox', { name: 'File name' })).toBeNull()
  await press('Export')
  expect(clicks).toEqual(['bracket.stl'])
  expect(posts(calls)).toEqual([])
  store.stop()
})

it('the Export button is disabled and says it is working while the save runs', async () => {
  let finish: (() => void) | null = null
  const gate = new Promise<void>((r) => (finish = r))
  const { store } = await open('desktop', ROOTS, () => ({ status: 201, json: {} }))
  const realFetch = globalThis.fetch
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST' && url.endsWith('/export')) await gate
    return realFetch(url, init)
  })
  window.localStorage.setItem(EXPORT_KEY, JSON.stringify({ format: 'stl', dest: 'shared' }))
  menuItem('Export STL…')
  await within(dialog()).findByRole('textbox', { name: 'File name' })
  fireEvent.click(within(dialog()).getByRole('button', { name: 'Save to shared' }))
  const working = await within(dialog()).findByRole('button', { name: 'Saving to shared' })
  expect((working as HTMLButtonElement).disabled).toBe(true)
  expect(within(dialog()).getByTestId('export-working').textContent).toBe('Saving to shared…')
  await act(async () => finish!())
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Export' })).toBeNull())
  store.stop()
})
