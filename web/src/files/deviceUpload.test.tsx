import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MODE_KEY } from '../shell/mode'
import type { Mode } from '../shell/mode'
import { Shell } from '../shell/Shell'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { FakeXhr, installFakeXhr } from '../test/fakeXhr'
import { fixture } from '../test/fixtures'
import { keyOf } from '../test/keys'
import { installDropGuard, planDrop } from '../viewport/fileDrop'
import { Explorer } from './Explorer'
import type { ImportReply } from './filesApi'
import { uploadLimitOf } from './filesApi'

// Card I11.8: From this device… in the explorer, and a file dropped on the view. The upload is a fake XMLHttpRequest the test drives.
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
  installFakeXhr()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const IMPORTED = (tabId = 'T1', tabs = [tab('T1', 'studio', 'Part Studio 1')]) => ({
  document: 'd',
  tab: tabs.find((t) => t.id === tabId),
  feature: { id: 'F9', name: 'Import box.step', error: null },
  parts: [{ id: 'P9', name: 'box' }],
  rebuilt: [],
  tabs,
})
const step = (name = 'box.step', size = 13) => new File(['x'.repeat(size)], name)
const ROOTS = { roots: [{ name: 'lib', files: 1, max_upload: 1000 }], export_roots: [] }
const key = (chord: string) => act(() => void window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...keyOf(chord) })))

