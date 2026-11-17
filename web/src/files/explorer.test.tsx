import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Mock } from 'vitest'
import type { DocumentRow } from '../net/api'
import { Home } from '../home/Home'
import { MODE_KEY } from '../shell/mode'
import type { Mode } from '../shell/mode'
import { Shell } from '../shell/Shell'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { keyOf } from '../test/keys'
import { Explorer } from './Explorer'
import type { ExplorerMode, ImportTarget } from './Explorer'
import type { FileEntry, FolderReply, ImportReply } from './filesApi'

// The file explorer (card I11.7): alone, in the Shell in both modes, and on the home screen. The replies are shaped as
// tests/test_serve_files.py and tests/test_serve_import.py have the server give them.
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
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const entry = (id: string, file: string, path: string, extra: Partial<FileEntry> = {}): FileEntry => ({
  id,
  name: file.replace(/\.[^.]+$/, ''),
  file,
  path,
  size: 5657,
  description: '',
  tags: [],
  licence: null,
  authors: null,
  url: null,
  ...extra,
})
const BEARING = entry('b0b0b0b0b0b0b0b0', 'skate-608.step', 'bearings/skate-608.step', {
  name: 'Skate Bearing 608',
  licence: 'CC-BY-4.0',
  authors: 'Ann Author, Bo Writer',
  url: 'https://example.org/skate-608',
  description: 'A 608 bearing',
})
const NUT = entry('a1a1a1a1a1a1a1a1', 'm3-nut-block.step', 'fasteners/m3-nut-block.step', { licence: 'CC0-1.0', size: 15416 })
const page = (o: Partial<FolderReply>): FolderReply => ({ root: 'lib', dir: '', q: '', dirs: [], entries: [], total: 0, offset: 0, limit: 200, ...o })
const ROOTS = { roots: [{ name: 'lib', files: 2, max_upload: 67108864 }, { name: 'parts', files: 1, max_upload: 67108864 }], export_roots: [] }
const IMPORTED = (tabId = 'T1', tabs = [tab('T1', 'studio', 'Part Studio 1')]) => ({
  document: 'd',
  tab: tabs.find((t) => t.id === tabId),
  feature: { id: 'F9', name: 'Import skate-608.step', error: null },
  parts: [{ id: 'P9', name: 'skate-608' }],
  rebuilt: [],
  tabs,
})

/** Answers the file calls the way a server with roots lib and parts does; `more` answers anything else. */
function server(more: (c: Call) => { status?: number; json?: unknown } | undefined = () => undefined) {
  return stubFetch((c) => {
    if (c.url === '/files') return { json: ROOTS }
    if (c.url.startsWith('/files/lib')) {
      const q = new URL(c.url, 'http://x').searchParams
      if (q.get('q') === 'bear') return { json: page({ q: 'bear', entries: [BEARING], total: 1 }) }
      if (q.get('dir') === 'bearings') return { json: page({ dir: 'bearings', entries: [BEARING], total: 1 }) }
      return {
        json: page({
          dirs: [
            { name: 'bearings', path: 'bearings', files: 1 },
            { name: 'fasteners', path: 'fasteners', files: 1 },
          ],
          entries: [NUT],
          total: 1,
        }),
      }
    }
    if (c.url === '/documents') return { json: { documents: [{ id: 'a', name: 'Bracket', modified: 1, holder: null, open: false, missing: false }] } }
    return more(c)
  })
}

