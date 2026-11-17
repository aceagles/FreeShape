import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { MODE_KEY } from '../shell/mode'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { MATE_IDS, RELATION_IDS } from '../tools/defs'
import { EXPORT_KEY } from './ExportDialog'
import { dispositionName, exportQuery, skippedNames } from './filesApi'

// Export STEP and STL (card I11.9) through the whole Shell, in both modes.
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

let saved: Array<{ name: string; size: number }> = []
beforeEach(() => {
  window.localStorage.clear()
  saved = []
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  URL.createObjectURL = (b: Blob | MediaSource) => `blob:${(b as Blob).size}`
  URL.revokeObjectURL = () => {}
  // the hidden link's click is the browser's save: record the name and stop jsdom from navigating
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    saved.push({ name: this.download, size: Number(this.href.split(':')[1]) })
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.localStorage.clear()
})

const hinge = (): ArrayBuffer => {
  const b = readFileSync(resolve(process.cwd(), 'fixtures', 'hinge.assembly.fsw'))
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
}
const FILE = new Uint8Array([1, 2, 3, 4]).buffer

async function open(mode: 'desktop' | 'tablet', kind: 'studio' | 'assembly' = 'studio', skipped = '') {
  window.localStorage.setItem(MODE_KEY, mode)
  const { bytes, outputs } = fixture('bracket')
  const calls = stubFetch((c: Call) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.method === 'GET' && c.url.endsWith('/tabs/T2/outputs')) return { bytes: hinge() }
    if (c.method === 'GET' && c.url.endsWith('/assembly/types')) return { json: { document: 'd', tab: 'T2', mates: MATE_IDS.map((id) => ({ id, label: id, help: 'h', freedoms: [] })), relations: RELATION_IDS.map((id) => ({ id, label: id, help: 'h' })) } }
    if (c.method === 'GET' && c.url.includes('/export?')) {
      const stl = c.url.includes('format=stl')
      return { bytes: FILE, headers: { 'Content-Disposition': `attachment; filename="bracket.${stl ? 'stl' : 'step'}"`, 'X-FreeShape-Skipped': skipped } }
    }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={vi.fn()} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'bracket'), tab('T2', 'assembly', 'hinge')], read_only: false, holder: holderOf('x') })
  if (kind === 'studio') {
    await screen.findByRole('list', { name: 'Features' })
    await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(outputs.revision))
  } else {
    await screen.findByRole('button', { name: 'hinge' })
    fireEvent.click(screen.getByRole('button', { name: 'hinge' }))
    await screen.findByTestId('instance-list')
  }
  return { calls, store, part: outputs.parts[0]!.id }
}
const exportCalls = (calls: Call[]) => calls.filter((c) => c.url.includes('/export?')).map((c) => c.url)
const dialog = () => screen.getByRole('dialog', { name: 'Export' })
const menuItem = (name: string) => {
  fireEvent.click(screen.getByRole('button', { name: 'Document' }))
  fireEvent.click(screen.getByRole('menuitem', { name }))
}
const go = async () => {
  await act(async () => fireEvent.click(within(dialog()).getByRole('button', { name: 'Export' })))
}
const pick = (group: string, option: string) => {
  const g = within(dialog())
  const radio = g.queryByRole('radio', { name: option })
  if (radio !== null) return fireEvent.click(radio)
  fireEvent.click(g.getByRole('button', { name: new RegExp(`^${group}`) }))
  fireEvent.click(screen.getByRole('menuitemcheckbox', { name: option }))
}

