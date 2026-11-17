import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Home } from '../home/Home'
import { MODE_KEY } from '../shell/mode'
import type { Mode } from '../shell/mode'
import { Shell } from '../shell/Shell'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { FakeXhr, installFakeXhr } from '../test/fakeXhr'
import { fixture } from '../test/fixtures'
import { forgetArchiveProbe } from './filesApi'

// Card I11.10: a Document as one .fsdoc, from the home screen's row menu and from inside a Document, and Open file… / Open Document archive….
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

let saved: string[]
beforeEach(() => {
  window.localStorage.clear()
  forgetArchiveProbe()
  installFakeXhr()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  URL.createObjectURL = (b: Blob | MediaSource) => `blob:${(b as Blob).size}`
  URL.revokeObjectURL = () => {}
  saved = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(this.download)
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const NOW = Date.UTC(2026, 10, 16, 12, 0, 0)
const rows = [
  { id: 'a', name: 'Bracket', modified: NOW / 1000 - 3600, holder: null, open: false, missing: false },
  { id: 'b', name: 'Hinge', modified: NOW / 1000 - 86400, holder: holderOf('desktop'), open: false, missing: false },
  { id: 'c', name: 'Lost', modified: null, holder: null, open: false, missing: true },
]
const FILE = new Uint8Array([80, 75, 3, 4]).buffer
const disposition = (name: string) => ({ 'Content-Disposition': `attachment; filename="${name}"` })
const reply = (id: string, name: string) => ({ document: { id, name }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: null })
const archiveGets = (calls: Call[]) => calls.filter((c) => c.method === 'GET' && c.url.includes('/archive'))

/** The home screen over a server that holds a Documents root (the probe's empty POST is a 422) unless `root` is false. */
function home(answer: (c: Call) => { status?: number; json?: unknown; bytes?: ArrayBuffer; headers?: Record<string, string> } | undefined = () => undefined, root = true) {
  const calls = stubFetch((c) => {
    if (c.url === '/documents' && c.method === 'GET') return { json: { documents: rows } }
    if (c.url.startsWith('/documents/archive') && c.method === 'POST') return root ? { status: 422, json: { error: 'not a zip' } } : undefined
    return answer(c)
  })
  const { store } = makeStore()
  const onOpened = vi.fn()
  render(<Home store={store} onOpened={onOpened} now={() => NOW} />)
  return { calls, onOpened, store }
}
const rowOf = async (name: string) => (await screen.findByText(name)).closest('li')!
const menuOf = async (name: string) => {
  fireEvent.click(within(await rowOf(name)).getByRole('button', { name: 'Document menu' }))
}
const dialogs = () => screen.queryByRole('dialog')

it('the row menu button is on every Document that has a folder, and Download (.fsdoc) requests its archive', async () => {
  const { calls } = home((c) => (c.url === '/documents/a/archive' ? { bytes: FILE, headers: disposition('Bracket.fsdoc') } : undefined))
  expect(within(await rowOf('Lost')).queryByRole('button', { name: 'Document menu' })).toBeNull()
  await menuOf('Bracket')
  await act(async () => fireEvent.click(screen.getByRole('menuitem', { name: 'Download (.fsdoc)' })))
  await waitFor(() => expect(saved).toEqual(['Bracket.fsdoc']))
  expect(archiveGets(calls).map((c) => c.url)).toEqual(['/documents/a/archive'])
  expect(archiveGets(calls)[0]!.headers).toEqual({})
  expect(dialogs()).toBeNull()
})

it('the name comes from filename* when the server sends one', async () => {
  home((c) => (c.url === '/documents/a/archive' ? { bytes: FILE, headers: { 'Content-Disposition': `attachment; filename="Bracket.fsdoc"; filename*=UTF-8''Br%C3%A4cket.fsdoc` } } : undefined))
  await menuOf('Bracket')
  await act(async () => fireEvent.click(screen.getByRole('menuitem', { name: 'Download (.fsdoc)' })))
  await waitFor(() => expect(saved).toEqual(['Bräcket.fsdoc']))
})

it('a Document with unsaved edits asks Save and download?, and yes sends save=1 with the client id', async () => {
  const { calls } = home((c) => {
    if (c.url === '/documents/a/archive') return { status: 409, json: { error: 'unsaved', document: 'a' } }
    if (c.url === '/documents/a/archive?save=1') return { bytes: FILE, headers: disposition('Bracket.fsdoc') }
    return undefined
  })
  await menuOf('Bracket')
  await act(async () => fireEvent.click(screen.getByRole('menuitem', { name: 'Download (.fsdoc)' })))
  const ask = await screen.findByRole('dialog', { name: 'Save and download?' })
  expect(saved).toEqual([])
  await act(async () => fireEvent.click(within(ask).getByRole('button', { name: 'Save and download' })))
  await waitFor(() => expect(saved).toEqual(['Bracket.fsdoc']))
  expect(archiveGets(calls).map((c) => c.url)).toEqual(['/documents/a/archive', '/documents/a/archive?save=1'])
  expect(archiveGets(calls)[1]!.headers).toEqual({ 'X-Client-Id': 'abcdefgh' })
})

it('Cancel on the question downloads nothing and does not save', async () => {
  const { calls } = home((c) => (c.url === '/documents/a/archive' ? { status: 409, json: { error: 'unsaved', document: 'a' } } : undefined))
  await menuOf('Bracket')
  await act(async () => fireEvent.click(screen.getByRole('menuitem', { name: 'Download (.fsdoc)' })))
  fireEvent.click(within(await screen.findByRole('dialog', { name: 'Save and download?' })).getByRole('button', { name: 'Cancel' }))
  expect(dialogs()).toBeNull()
  expect(archiveGets(calls).length).toBe(1)
  expect(saved).toEqual([])
})

it('423 on the save names the holder; 422 gives the server\'s reason; 404 says the Document is gone', async () => {
  let n = 0
  home((c) => {
    if (c.url === '/documents/a/archive') return { status: 409, json: { error: 'unsaved', document: 'a' } }
    if (c.url === '/documents/a/archive?save=1') return { status: 423, json: { error: 'locked', document: 'a', holder: holderOf('desktop') } }
    if (c.url === '/documents/b/archive') return n++ === 0 ? { status: 422, json: { error: 'tab file name "a:b" has a colon' } } : { status: 404, json: { error: 'not found' } }
    return undefined
  })
  await menuOf('Bracket')
  await act(async () => fireEvent.click(screen.getByRole('menuitem', { name: 'Download (.fsdoc)' })))
  await act(async () => fireEvent.click(within(await screen.findByRole('dialog', { name: 'Save and download?' })).getByRole('button', { name: 'Save and download' })))
  let d = await screen.findByRole('dialog', { name: 'Could not download "Bracket"' })
  expect(d.textContent).toContain('is being edited in the desktop')
  expect(d.textContent).toContain('Nothing was downloaded')
  fireEvent.click(within(d).getByRole('button', { name: 'OK' }))

  await menuOf('Hinge')
  await act(async () => fireEvent.click(screen.getByRole('menuitem', { name: 'Download (.fsdoc)' })))
  d = await screen.findByRole('dialog', { name: 'Could not download "Hinge"' })
  expect(d.textContent).toContain('tab file name "a:b" has a colon')
  fireEvent.click(within(d).getByRole('button', { name: 'OK' }))

  await menuOf('Hinge')
  await act(async () => fireEvent.click(screen.getByRole('menuitem', { name: 'Download (.fsdoc)' })))
  expect((await screen.findByRole('dialog', { name: 'Could not download "Hinge"' })).textContent).toContain('not on the server any more')
  expect(saved).toEqual([])
})

it('a Document held by another program downloads the saved state with a note, and never sends save=1', async () => {
  const { calls } = home((c) => (c.url === '/documents/b/archive' ? { bytes: FILE, headers: disposition('Hinge.fsdoc') } : undefined))
  await menuOf('Hinge')
  await act(async () => fireEvent.click(screen.getByRole('menuitem', { name: 'Download (.fsdoc)' })))
  const note = await screen.findByRole('dialog', { name: 'Downloaded Hinge.fsdoc' })
  expect(note.textContent).toContain('open read-only here')
  expect(note.textContent).toContain('last saved')
  expect(saved).toEqual(['Hinge.fsdoc'])
  expect(archiveGets(calls).map((c) => c.url)).toEqual(['/documents/b/archive'])
})

const fsdoc = (name = 'Bracket copy.fsdoc', size = 40) => new File(['z'.repeat(size)], name)
const pickFile = async (file: File) => {
  const input = await screen.findByTestId('archive-input')
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  await act(async () => void fireEvent.change(input))
}

it('Open file… is in the header on a Documents root, takes .fsdoc, posts the file, and opens the new Document from the reply', async () => {
  const { calls, onOpened } = home((c) => (c.url === '/documents/n1/open' ? { json: reply('n1', 'Bracket') } : undefined))
  const button = await screen.findByRole('button', { name: 'Open file…' })
  expect((screen.getByTestId('archive-input') as HTMLInputElement).accept).toBe('.fsdoc')
  const spy = vi.spyOn(screen.getByTestId('archive-input'), 'click')
  fireEvent.click(button)
  expect(spy).toHaveBeenCalled()
  const file = fsdoc()
  await pickFile(file)
  const [x] = FakeXhr.all
  expect(x!.method).toBe('POST')
  expect(x!.url).toBe('/documents/archive?name=Bracket+copy.fsdoc')
  expect(x!.body).toBe(file)
  expect(x!.headers['Content-Type']).toBe('application/octet-stream')
  x!.progress(20, 40)
  expect(await screen.findByTestId('upload-progress')).toBeTruthy()
  expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('50')
  await act(async () => x!.reply(201, { id: 'n1', name: 'Bracket' }))
  await waitFor(() => expect(onOpened).toHaveBeenCalledWith(reply('n1', 'Bracket')))
  expect(calls.some((c) => c.method === 'POST' && c.url === '/documents/n1/open')).toBe(true)
  expect(screen.queryByTestId('upload-progress')).toBeNull()
})

it('a 422 shows the server\'s reason and opens nothing; Cancel stops the upload quietly', async () => {
  const { onOpened } = home()
  await screen.findByRole('button', { name: 'Open file…' })
  await pickFile(fsdoc('bad.fsdoc'))
  await act(async () => FakeXhr.all[0]!.reply(422, { error: 'not a zip file' }))
  const d = await screen.findByRole('dialog', { name: 'Could not open "bad.fsdoc"' })
  expect(d.textContent).toContain('not a zip file')
  fireEvent.click(within(d).getByRole('button', { name: 'OK' }))

  await pickFile(fsdoc('slow.fsdoc'))
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(screen.queryByTestId('upload-progress')).toBeNull())
  expect(FakeXhr.all[1]!.aborted).toBe(true)
  expect(dialogs()).toBeNull()
  expect(onOpened).not.toHaveBeenCalled()
})

it('413, 408 and 507 each say what happened, with the server\'s text where it has one', async () => {
  home()
  await screen.findByRole('button', { name: 'Open file…' })
  const answers: Array<[number, string]> = [
    [413, 'body over 268435456 bytes'],
    [408, 'request body too slow'],
    [507, 'no space left'],
  ]
  for (const [i, [status, text]] of answers.entries()) {
    await pickFile(fsdoc(`f${i}.fsdoc`))
    await act(async () => FakeXhr.all[i]!.reply(status, { error: text }))
    const d = await screen.findByRole('dialog', { name: `Could not open "f${i}.fsdoc"` })
    if (status === 408) expect(d.textContent).toContain('too slowly')
    else expect(d.textContent).toContain(text)
    fireEvent.click(within(d).getByRole('button', { name: 'OK' }))
  }
})

it('on a server that serves one Document there is no Open file… button, and an upload answered 404 hides it', async () => {
  home(() => undefined, false)
  await screen.findByRole('list', { name: 'Documents' })
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Open file…' })).toBeNull())
})

it('a probe that is not a 404 shows the button; a 404 on the upload itself then hides it and says why', async () => {
  home()
  await screen.findByRole('button', { name: 'Open file…' })
  await pickFile(fsdoc())
  await act(async () => FakeXhr.all[0]!.reply(404, { error: 'this server serves a single Document' }))
  expect((await screen.findByRole('dialog', { name: 'This server cannot open an archive' })).textContent).toContain('single Document')
  expect(screen.queryByRole('button', { name: 'Open file…' })).toBeNull()
})

// ---- inside a Document ----------------------------------------------------------------------------------------------------------

async function shell(mode: Mode, o: { readOnly?: boolean; dirty?: boolean } = {}, answer: (c: Call) => { status?: number; json?: unknown; bytes?: ArrayBuffer; headers?: Record<string, string> } | undefined = () => undefined) {
  window.localStorage.setItem(MODE_KEY, mode)
  const { bytes } = fixture('bracket')
  const onLeave = vi.fn()
  const calls = stubFetch((c: Call) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes } as never
    if (c.url.startsWith('/documents/archive') && c.method === 'POST') return { status: 422, json: { error: 'not a zip' } }
    return answer(c)
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={onLeave} />)
  const t = { ...tab('T1', 'studio', 'bracket'), dirty: o.dirty === true }
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [t], read_only: o.readOnly === true, holder: o.readOnly === true ? holderOf('desktop') : holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  return { calls, store, onLeave }
}
const docMenu = (name: string) => {
  fireEvent.click(screen.getByRole('button', { name: 'Document' }))
  fireEvent.click(screen.getByRole('menuitem', { name }))
}

