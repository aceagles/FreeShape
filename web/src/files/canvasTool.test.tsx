import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MODE_KEY } from '../shell/mode'
import type { Mode } from '../shell/mode'
import { Shell } from '../shell/Shell'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { FakeXhr, installFakeXhr } from '../test/fakeXhr'
import { fixture } from '../test/fixtures'
import { CANVAS_IMAGE_LIMIT } from './canvasApi'

// Card I12.5: the Canvas tool. It opens the device's picker for a PNG or JPEG, sends the image to the tab (I11.8's upload through
// I12.2's images route), then opens the generic dialog for a new canvas on the uploaded image. The picker's input is made when it is
// asked for and is never on the page; the upload is a fake XMLHttpRequest the test drives.
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

let picked: HTMLInputElement[] = []
beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  installFakeXhr()
  picked = []
  // the file picker cannot open here: record the input it was asked on
  vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(function (this: HTMLInputElement) {
    picked.push(this)
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const UPLOADED = { document: 'd', tab: 'T1', blob: 'ab'.repeat(32), filename: 'photo.png', format: 'png', pixels: [64, 32], width: 16.9, height: 8.5 }
const created = {
  dialog: 'did7',
  document: 'd',
  tab: 'T1',
  feature: null,
  type: 'canvas',
  name: 'Canvas 2',
  revision: 1,
  description: { type: 'canvas', custom: [], fields: [{ key: 'width', kind: 'num', standard: true, visible: true, label: 'Width', unit: 'mm', min: 0, signed: false }] },
  values: { width: { value: 16.9, text: '16.9' } },
  fields: {},
  active: null,
}
const png = (name = 'photo.png', size = 114) => new File(['x'.repeat(size)], name, { type: 'image/png' })
const choose = (f: File) => {
  const input = picked[picked.length - 1]!
  Object.defineProperty(input, 'files', { value: [f], configurable: true })
  fireEvent.change(input)
}

async function shell(mode: Mode, outputs: 'bracket' | 'bracket.canvas' = 'bracket') {
  window.localStorage.setItem(MODE_KEY, mode)
  const { bytes } = fixture(outputs)
  const calls = stubFetch((c: Call) => {
    if (c.url === '/files') return { json: { roots: [{ name: 'lib', files: 1, max_upload: 1000 }], export_roots: [] } }
    if (c.url.startsWith('/files/lib')) return { json: { root: 'lib', dir: '', q: '', dirs: [], entries: [], total: 0, offset: 0, limit: 200 } }
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes } as never
    if (c.url.includes('/features/types')) return { json: { document: 'd', tab: 'T1', revision: 1, types: [] } }
    if (c.method === 'POST' && c.url.endsWith('/features/dialog')) return { status: 201, json: created }
    if (c.url.endsWith('/cancel')) return { json: { cancelled: true } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={vi.fn()} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture(outputs).outputs.revision))
  return { store, sock, calls }
}

const dialogPosts = (calls: Call[]) => calls.filter((c) => c.method === 'POST' && c.url.endsWith('/features/dialog'))
const nativeInputs = () => document.querySelectorAll('input, textarea, select, [contenteditable]').length

/** Tablet mode: Create, then Canvas. */
function tapCanvasInStrip() {
  fireEvent.pointerDown(screen.getByRole('button', { name: /^Create/ }))
  fireEvent.pointerUp(within(screen.getByRole('menu')).getByRole('menuitem', { name: /^Canvas/ }))
}

describe('Tablet mode', () => {
  it('Create lists Canvas after Import; tapping it opens the picker for PNG and JPEG, uploads the file, then opens the dialog on the uploaded image', async () => {
    const { calls } = await shell('tablet')
    fireEvent.pointerDown(screen.getByRole('button', { name: /^Create/ }))
    const names = within(screen.getByRole('menu')).getAllByRole('menuitem').map((m) => m.textContent ?? '')
    expect(names.findIndex((n) => /^Canvas/.test(n))).toBe(names.findIndex((n) => /^Import/.test(n)) + 1)
    fireEvent.pointerUp(within(screen.getByRole('menu')).getByRole('menuitem', { name: /^Canvas/ }))
    // the picker was asked for on an input that is not on the page
    expect(picked).toHaveLength(1)
    expect(picked[0]!.type).toBe('file')
    expect(picked[0]!.accept).toBe('image/png,image/jpeg,.png,.jpg,.jpeg')
    expect(picked[0]!.isConnected).toBe(false)
    expect(nativeInputs()).toBe(0)
    choose(png('photo.png', 300))
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    const x = FakeXhr.all[0]!
    expect(x.method).toBe('POST')
    expect(x.url).toBe('/documents/d/tabs/T1/images?name=photo.png')
    expect(x.headers['Content-Type']).toBe('application/octet-stream')
    expect(x.headers['X-Client-Id']).toBe('abcdefgh')
    x.progress(150, 300)
    expect((await screen.findByRole('progressbar')).getAttribute('aria-valuenow')).toBe('50')
    expect(dialogPosts(calls)).toHaveLength(0)
    x.reply(200, UPLOADED)
    await screen.findByRole('dialog', { name: 'Canvas 2' })
    expect(dialogPosts(calls)).toHaveLength(1)
    expect(dialogPosts(calls)[0]!.body).toEqual({ type: 'canvas', params: { blob: UPLOADED.blob, filename: 'photo.png' } })
    expect(dialogPosts(calls)[0]!.headers['X-Client-Id']).toBe('abcdefgh')
    expect(screen.queryByRole('progressbar')).toBeNull()
    // the number pad's rule: nothing native on the page, not while the dialog is open either
    expect(nativeInputs()).toBe(0)
  })

  it('a face selected before the tool is sent as the dialog\'s pick', async () => {
    const { calls } = await shell('tablet')
    const part = fixture('bracket').outputs.parts[0]!.id
    act(() => window.__freeshape!.shell.selection.set([{ id: `face|${part}|Face4`, kind: 'face', part, index: 3, name: 'Face4', key: null }]))
    tapCanvasInStrip()
    choose(png())
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    FakeXhr.all[0]!.reply(200, UPLOADED)
    await screen.findByRole('dialog', { name: 'Canvas 2' })
    expect(dialogPosts(calls)[0]!.body).toEqual({ type: 'canvas', picks: [{ part, kind: 'face', index: 4 }], params: { blob: UPLOADED.blob, filename: 'photo.png' } })
  })

  it("the server's refusal is shown with its own text and no dialog opens; a file over 20 MiB is refused before any request", async () => {
    const { calls } = await shell('tablet')
    tapCanvasInStrip()
    const big = png('big.png', 10)
    Object.defineProperty(big, 'size', { value: CANVAS_IMAGE_LIMIT + 1 })
    choose(big)
    await screen.findByText(/big.png is 20.0 MB; an image can be at most 20 MB/)
    expect(FakeXhr.all).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'OK' }))
    tapCanvasInStrip()
    choose(png('notes.png', 10))
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    FakeXhr.all[0]!.reply(415, { error: 'notes.png is a PDF document, not an image. Only PNG and JPEG images can be used' })
    await screen.findByText(/is a PDF document/)
    expect(dialogPosts(calls)).toHaveLength(0)
  })

  it('Cancel stops the upload; no dialog opens', async () => {
    const { calls } = await shell('tablet')
    tapCanvasInStrip()
    choose(png())
    await screen.findByRole('progressbar')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(FakeXhr.all[0]!.aborted).toBe(true))
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(dialogPosts(calls)).toHaveLength(0)
  })

  it("with the file explorer open the tool still works, and starting the dialog closes the explorer: the area holds one panel", async () => {
    await shell('tablet')
    fireEvent.pointerDown(screen.getByRole('button', { name: /^Create/ }))
    fireEvent.pointerUp(within(screen.getByRole('menu')).getByRole('menuitem', { name: /^Import/ }))
    await screen.findByRole('dialog', { name: 'Files' })
    tapCanvasInStrip()
    choose(png())
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    FakeXhr.all[0]!.reply(200, UPLOADED)
    await screen.findByRole('dialog', { name: 'Canvas 2' })
    expect(screen.queryByRole('dialog', { name: 'Files' })).toBeNull()
  })
})