it('the query: format, parts, the preset, then ascii; Custom sends defl in mm and angle in radians', () => {
  expect(exportQuery({ format: 'step' })).toBe('format=step')
  expect(exportQuery({ format: 'step', parts: ['F2/1'] })).toBe('format=step&parts=F2%2F1')
  expect(exportQuery({ format: 'stl', parts: ['F2/1', 'F4/1'], preset: 'fine', ascii: false })).toBe('format=stl&parts=F2%2F1,F4%2F1&preset=fine&ascii=0')
  expect(exportQuery({ format: 'stl', preset: 'coarse', ascii: true })).toBe('format=stl&preset=coarse&ascii=1')
  expect(exportQuery({ format: 'stl', preset: 'custom', defl: 0.02, angleDeg: 10, ascii: false })).toBe('format=stl&preset=custom&defl=0.02&angle=0.17453&ascii=0')
})

it('reads the file name and the skipped bodies from the headers', () => {
  expect(dispositionName(`attachment; filename="a_b.stl"; filename*=UTF-8''%C3%A9.stl`)).toBe('é.stl')
  expect(dispositionName('attachment; filename="bracket.step"')).toBe('bracket.step')
  expect(dispositionName(null)).toBeNull()
  expect(skippedNames('Surface%201,Sk%C3%A4l')).toEqual(['Surface 1', 'Skäl'])
  expect(skippedNames('')).toEqual([])
})

it('Desktop: with one part selected, Export STL with Fine and Binary requests that part, and the browser saves the file', async () => {
  const { calls, part, store } = await open('desktop')
  act(() => window.__freeshape!.shell.selectPart(part))
  menuItem('Export STL…')
  expect(within(dialog()).getByRole('button', { name: /^Format, STL/ })).toBeTruthy()
  expect(within(dialog()).getByRole('button', { name: /^Resolution, Fine/ })).toBeTruthy()
  expect(within(dialog()).getByRole('button', { name: /^Encoding, Binary/ })).toBeTruthy()
  await go()
  expect(exportCalls(calls)).toEqual([`/documents/d/tabs/T1/export?format=stl&parts=${encodeURIComponent(part)}&preset=fine&ascii=0`])
  expect(saved).toEqual([{ name: 'bracket.stl', size: 4 }])
  expect(screen.queryByRole('dialog', { name: 'Export' })).toBeNull()
  store.stop()
})

it('Desktop: with nothing selected no parts are sent, and the last choices are kept', async () => {
  const { calls, store } = await open('desktop')
  menuItem('Export STL…')
  expect(within(dialog()).queryByRole('button', { name: /^Parts/ })).toBeNull()
  pick('Resolution', 'Coarse')
  pick('Encoding', 'ASCII')
  await go()
  expect(exportCalls(calls)).toEqual(['/documents/d/tabs/T1/export?format=stl&preset=coarse&ascii=1'])
  expect(JSON.parse(window.localStorage.getItem(EXPORT_KEY)!)).toMatchObject({ format: 'stl', preset: 'coarse', ascii: true })
  // the next dialog starts from them; a menu item names its own format
  menuItem('Export STEP…')
  expect(within(dialog()).getByRole('button', { name: /^Format, STEP/ })).toBeTruthy()
  expect(within(dialog()).queryByRole('button', { name: /^Resolution/ })).toBeNull()
  pick('Format', 'STL')
  expect(within(dialog()).getByRole('button', { name: /^Resolution, Coarse/ })).toBeTruthy()
  store.stop()
})