const target = (o: Partial<ImportTarget> = {}): ImportTarget => ({ document: 'd', tab: 'T1', studio: true, readOnly: false, ...o })
function explorer(uiMode: Mode, o: { mode?: ExplorerMode; target?: ImportTarget | null; onImported?: Mock<(reply: ImportReply, newTab: boolean) => void>; onOpenDocument?: Mock<(row: DocumentRow) => void> } = {}) {
  const onClose = vi.fn()
  const onOpenDocument = o.onOpenDocument ?? vi.fn<(row: DocumentRow) => void>()
  const onImported = o.onImported ?? vi.fn<(reply: ImportReply, newTab: boolean) => void>()
  render(
    <Explorer
      mode={o.mode ?? 'add'}
      placement={uiMode === 'desktop' ? 'float' : 'column'}
      uiMode={uiMode}
      target={o.target === undefined ? target() : o.target}
      onOpenDocument={onOpenDocument}
      onImported={onImported}
      onClose={onClose}
    />,
  )
  return { onClose, onOpenDocument, onImported }
}
const getsOf = (calls: Call[], prefix: string) => calls.filter((c) => c.method === 'GET' && c.url.startsWith(prefix)).map((c) => c.url)
const rootNames = () => within(screen.getByRole('navigation', { name: 'Roots' })).getAllByRole('button').map((b) => b.querySelector('.ex-root-name')!.textContent)
const rowNames = () => within(screen.getByRole('list', { name: 'Files' })).getAllByRole('listitem').map((i) => i.querySelector('.ex-name')!.textContent)