describe('Desktop mode', () => {
  it('the row has Canvas right after Import STEP; a click picks, uploads and opens the same dialog', async () => {
    const { calls } = await shell('desktop')
    const row = [...document.querySelectorAll('.desktop-toolbar [data-tool]')].map((b) => b.getAttribute('data-tool'))
    expect(row[row.indexOf('import') + 1]).toBe('canvas')
    const button = document.querySelector('.desktop-toolbar [data-tool="canvas"]') as HTMLElement
    expect(button.getAttribute('data-state')).toBe('ok')
    expect(button.getAttribute('title')).toBe('Canvas')
    fireEvent.click(button)
    expect(picked).toHaveLength(1)
    choose(png('plan.png', 40))
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    expect(FakeXhr.all[0]!.url).toBe('/documents/d/tabs/T1/images?name=plan.png')
    FakeXhr.all[0]!.reply(200, { ...UPLOADED, filename: 'plan.png' })
    await screen.findByRole('dialog', { name: 'Canvas 2' })
    expect(dialogPosts(calls)[0]!.body).toEqual({ type: 'canvas', params: { blob: UPLOADED.blob, filename: 'plan.png' } })
  })
})

describe('a canvas in the view', () => {
  it('a canvas picked in the view selects its feature row, and a double click on it opens its dialog', async () => {
    const { calls } = await shell('desktop', 'bracket.canvas')
    const canvas = fixture('bracket.canvas').outputs.canvases[0]!
    const item = { id: `canvas|${canvas.id}`, kind: 'canvas' as const, part: null, index: -1, name: null, key: canvas.id }
    expect(document.querySelector(`.panel-row[data-feature="${canvas.id}"]`)?.getAttribute('aria-current')).toBeNull()
    act(() => window.__freeshape!.shell.selection.set([item]))
    await waitFor(() => expect(screen.getByTestId('feature-panel').getAttribute('data-selected-feature')).toBe(canvas.id))
    expect(screen.getByText('1 canvas')).toBeTruthy()
    act(() => window.__freeshape!.shell.selection.doubleClick(item))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith(`/features/${canvas.id}/dialog`))).toBe(true))
  })
})
