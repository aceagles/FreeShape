import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { MODE_KEY } from './mode'
import { Shell } from './Shell'

// I9.14 through the whole Shell: the Select menu's ticks and what they do to the pick filter, and what a double click on
// a sketch region, a plane, a face does. The pointer's double click itself is tested in selection/selectFilter.test.ts.
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

/** The Bracket's bytes with its last feature (a chamfer) a plane instead: the fixture has no plane feature. The header's JSON is padded back to a multiple of 8. */
function withPlane(bytes: ArrayBuffer): ArrayBuffer {
  const raw = new Uint8Array(bytes)
  const h = new DataView(bytes).getUint32(4, true)
  const text = new TextDecoder().decode(raw.subarray(8, 8 + h)).trimEnd()
  const old = '"type":"chamfer","label":"Chamfer","name":"Chamfer 1"'
  if (!text.includes(old)) throw new Error('the bracket fixture does not look as withPlane expects')
  const patched = new TextEncoder().encode(text.replace(old, '"type":"plane","label":"Plane","name":"Plane 1"'))
  const padded = new Uint8Array(Math.ceil(patched.length / 8) * 8).fill(0x20)
  padded.set(patched)
  const out = new Uint8Array(8 + padded.length + (raw.length - 8 - h))
  out.set(raw.subarray(0, 8))
  new DataView(out.buffer).setUint32(4, padded.length, true)
  out.set(padded, 8)
  out.set(raw.subarray(8 + h), 8 + padded.length)
  return out.buffer
}

async function shell(mode: 'desktop' | 'tablet' = 'desktop') {
  window.localStorage.setItem(MODE_KEY, mode)
  const { bytes, outputs } = fixture('bracket')
  const calls = stubFetch((c: Call) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes: withPlane(bytes) }
    if (c.method === 'GET' && c.url.endsWith('/documents/d/tabs')) return { json: { tabs: [tab('T1', 'studio', 'Part Studio 1')] } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={vi.fn()} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: holderOf('x') })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(outputs.revision))
  return { sock, calls, outputs }
}

const selectItem = (name: RegExp) => {
  fireEvent.click(screen.getByRole('button', { name: 'Select' }))
  return screen.getByRole('menuitemcheckbox', { name })
}
const ticks = (): Record<string, string | null> => {
  fireEvent.click(screen.getByRole('button', { name: 'Select' }))
  const out = Object.fromEntries(screen.getAllByRole('menuitemcheckbox').map((i) => [i.textContent!.replace(/\s+/g, ' ').trim(), i.getAttribute('aria-checked')]))
  fireEvent.keyDown(screen.getByRole('menu', { name: 'Select' }), { key: 'Escape' })
  return out
}
const store = () => window.__freeshape!.shell.selection
const region = (sketch: string) => ({ id: `region|${sketch}|#0`, kind: 'region' as const, part: sketch, index: 0, name: null, key: null })

describe('the Select menu in Desktop mode', () => {
  it('shows the desktop defaults, takes a tick, and Reset to default puts them back', async () => {
    await shell()
    const defaults = { Vertices: 'false', Edges: 'true', Faces: 'true', 'Solids (whole parts)': 'false', Sketches: 'true', Planes: 'true', Axes: 'true' }
    expect(ticks()).toEqual(defaults)
    expect(store().pickFilter).not.toContain('vertex')
    fireEvent.click(selectItem(/^Faces/))
    expect(ticks()['Faces']).toBe('false')
    expect(store().pickFilter).not.toContain('face')
    fireEvent.click(selectItem(/^Vertices/))
    expect(store().pickFilter).toContain('vertex')
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Reset to default/ }))
    expect(ticks()).toEqual(defaults)
    // a canvas is always picked (card I12.5): the desktop picks it as a feature, whatever the menu says
    expect(store().pickFilter).toEqual(['edge', 'face', 'region', 'plane', 'axis', 'canvas'])
  })

  it('has no filter in Tablet mode', async () => {
    await shell('tablet')
    expect(store().pickFilter).toBeUndefined()
  })
})

describe('a mouse double click in Desktop mode', () => {
  it('on a sketch region sends sketch.open with the sketch\'s feature id', async () => {
    const { sock } = await shell()
    act(() => store().doubleClick(region('F4')))
    expect(sock.sent.filter((m) => m['type'] === 'sketch.open')).toEqual([{ type: 'sketch.open', document: 'd', tab: 'T1', feature: 'F4' }])
  })

  it('on a plane of the studio opens that feature\'s dialog; on a default plane it does nothing', async () => {
    const { sock, calls } = await shell()
    const asked = () => calls.filter((c) => c.method === 'POST' && c.url.endsWith('/dialog')).map((c) => c.url)
    act(() => store().doubleClick({ id: 'plane|Top', kind: 'plane', part: null, index: -1, name: null, key: 'Top' }))
    expect(asked()).toEqual([])
    act(() => store().doubleClick({ id: 'plane|F8', kind: 'plane', part: null, index: -1, name: null, key: 'F8' }))
    await waitFor(() => expect(asked().some((u) => u.endsWith('/features/F8/dialog'))).toBe(true))
    expect(sock.sent.filter((m) => m['type'] === 'sketch.open')).toEqual([])
  })

  it('on a face, an edge or a vertex does nothing', async () => {
    const { sock, calls, outputs } = await shell()
    const part = outputs.parts[0]!.id
    for (const kind of ['face', 'edge', 'vertex'] as const) act(() => store().doubleClick({ id: `${kind}|${part}|#0`, kind, part, index: 0, name: null, key: null }))
    expect(sock.sent.filter((m) => m['type'] === 'sketch.open')).toEqual([])
    expect(calls.filter((c) => c.method === 'POST' && c.url.endsWith('/dialog'))).toEqual([])
  })
})