it('Desktop: the Document menu has Open Document archive… after Open Document folder… and Save Document archive… after Save as…', async () => {
  const { store } = await shell('desktop')
  await waitFor(() => expect(screen.queryAllByRole('button', { name: 'Open file…' }).length).toBe(0))
  fireEvent.click(screen.getByRole('button', { name: 'Document' }))
  const names = screen.getAllByRole('menuitem').map((b) => b.querySelector('.menu-label')?.textContent)
  expect(names.indexOf('Open Document archive…')).toBe(names.indexOf('Open Document folder…') + 1)
  expect(names.indexOf('Save Document archive…')).toBe(names.indexOf('Save as…') + 1)
  await waitFor(() => expect((screen.getByRole('menuitem', { name: 'Open Document archive…' }) as HTMLButtonElement).disabled).toBe(false))
  store.stop()
})

it('Desktop: Save Document archive… downloads the Document, and asks first when it has unsaved edits', async () => {
  const { calls, store } = await shell('desktop', { dirty: true }, (c) => {
    if (c.url === '/documents/d/archive') return { status: 409, json: { error: 'unsaved', document: 'd' } }
    if (c.url === '/documents/d/archive?save=1') return { bytes: FILE, headers: disposition('Bracket.fsdoc') }
    return undefined
  })
  docMenu('Save Document archive…')
  const ask = await screen.findByRole('dialog', { name: 'Save and download?' })
  await act(async () => fireEvent.click(within(ask).getByRole('button', { name: 'Save and download' })))
  await waitFor(() => expect(saved).toEqual(['Bracket.fsdoc']))
  expect(archiveGets(calls).map((c) => c.url)).toEqual(['/documents/d/archive', '/documents/d/archive?save=1'])
  expect(archiveGets(calls)[1]!.headers).toEqual({ 'X-Client-Id': 'abcdefgh' })
  store.stop()
})