describe('the roots, folders, search and paging', () => {
  it('lists Documents and then the roots of GET /files, and starts in the first file root with 200 entries a page', async () => {
    const calls = server()
    explorer('desktop')
    await screen.findByRole('list', { name: 'Files' })
    expect(rootNames()).toEqual(['Documents', 'lib', 'parts'])
    expect(getsOf(calls, '/files')).toEqual(['/files', '/files/lib?limit=200'])
    // folders come first, then the entries, each with its size and its licence as the manifest wrote it
    expect(rowNames()).toEqual(['bearings', 'fasteners', 'm3-nut-block'])
    const nut = screen.getByRole('button', { name: /m3-nut-block/ })
    expect(nut.textContent).toContain('15 kB')
    expect(nut.textContent).toContain('CC0-1.0')
    expect(nut.textContent).toContain('fasteners')
  })

  it('opening a folder fetches dir, the breadcrumb goes back, and a root lists its own top', async () => {
    const calls = server()
    explorer('desktop')
    fireEvent.click(await screen.findByRole('button', { name: /^bearings/ }))
    await screen.findByRole('button', { name: /Skate Bearing 608/ })
    expect(getsOf(calls, '/files/lib')).toEqual(['/files/lib?limit=200', '/files/lib?dir=bearings&limit=200'])
    const crumbs = within(screen.getByRole('navigation', { name: 'Folder' }))
    expect(crumbs.getAllByRole('button').map((b) => b.textContent)).toEqual(['lib', 'bearings'])
    fireEvent.click(crumbs.getByRole('button', { name: 'lib' }))
    await waitFor(() => expect(rowNames()).toEqual(['bearings', 'fasteners', 'm3-nut-block']))
    fireEvent.click(screen.getByRole('button', { name: /^parts/ }))
    await waitFor(() => expect(getsOf(calls, '/files/parts')).toEqual(['/files/parts?limit=200']))
  })

  it('typing in the search box fetches q (Desktop mode), and clearing it lists the folder again', async () => {
    const calls = server()
    explorer('desktop')
    await screen.findByRole('list', { name: 'Files' })
    fireEvent.change(screen.getByRole('textbox', { name: 'Search' }), { target: { value: 'bear' } })
    await screen.findByText('1 match')
    expect(getsOf(calls, '/files/lib').at(-1)).toBe('/files/lib?q=bear&limit=200')
    expect(rowNames()).toEqual(['Skate Bearing 608'])
    fireEvent.click(screen.getByRole('button', { name: 'Clear the search' }))
    await waitFor(() => expect(rowNames()).toContain('fasteners'))
  })

  it('Tablet mode asks for the search in a prompt, as a tab name is asked, then fetches q', async () => {
    const calls = server()
    explorer('tablet')
    await screen.findByRole('list', { name: 'Files' })
    expect(screen.queryByRole('textbox', { name: 'Search' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    const prompt = screen.getByRole('dialog', { name: 'Search' })
    fireEvent.change(within(prompt).getByRole('textbox'), { target: { value: 'bear' } })
    fireEvent.click(within(prompt).getByRole('button', { name: 'Search' }))
    await screen.findByText('1 match')
    expect(getsOf(calls, '/files/lib').at(-1)).toBe('/files/lib?q=bear&limit=200')
    expect(screen.queryByRole('dialog', { name: 'Search' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Search, bear' })).toBeTruthy()
  })

  it('scrolling to the end fetches the next page from where the loaded entries stop, and not twice', async () => {
    const many = Array.from({ length: 200 }, (_, i) => entry(`e${i}`, `p${i}.step`, `p${i}.step`))
    const rest = Array.from({ length: 30 }, (_, i) => entry(`f${i}`, `q${i}.step`, `q${i}.step`))
    const calls = stubFetch((c) => {
      if (c.url === '/files') return { json: ROOTS }
      if (c.url === '/files/lib?limit=200') return { json: page({ entries: many, total: 230 }) }
      if (c.url === '/files/lib?offset=200&limit=200') return { json: page({ entries: rest, total: 230, offset: 200 }) }
      return undefined
    })
    explorer('desktop')
    await screen.findByRole('list', { name: 'Files' })
    const list = screen.getByTestId('ex-list')
    // not at the end yet: nothing is asked for
    Object.defineProperties(list, { scrollHeight: { value: 8000, configurable: true }, clientHeight: { value: 400, configurable: true } })
    list.scrollTop = 100
    fireEvent.scroll(list)
    expect(getsOf(calls, '/files/lib')).toEqual(['/files/lib?limit=200'])
    list.scrollTop = 7600
    fireEvent.scroll(list)
    fireEvent.scroll(list)
    await waitFor(() => expect(rowNames().length).toBe(230))
    expect(getsOf(calls, '/files/lib')).toEqual(['/files/lib?limit=200', '/files/lib?offset=200&limit=200'])
    // every entry is loaded: the end of the list asks for nothing more
    fireEvent.scroll(list)
    expect(getsOf(calls, '/files/lib').length).toBe(2)
  })

  it('shows the licence, authors, source URL and description of the chosen entry, in full', async () => {
    server()
    explorer('desktop')
    fireEvent.click(await screen.findByRole('button', { name: /^bearings/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Skate Bearing 608/ }))
    const detail = within(screen.getByTestId('ex-detail'))
    expect(detail.getByText(/CC-BY-4.0/)).toBeTruthy()
    expect(detail.getByText('By Ann Author, Bo Writer')).toBeTruthy()
    expect(detail.getByText('A 608 bearing')).toBeTruthy()
    expect(detail.getByRole('link', { name: 'https://example.org/skate-608' }).getAttribute('href')).toBe('https://example.org/skate-608')
  })

  it('says so when the server cannot list the roots, and Documents still opens', async () => {
    stubFetch((c) => (c.url === '/documents' ? { json: { documents: [] } } : { status: 500, json: { error: 'no files' } }))
    explorer('desktop')
    expect((await screen.findAllByRole('alert'))[0]!.textContent).toContain('no files')
    expect(rootNames()).toEqual(['Documents'])
  })

  it('the Documents root lists the Documents and a tap on one is handed on', async () => {
    server()
    const { onOpenDocument } = explorer('desktop')
    fireEvent.click(await screen.findByRole('button', { name: /^Documents/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Bracket/ }))
    expect(onOpenDocument).toHaveBeenCalledOnce()
    expect(vi.mocked(onOpenDocument).mock.calls[0]![0]).toMatchObject({ id: 'a', name: 'Bracket' })
  })
})

describe('importing an entry', () => {
  const chooseNut = async () => {
    fireEvent.click(await screen.findByRole('button', { name: /m3-nut-block/ }))
  }

  it('Add to this Part Studio posts {root, entry} to the current tab and says Imported <name>', async () => {
    const calls = server((c) => (c.method === 'POST' && c.url === '/documents/d/tabs/T1/import' ? { json: IMPORTED() } : undefined))
    const { onImported } = explorer('desktop')
    await chooseNut()
    fireEvent.click(screen.getByRole('button', { name: 'Add to this Part Studio' }))
    expect((await screen.findByTestId('ex-status')).textContent).toBe('Imported m3-nut-block')
    const post = calls.find((c) => c.method === 'POST')!
    expect(post.url).toBe('/documents/d/tabs/T1/import')
    expect(post.body).toEqual({ root: 'lib', entry: NUT.id })
    expect(onImported).toHaveBeenCalledWith(IMPORTED(), false)
  })

  it('Open in a new Part Studio adds new_tab=1 and hands the new tab on', async () => {
    const tabs = [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'studio', 'm3-nut-block')]
    const calls = server((c) => (c.method === 'POST' && c.url === '/documents/d/tabs/T1/import?new_tab=1' ? { json: IMPORTED('T2', tabs) } : undefined))
    const { onImported } = explorer('tablet', { mode: 'new' })
    await chooseNut()
    fireEvent.click(screen.getByRole('button', { name: 'Open in a new Part Studio' }))
    await screen.findByText('Imported m3-nut-block')
    expect(calls.find((c) => c.method === 'POST')!.body).toEqual({ root: 'lib', entry: NUT.id })
    expect(onImported).toHaveBeenCalledWith(IMPORTED('T2', tabs), true)
  })

  it("says the server's error as it gave it, and an import feature that did not build", async () => {
    let fails = true
    server((c) =>
      c.method === 'POST'
        ? fails
          ? { status: 409, json: { error: 'the file changed on disk: rescan the root' } }
          : { json: { ...IMPORTED(), feature: { id: 'F9', name: 'Import x', error: 'no solid in the file' } } }
        : undefined,
    )
    explorer('desktop')
    await chooseNut()
    fireEvent.click(screen.getByRole('button', { name: 'Add to this Part Studio' }))
    expect((await screen.findByRole('alert', { name: '' })).textContent).toBe('the file changed on disk: rescan the root')
    fails = false
    fireEvent.click(screen.getByRole('button', { name: 'Add to this Part Studio' }))
    await waitFor(() => expect(screen.getByTestId('ex-status').textContent).toBe('m3-nut-block: no solid in the file'))
  })

  it('offers no import where there is nothing to import into, and says why', async () => {
    server()
    explorer('desktop', { target: null })
    await chooseNut()
    expect((screen.getByRole('button', { name: 'Add to this Part Studio' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Open in a new Part Studio' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('Open a Document to add parts to it.')).toBeTruthy()
  })

  it('on an Assembly tab only the new Part Studio is on, and a read-only Document has neither', async () => {
    server()
    explorer('desktop', { target: target({ studio: false }) })
    await chooseNut()
    expect((screen.getByRole('button', { name: 'Add to this Part Studio' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Open in a new Part Studio' }) as HTMLButtonElement).disabled).toBe(false)
    cleanup()
    server()
    explorer('desktop', { target: target({ readOnly: true }) })
    await chooseNut()
    expect((screen.getByRole('button', { name: 'Open in a new Part Studio' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('the mode of the opening picks the main button', async () => {
    server()
    explorer('desktop', { mode: 'new' })
    await chooseNut()
    expect(screen.getByRole('button', { name: 'Open in a new Part Studio' }).getAttribute('data-kind')).toBe('primary')
    expect(screen.getByRole('button', { name: 'Add to this Part Studio' }).getAttribute('data-kind')).toBe('plain')
  })
})

const key = (chord: string) => act(() => void window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...keyOf(chord) })))
const explorerNow = () => screen.queryByTestId('explorer')

async function shell(mode: Mode, more: (c: Call) => { status?: number; json?: unknown } | undefined = () => undefined, onLeave = vi.fn()) {
  window.localStorage.setItem(MODE_KEY, mode)
  const { bytes } = fixture('bracket')
  const calls = server((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes } as never
    if (c.url.includes('/features/types')) return { json: { document: 'd', tab: 'T1', revision: 1, types: [] } }
    return more(c)
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={onLeave} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  return { store, sock, calls, onLeave }
}

describe('in the Shell', () => {
  it('Desktop mode: Ctrl+Shift+L and the three Document menu items open it, each in its own mode, floating over the view', async () => {
    await shell('desktop')
    key('Ctrl+Shift+L')
    await screen.findByTestId('explorer')
    expect(explorerNow()!.getAttribute('data-open-mode')).toBe('browse')
    expect(explorerNow()!.getAttribute('data-placement')).toBe('float')
    expect(screen.getByRole('dialog', { name: 'Files' }).classList.contains('dialog-floating')).toBe(true)
    // Escape closes it
    key('Escape')
    expect(explorerNow()).toBeNull()
    for (const [label, want] of [
      ['Import STEP / BREP (new tab)…', 'new'],
      ['Import STEP / BREP into this tab…', 'add'],
      ['Component library…', 'browse'],
    ] as const) {
      fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: 'Document' }))
      const item = screen.getByRole('menuitem', { name: (n) => n.startsWith(label) }) as HTMLButtonElement
      expect(item.disabled, label).toBe(false)
      fireEvent.click(item)
      await screen.findByTestId('explorer')
      expect(explorerNow()!.getAttribute('data-open-mode'), label).toBe(want)
      fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }))
      expect(explorerNow()).toBeNull()
    }
  })

  it("Desktop mode: the toolbar's Import opens it in new-tab mode, and the search palette lists the three commands as runnable", async () => {
    await shell('desktop')
    await waitFor(() => expect((document.querySelector('.desktop-toolbar [data-tool="import"]') as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(document.querySelector('.desktop-toolbar [data-tool="import"]')!)
    await screen.findByTestId('explorer')
    expect(explorerNow()!.getAttribute('data-open-mode')).toBe('new')
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }))
    key('Alt+C')
    const box = await screen.findByRole('combobox', { name: 'Search tools' })
    fireEvent.change(box, { target: { value: 'library' } })
    const option = screen.getByRole('option', { name: /Component library/ })
    expect(option.getAttribute('title')).not.toBe('Desktop only for now')
    expect(option.getAttribute('aria-disabled')).not.toBe('true')
  })

  it("Tablet mode: the strip's Create group has Import, which opens it over the left column in add mode", async () => {
    await shell('tablet')
    expect(explorerNow()).toBeNull()
    const create = screen.getByRole('button', { name: /^Create/ })
    fireEvent.pointerDown(create)
    fireEvent.pointerUp(create)
    fireEvent.click(within(screen.getByRole('menu', { name: 'Create' })).getByRole('menuitem', { name: /^Import/ }), { detail: 0 })
    await screen.findByTestId('explorer')
    expect(explorerNow()!.getAttribute('data-open-mode')).toBe('add')
    expect(explorerNow()!.getAttribute('data-placement')).toBe('column')
    expect(screen.getByTestId('left-column').contains(explorerNow())).toBe(true)
    // no keyboard key opens it in Tablet mode
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }))
    key('Ctrl+Shift+L')
    expect(explorerNow()).toBeNull()
  })

  it('adds to the Part Studio on screen, and a new Part Studio takes the new tab', async () => {
    const tabs = [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'studio', 'm3-nut-block')]
    const { calls } = await shell('tablet', (c) =>
      c.method === 'POST' && c.url === '/documents/d/tabs/T1/import' ? { json: IMPORTED() } : c.method === 'POST' && c.url === '/documents/d/tabs/T1/import?new_tab=1' ? { json: IMPORTED('T2', tabs) } : undefined,
    )
    const create = screen.getByRole('button', { name: /^Create/ })
    fireEvent.pointerDown(create)
    fireEvent.pointerUp(create)
    fireEvent.click(within(screen.getByRole('menu', { name: 'Create' })).getByRole('menuitem', { name: /^Import/ }), { detail: 0 })
    fireEvent.click(await screen.findByRole('button', { name: /m3-nut-block/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Add to this Part Studio' }))
    await screen.findByText('Imported m3-nut-block')
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.url)).toEqual(['/documents/d/tabs/T1/import'])
    fireEvent.click(screen.getByRole('button', { name: 'Open in a new Part Studio' }))
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST').length).toBe(2))
    await waitFor(() => expect(document.querySelector('.top-bar-tabs [aria-current="page"]')?.textContent).toBe('m3-nut-block'))
  })

  it('the Documents root leaves this Document for the one chosen', async () => {
    const onLeave = vi.fn()
    await shell('desktop', () => undefined, onLeave)
    key('Ctrl+Shift+L')
    fireEvent.click(await screen.findByRole('button', { name: /^Documents/ }))
    fireEvent.click(await within(screen.getByTestId('explorer')).findByRole('button', { name: /Bracket/ }))
    await waitFor(() => expect(onLeave).toHaveBeenCalledWith({ id: 'a', name: 'Bracket' }))
    expect(explorerNow()).toBeNull()
  })
})

describe('on the home screen', () => {
  const rows = [
    { id: 'a', name: 'Bracket', modified: 1, holder: null, open: false, missing: false },
    { id: 'b', name: 'Hinge', modified: 2, holder: null, open: false, missing: false },
  ]
  const reply = (id: string, name: string) => ({ document: { id, name }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: null })
  const home = (props: { openNow?: { id: string; name: string } } = {}) => {
    const calls = stubFetch((c) => {
      if (c.url === '/files') return { json: ROOTS }
      if (c.url.startsWith('/files/lib')) return { json: page({ entries: [NUT], total: 1 }) }
      if (c.url === '/documents') return { json: { documents: rows } }
      if (c.url === '/documents/b/open') return { json: reply('b', 'Hinge') }
      return undefined
    })
    const { store } = makeStore()
    const onOpened = vi.fn()
    render(<Home store={store} onOpened={onOpened} {...props} />)
    return { calls, onOpened }
  }

  it("the Files button opens the explorer in browse mode with no import to offer, and a Document in its Documents root opens as the home screen's list does", async () => {
    const { calls, onOpened } = home()
    await screen.findByRole('list', { name: 'Documents' })
    fireEvent.click(screen.getByRole('button', { name: 'Files' }))
    await screen.findByTestId('explorer')
    expect(explorerNow()!.getAttribute('data-open-mode')).toBe('browse')
    fireEvent.click(await screen.findByRole('button', { name: /m3-nut-block/ }))
    expect((screen.getByRole('button', { name: 'Add to this Part Studio' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Roots' })).getByRole('button', { name: /^Documents/ }))
    const inExplorer = within(screen.getByTestId('explorer'))
    fireEvent.click(await inExplorer.findByRole('button', { name: /Hinge/ }))
    await waitFor(() => expect(onOpened).toHaveBeenCalledWith(reply('b', 'Hinge')))
    expect(calls.some((c) => c.method === 'POST' && c.url === '/documents/b/open')).toBe(true)
  })

  it('opens the Document it was told to as soon as it is up', async () => {
    const { onOpened } = home({ openNow: { id: 'b', name: 'Hinge' } })
    await waitFor(() => expect(onOpened).toHaveBeenCalledWith(reply('b', 'Hinge')))
    expect(onOpened).toHaveBeenCalledOnce()
  })
})