async function shell(mode: Mode, roots = ROOTS) {
  window.localStorage.setItem(MODE_KEY, mode)
  const { bytes } = fixture('bracket')
  const calls = stubFetch((c: Call) => {
    if (c.url === '/files') return { json: roots }
    if (c.url.startsWith('/files/lib')) return { json: { root: 'lib', dir: '', q: '', dirs: [], entries: [], total: 0, offset: 0, limit: 200 } }
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes } as never
    if (c.url.includes('/features/types')) return { json: { document: 'd', tab: 'T1', revision: 1, types: [] } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={vi.fn()} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  return { store, sock, calls }
}

const input = () => screen.getByTestId('ex-device-input') as HTMLInputElement
const choose = (f: File) => fireEvent.change(input(), { target: { files: [f] } })
const drop = (target: Element, files: File[]) => fireEvent.drop(target, { dataTransfer: { types: ['Files'], files } })

describe('From this device…', () => {
  it('imports into the current Part Studio: octet-stream, the name in ?name=, a progress bar, then the status', async () => {
    const onImported = vi.fn<(r: ImportReply, n: boolean) => void>()
    stubFetch((c) => (c.url === '/files' ? { json: ROOTS } : undefined))
    render(<Explorer mode="add" placement="float" uiMode="desktop" target={{ document: 'd', tab: 'T1', studio: true, readOnly: false }} onOpenDocument={vi.fn()} onImported={onImported} onClose={vi.fn()} />)
    expect(input().accept).toBe('.step,.stp,.brep,.brp')
    fireEvent.click(screen.getByRole('button', { name: 'From this device…' }))
    choose(step('box.step', 200))
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    const x = FakeXhr.all[0]!
    expect(x.url).toBe('/documents/d/tabs/T1/import?name=box.step')
    expect(x.headers['Content-Type']).toBe('application/octet-stream')
    x.progress(50, 200)
    expect((await screen.findByRole('progressbar')).getAttribute('aria-valuenow')).toBe('25')
    expect(screen.getByTestId('upload-progress').textContent).toContain('box.step')
    x.reply(200, IMPORTED())
    await screen.findByText('Imported box.step')
    expect(onImported).toHaveBeenCalledWith(expect.objectContaining({ document: 'd' }), false)
    expect(screen.queryByRole('progressbar')).toBeNull()
  })

  it('the second button and the new-tab mode send new_tab=1', async () => {
    stubFetch(() => undefined)
    const onImported = vi.fn<(r: ImportReply, n: boolean) => void>()
    render(<Explorer mode="add" placement="float" uiMode="desktop" target={{ document: 'd', tab: 'T1', studio: true, readOnly: false }} onOpenDocument={vi.fn()} onImported={onImported} onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'From this device into a new Part Studio…' }))
    choose(step())
    await waitFor(() => expect(FakeXhr.all[0]?.url).toBe('/documents/d/tabs/T1/import?name=box.step&new_tab=1'))
    FakeXhr.all[0]!.reply(200, IMPORTED())
    await waitFor(() => expect(onImported).toHaveBeenCalledWith(expect.anything(), true))
  })

  it('Cancel stops the upload and says so; nothing is imported', async () => {
    stubFetch(() => undefined)
    const onImported = vi.fn()
    render(<Explorer mode="add" placement="float" uiMode="tablet" target={{ document: 'd', tab: 'T1', studio: true, readOnly: false }} onOpenDocument={vi.fn()} onImported={onImported} onClose={vi.fn()} />)
    choose(step())
    await screen.findByRole('progressbar')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await screen.findByText('Cancelled box.step.')
    expect(FakeXhr.all[0]!.aborted).toBe(true)
    expect(onImported).not.toHaveBeenCalled()
  })

  it("refuses a file over the server's max_upload before sending, and shows the server's text for a refusal", async () => {
    stubFetch((c) => (c.url === '/files' ? { json: ROOTS } : undefined))
    render(<Explorer mode="add" placement="float" uiMode="desktop" target={{ document: 'd', tab: 'T1', studio: true, readOnly: false }} onOpenDocument={vi.fn()} onImported={vi.fn()} onClose={vi.fn()} />)
    await within(screen.getByRole('navigation', { name: 'Roots' })).findByRole('button', { name: /^lib/ })
    choose(step('big.step', 1001))
    await screen.findByText(/big.step is .* MB; the server takes files up to/)
    expect(FakeXhr.all.length).toBe(0)
    choose(step('pic.step', 10))
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    FakeXhr.all[0]!.reply(415, { error: 'the body is not a STEP (ISO-10303-21;) or BREP (DBRep_DrawableShape) file' })
    await screen.findByText(/the body is not a STEP/)
  })

  it('the limit is the smallest max_upload the roots list, and none when there are no roots', () => {
    expect(uploadLimitOf([{ name: 'a', files: 1, max_upload: 9 }, { name: 'b', files: 1, max_upload: 5 }])).toBe(5)
    expect(uploadLimitOf([])).toBeNull()
  })

  it('is not offered on the home screen (no target), and is disabled in a read-only Document', () => {
    stubFetch(() => undefined)
    const { unmount } = render(<Explorer mode="browse" placement="page" uiMode="desktop" target={null} onOpenDocument={vi.fn()} onImported={vi.fn()} onClose={vi.fn()} />)
    expect(screen.queryByTestId('ex-device')).toBeNull()
    unmount()
    render(<Explorer mode="add" placement="float" uiMode="desktop" target={{ document: 'd', tab: 'T1', studio: true, readOnly: true }} onOpenDocument={vi.fn()} onImported={vi.fn()} onClose={vi.fn()} />)
    expect((screen.getByRole('button', { name: 'From this device…' }) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('a file dropped on the page', () => {
  it('Desktop mode: a .step on the view imports into a new tab, with the progress over the view', async () => {
    const tabs = [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'studio', 'box')]
    await shell('desktop')
    drop(screen.getByTestId('shell-viewport'), [step('box.step')])
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    const x = FakeXhr.all[0]!
    expect(x.url).toBe('/documents/d/tabs/T1/import?name=box.step&new_tab=1')
    x.progress(5, 13)
    expect(within(await screen.findByTestId('drop-upload')).getByRole('progressbar').getAttribute('aria-valuenow')).toBe('38')
    x.reply(200, IMPORTED('T2', tabs))
    await waitFor(() => expect(document.querySelector('nav[aria-label="Tabs"] .tab-button[aria-current="page"]')?.textContent).toBe('box'))
    expect(screen.queryByTestId('drop-upload')).toBeNull()
    await screen.findByText('Imported box.step')
  })

  it('Desktop mode: a .png does nothing but say why, and a .step beside it still imports', async () => {
    await shell('desktop')
    drop(screen.getByTestId('shell-viewport'), [step('photo.png')])
    const dlg = await screen.findByRole('dialog', { name: 'Import' })
    expect(dlg.textContent).toContain('“photo.png” is not a STEP or BREP file')
    expect(FakeXhr.all.length).toBe(0)
    fireEvent.click(within(dlg).getAllByRole('button').at(-1)!)
    drop(screen.getByTestId('shell-viewport'), [step('a.stp'), step('b.txt')])
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
    expect(FakeXhr.all[0]!.url).toContain('name=a.stp')
    await screen.findByText(/“b.txt” is not a STEP or BREP file/)
  })

  it('Desktop mode: a drop off the view imports nothing and says where to drop', async () => {
    await shell('desktop')
    drop(screen.getByRole('banner'), [step('box.step')])
    await screen.findByText(/Drop a STEP or BREP file on the view/)
    expect(FakeXhr.all.length).toBe(0)
  })

  it('Tablet mode: a drop imports nothing and points to From this device…', async () => {
    await shell('tablet')
    drop(screen.getByTestId('shell-viewport'), [step('box.step')])
    await screen.findByText(/Dropping files works in Desktop mode/)
    expect(FakeXhr.all.length).toBe(0)
  })

  it('a drop while the explorer is open still imports', async () => {
    await shell('desktop')
    key('Ctrl+Shift+L')
    await screen.findByTestId('explorer')
    drop(screen.getByTestId('shell-viewport'), [step('box.step')])
    await waitFor(() => expect(FakeXhr.all.length).toBe(1))
  })
})

describe('the drop guard and the plan', () => {
  it('keeps the browser from opening a dropped file, and leaves text drags and a file input alone', () => {
    const off = installDropGuard()
    const over = (types: string[], target: EventTarget = document.body) => {
      const e = new Event('dragover', { bubbles: true, cancelable: true }) as Event & { dataTransfer: unknown }
      e.dataTransfer = { types, dropEffect: 'none' }
      target.dispatchEvent(e)
      return e.defaultPrevented
    }
    expect(over(['Files'])).toBe(true)
    expect(over(['text/plain'])).toBe(false)
    const field = document.createElement('input')
    field.type = 'file'
    document.body.append(field)
    expect(over(['Files'], field)).toBe(false)
    field.remove()
    off()
    expect(over(['Files'])).toBe(false)
  })

  it('planDrop: by mode, place and extension', () => {
    const f = (n: string) => new File(['x'], n)
    const ctx = { desktop: true, onView: true, blocked: null }
    expect(planDrop([f('a.STEP'), f('b.brp')], ctx)).toEqual({ import: [expect.anything(), expect.anything()], say: null })
    expect(planDrop([f('a.png')], ctx).import).toEqual([])
    expect(planDrop([f('a.step')], { ...ctx, desktop: false }).import).toEqual([])
    expect(planDrop([f('a.step')], { ...ctx, onView: false }).import).toEqual([])
    expect(planDrop([f('a.step')], { ...ctx, blocked: 'why' })).toEqual({ import: [], say: 'why' })
    expect(planDrop([], ctx)).toEqual({ import: [], say: null })
  })
})
