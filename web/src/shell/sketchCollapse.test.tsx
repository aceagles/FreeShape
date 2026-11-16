import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { openedMessage } from '../sketch/testFixture'
import { makeStore, stubFetch, tab } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { MODE_KEY } from './mode'
import type { Mode } from './mode'
import { Shell } from './Shell'

// Card F18: the left column collapses while a sketch is open, in both modes (I7.7 had locked the button for the whole
// sketch; neither the layout mock nor a card asked for that). The sketch stays open through collapse and expand.
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

async function mountSketch(mode: Mode) {
  window.localStorage.setItem(MODE_KEY, mode)
  const { bytes } = fixture('bracket')
  stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.includes('/features/types')) return { json: { document: 'd', tab: 'T1', revision: 1, types: [] } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: null })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  act(() => window.__freeshape!.openSketch({ plane: { kind: 'std', name: 'Front' } }))
  sock.say({ ...openedMessage(), document: 'd', tab: 'T1' })
  await waitFor(() => expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull())
}

const collapseButton = () => screen.getByRole('button', { name: 'Collapse feature panel' }) as HTMLButtonElement

for (const mode of ['desktop', 'tablet'] as const) {
  it(`${mode} mode: in a sketch the collapse button is on; the column collapses to the rail and expands, and the sketch stays open`, async () => {
    await mountSketch(mode)
    expect(screen.getByTestId('shell').dataset.mode).toBe(mode)
    expect(collapseButton().disabled).toBe(false)
    act(() => collapseButton().click())
    expect(screen.getByTestId('panel-rail')).toBeTruthy()
    expect(screen.queryByTestId('feature-panel')).toBeNull()
    expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull()
    act(() => screen.getByRole('button', { name: 'Expand feature panel' }).click())
    expect(screen.getByTestId('feature-panel')).toBeTruthy()
    expect(collapseButton().disabled).toBe(false)
    expect(document.querySelector('[data-strip="sketch"]')).not.toBeNull()
  })
}

it('Desktop mode: a dialog opened in a sketch (the Dimension box) floats over the viewport and leaves the collapsed column collapsed', async () => {
  await mountSketch('desktop')
  act(() => collapseButton().click())
  act(() => window.__freeshape!.shell.openDialog('Dimension'))
  expect(screen.getByRole('dialog', { name: 'Dimension' })).toBeTruthy()
  expect(screen.getByTestId('panel-rail')).toBeTruthy()
  // with the dialog open the column cannot be collapsed or expanded under it, as before; closing it leaves the rail
  act(() => window.__freeshape!.shell.closeDialog())
  expect(screen.getByTestId('panel-rail')).toBeTruthy()
})

it('Tablet mode: a dialog opened in a sketch expands the collapsed column, whose box it takes', async () => {
  await mountSketch('tablet')
  act(() => collapseButton().click())
  act(() => window.__freeshape!.shell.openDialog('Dimension'))
  expect(screen.getByRole('dialog', { name: 'Dimension' })).toBeTruthy()
  expect(screen.queryByTestId('panel-rail')).toBeNull()
  // while the dialog covers the panel its collapse button is off, as before
  expect(collapseButton().disabled).toBe(true)
})