it('Desktop: a read-only Document is downloaded as saved, with a note', async () => {
  const { calls, store } = await shell('desktop', { readOnly: true }, (c) => (c.url === '/documents/d/archive' ? { bytes: FILE, headers: disposition('Bracket.fsdoc') } : undefined))
  docMenu('Save Document archive…')
  expect((await screen.findByRole('dialog', { name: 'Downloaded Bracket.fsdoc' })).textContent).toContain('read-only')
  expect(archiveGets(calls).map((c) => c.url)).toEqual(['/documents/d/archive'])
  store.stop()
})

it('Desktop: Open Document archive… uploads the file and leaves this Document for the new one', async () => {
  const { calls, store, onLeave } = await shell('desktop', {}, (c) => (c.url === '/documents/d/close' ? { json: { closed: true } } : undefined))
  await waitFor(() => expect((screen.getByTestId('archive-input') as HTMLInputElement).isConnected).toBe(true))
  await waitFor(() => expect(calls.some((c) => c.url.startsWith('/documents/archive'))).toBe(true))
  await waitFor(() => docMenu('Open Document archive…'))
  await pickFile(fsdoc())
  await act(async () => FakeXhr.all[0]!.reply(201, { id: 'n1', name: 'Bracket' }))
  await waitFor(() => expect(onLeave).toHaveBeenCalledWith({ id: 'n1', name: 'Bracket' }))
  expect(calls.some((c) => c.method === 'POST' && c.url === '/documents/d/close')).toBe(true)
  store.stop()
})

