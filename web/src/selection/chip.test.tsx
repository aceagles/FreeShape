import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { fixture } from '../test/fixtures'
import { holderOf, makeStore, stubFetch, tab } from '../test/fakes'
import { idOfItem } from './store'
import type { SelectionItem } from './store'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const hello = (extra: object = {}) => ({
  type: 'hello',
  client: 'abcdefgh',
  document: { id: 'd', name: 'Bracket' },
  tabs: [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'studio', 'Part Studio 2')],
  read_only: false,
  holder: null,
  ...extra,
})

async function open(extra: object = {}) {
  const bytes = fixture('bracket').bytes
  stubFetch((c) => (c.method === 'GET' && c.url.endsWith('/outputs') ? { bytes } : undefined))
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say(hello(extra))
  await screen.findByRole('list', { name: 'Parts (1)' })
  // the shell's api is a new object whenever something it reports changes: read it afresh
  return () => window.__freeshape!.shell
}

const text = () => screen.getByTestId('selection-count').textContent
const clear = () => screen.getByTestId('selection-clear') as HTMLButtonElement
const multi = () => screen.getByTestId('selection-multi') as HTMLButtonElement
const item = (kind: 'face' | 'edge' | 'vertex', i: number): SelectionItem => ({ id: idOfItem(kind, 'F2/1', `${kind}${i}`, i, null), kind, part: 'F2/1', index: i, name: `${kind}${i}`, key: null })

it('shows the chip with nothing selected: the count says so, Multi is off and reachable, Clear is dimmed', async () => {
  await open()
  expect(text()).toBe('Nothing selected')
  expect(clear().disabled).toBe(true)
  expect(multi().getAttribute('aria-pressed')).toBe('false')
  expect(screen.getByTestId('chip-slot').contains(screen.getByTestId('selection-chip'))).toBe(true)
})

it('counts what is selected: one kind, two kinds, and items for more', async () => {
  const shell = await open()
  act(() => shell().selection.set([item('face', 0)]))
  expect(text()).toBe('1 face')
  act(() => shell().selection.set([item('face', 0), item('face', 1), item('edge', 0)]))
  expect(text()).toBe('2 faces, 1 edge')
  act(() => shell().selection.set([item('face', 0), item('edge', 0), item('vertex', 0)]))
  expect(text()).toBe('3 items')
})

it('the Multi toggle switches the mode, and Clear empties the selection', async () => {
  const shell = await open()
  fireEvent.click(multi())
  expect(multi().getAttribute('aria-pressed')).toBe('true')
  expect(shell().selection.multi).toBe(true)
  act(() => shell().selection.set([item('face', 0), item('face', 1)]))
  fireEvent.click(multi())
  expect(shell().selection.multi).toBe(false)
  expect(text()).toBe('2 faces') // kept until the next Commit
  fireEvent.click(clear())
  expect(text()).toBe('Nothing selected')
  expect(shell().selection.getState().items).toEqual([])
})

it('Multi shows as on while a dialog field takes several items', async () => {
  const shell = await open()
  act(() => shell().selection.setDialogMulti(true))
  expect(multi().getAttribute('aria-pressed')).toBe('true')
  act(() => shell().selection.setDialogMulti(false))
  expect(multi().getAttribute('aria-pressed')).toBe('false')
})

it('the Parts list and the view share one selection: a part tapped in the list is one part in the chip, and Clear unselects it in the list', async () => {
  const shell = await open()
  fireEvent.click(screen.getByRole('button', { name: /^Bracket$/ }))
  expect(shell().selectedPart).toBe('F2/1')
  expect(text()).toBe('1 part')
  fireEvent.click(clear())
  expect(shell().selectedPart).toBeNull()
  expect(screen.getByRole('button', { name: /^Bracket$/ }).getAttribute('aria-current')).toBeNull()
  // a face picked in the view replaces the part in the list
  act(() => shell().selectPart('F2/1'))
  act(() => shell().selection.set([item('face', 0)]))
  expect(shell().selectedPart).toBeNull()
})

it('a selected feature and the selection clear each other', async () => {
  const shell = await open()
  fireEvent.click(screen.getByRole('button', { name: 'Extrude 1' }))
  expect(shell().selectedFeature).toBe('F2')
  act(() => shell().selection.set([item('face', 0)]))
  expect(shell().selectedFeature).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Extrude 1' }))
  expect(shell().selection.getState().items).toEqual([])
  expect(text()).toBe('Nothing selected')
})

it('the selection is cleared when the tab changes', async () => {
  const shell = await open()
  act(() => shell().selection.set([item('face', 0), item('edge', 1)]))
  expect(text()).toBe('1 face, 1 edge')
  fireEvent.click(screen.getByRole('button', { name: 'Part Studio 2' }))
  await waitFor(() => expect(text()).toBe('Nothing selected'))
  expect(shell().selection.getState().items).toEqual([])
})

it('with the Document read-only the chip is there and selecting works', async () => {
  const shell = await open({ read_only: true, holder: holderOf('desktop') })
  expect(screen.getByTestId('read-only-banner')).toBeTruthy()
  act(() => shell().selection.set([item('face', 0)]))
  expect(text()).toBe('1 face')
  fireEvent.click(clear())
  expect(text()).toBe('Nothing selected')
})