it('Desktop: Custom takes the deviation in mm and the angle in degrees, and holds them to the server\'s limits', async () => {
  const { calls, store } = await open('desktop')
  menuItem('Export STL…')
  pick('Resolution', 'Custom')
  const defl = within(dialog()).getByRole('textbox', { name: 'Deviation' }) as HTMLInputElement
  const angle = within(dialog()).getByRole('textbox', { name: 'Angle' }) as HTMLInputElement
  expect(defl.value).toBe('0.05')
  fireEvent.change(defl, { target: { value: '50' } })
  fireEvent.blur(defl)
  expect(within(dialog()).getByRole('alert').textContent).toContain('Deviation must be between')
  expect((within(dialog()).getByRole('button', { name: 'Export' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.change(defl, { target: { value: '0.02' } })
  fireEvent.blur(defl)
  fireEvent.change(angle, { target: { value: '10' } })
  fireEvent.blur(angle)
  await go()
  expect(exportCalls(calls)).toEqual(['/documents/d/tabs/T1/export?format=stl&preset=custom&defl=0.02&angle=0.17453&ascii=0'])
  store.stop()
})

it('Desktop: the feature toolbar\'s Export opens the dialog on the format used last, STEP at first', async () => {
  const { calls, store } = await open('desktop')
  fireEvent.click(document.querySelector('.desktop-toolbar [data-tool="export"]') as HTMLElement)
  expect(within(dialog()).getByRole('button', { name: /^Format, STEP/ })).toBeTruthy()
  await go()
  expect(exportCalls(calls)).toEqual(['/documents/d/tabs/T1/export?format=step'])
  expect(saved[0]!.name).toBe('bracket.step')
  store.stop()
})

it('Desktop: an Assembly exports from the Document menu and the toolbar, and has no Parts to choose', async () => {
  const { calls, store } = await open('desktop', 'assembly')
  fireEvent.click(document.querySelector('.desktop-toolbar [data-strip="assembly"] [data-tool="export"], [data-strip="assembly"] [data-tool="export"]') as HTMLElement)
  expect(within(dialog()).getByRole('button', { name: /^Format, STEP/ })).toBeTruthy()
  expect(within(dialog()).queryByText(/Parts/)).toBeNull()
  await go()
  expect(exportCalls(calls)).toEqual(['/documents/d/tabs/T2/export?format=step'])
  menuItem('Export STL…')
  expect(within(dialog()).queryByText(/Parts/)).toBeNull()
  store.stop()
})

it('Tablet: the top bar\'s Export opens the dialog over the left column; the Resolution is a row of buttons and Custom opens the pad', async () => {
  const { calls, store } = await open('tablet')
  fireEvent.click(screen.getByRole('button', { name: 'Export' }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Export STEP / STL…' }))
  const d = dialog()
  expect(d.closest('[data-region="left-column"]')).not.toBeNull()
  fireEvent.click(within(d).getByRole('radio', { name: 'STL' }))
  fireEvent.click(within(d).getByRole('radio', { name: 'Custom' }))
  fireEvent.click(within(d).getByRole('button', { name: /^Deviation, 0.05 mm/ }))
  expect(screen.getByTestId('pad')).toBeTruthy()
  fireEvent.click(within(screen.getByTestId('pad')).getByRole('button', { name: 'Cancel' }))
  await go()
  expect(exportCalls(calls)).toEqual(['/documents/d/tabs/T1/export?format=stl&preset=custom&defl=0.05&angle=0.3&ascii=0'])
  store.stop()
})

it('Tablet: the top bar\'s Export on an Assembly has no Parts row', async () => {
  const { store } = await open('tablet', 'assembly')
  fireEvent.click(screen.getByRole('button', { name: 'Export' }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Export STEP / STL…' }))
  expect(within(dialog()).queryByText(/Parts/)).toBeNull()
  store.stop()
})

it('says which surface bodies an STL left out, and keeps the dialog open with the server\'s words when the export is refused', async () => {
  const { store } = await open('desktop', 'studio', 'Surface%201')
  menuItem('Export STL…')
  await go()
  expect((await screen.findByRole('dialog', { name: 'Surface bodies left out' })).textContent).toContain('bracket.stl was saved without Surface 1')
  store.stop()
})

it('a refused export shows the server\'s words in the dialog and saves nothing', async () => {
  const { store } = await open('desktop')
  stubFetch((c) => (c.url.includes('/export?') ? { status: 400, json: { error: 'unknown part id F9/1' } } : undefined))
  menuItem('Export STEP…')
  await go()
  expect((await within(dialog()).findByTestId('export-error')).textContent).toBe('unknown part id F9/1')
  expect(saved).toEqual([])
  store.stop()
})