it('Desktop: with unsaved changes the upload is followed by the Save / Discard / Stay question and nothing is lost', async () => {
  const { calls, store, onLeave } = await shell('desktop', { dirty: true })
  await waitFor(() => expect(calls.some((c) => c.url.startsWith('/documents/archive'))).toBe(true))
  await waitFor(() => docMenu('Open Document archive…'))
  await pickFile(fsdoc())
  await act(async () => FakeXhr.all[0]!.reply(201, { id: 'n1', name: 'Bracket' }))
  const ask = await screen.findByRole('dialog', { name: 'Save changes to "Bracket"?' })
  expect(onLeave).not.toHaveBeenCalled()
  fireEvent.click(within(ask).getByRole('button', { name: 'Stay' }))
  expect(onLeave).not.toHaveBeenCalled()
  store.stop()
})

it('Desktop: the palette lists both archive commands', async () => {
  const { store } = await shell('desktop')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Document' })).toBeTruthy())
  act(() => void window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', code: 'Space', ctrlKey: true, bubbles: true, cancelable: true })))
  const box = await screen.findByRole('combobox')
  fireEvent.change(box, { target: { value: 'archive' } })
  const options = screen.getAllByRole('option').map((o) => o.textContent ?? '')
  expect(options.some((t) => t.startsWith('Save Document archive'))).toBe(true)
  expect(options.some((t) => t.startsWith('Open Document archive'))).toBe(true)
  store.stop()
})

it('Tablet: Export is a menu with Export STEP / STL… and Download Document; Download Document works while no tab can be exported', async () => {
  const { calls, store } = await shell('tablet', {}, (c) => (c.url === '/documents/d/archive' ? { bytes: FILE, headers: disposition('Bracket.fsdoc') } : undefined))
  expect(calls.some((c) => c.url.startsWith('/documents/archive'))).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Export' }))
  const menu = screen.getByRole('menu', { name: 'Export menu' })
  expect(within(menu).getAllByRole('menuitem').map((b) => b.textContent)).toEqual(['Export STEP / STL…', 'Download Document'])
  await act(async () => fireEvent.click(within(menu).getByRole('menuitem', { name: 'Download Document' })))
  await waitFor(() => expect(saved).toEqual(['Bracket.fsdoc']))
  expect(archiveGets(calls).map((c) => c.url)).toEqual(['/documents/d/archive'])
  fireEvent.click(screen.getByRole('button', { name: 'Export' }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Export STEP / STL…' }))
  expect(screen.getByRole('dialog', { name: 'Export' })).toBeTruthy()
  store.stop()
})

it('a Document to open that arrives after the home screen is up is opened once', async () => {
  const calls = stubFetch((c) => (c.url === '/documents' ? { json: { documents: rows } } : c.url === '/documents/n1/open' ? { json: reply('n1', 'Bracket') } : undefined))
  const { store } = makeStore()
  const onOpened = vi.fn()
  const view = render(<Home store={store} onOpened={onOpened} now={() => NOW} />)
  await screen.findByRole('list', { name: 'Documents' })
  expect(calls.some((c) => c.url === '/documents/n1/open')).toBe(false)
  const next = { id: 'n1', name: 'Bracket' }
  view.rerender(<Home store={store} openNow={next} onOpened={onOpened} now={() => NOW} />)
  await waitFor(() => expect(onOpened).toHaveBeenCalledTimes(1))
  view.rerender(<Home store={store} openNow={next} onOpened={onOpened} now={() => NOW + 1} />)
  expect(calls.filter((c) => c.url === '/documents/n1/open').length).toBe(1)
})
