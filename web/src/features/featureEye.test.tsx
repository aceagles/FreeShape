import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { Shell } from '../shell/Shell'
import { LONG_PRESS_MS } from '../shell/longPress'
import { fixture } from '../test/fixtures'
import { makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function open(name: 'bracket' | 'tree') {
  const { bytes } = fixture(name)
  const calls = stubFetch((c) => (c.method === 'GET' && c.url.endsWith('/outputs') ? { bytes } : c.url.endsWith('/edit') ? { json: { rebuilt: ['T1'] } } : undefined))
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: null })
  await screen.findByRole('list', { name: 'Features' })
  return () => calls.filter((c: Call) => c.url.endsWith('/edit'))
}

function longPress(el: HTMLElement) {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const at = { pointerId: 1, pointerType: 'touch', button: 0, clientX: 10, clientY: 10 }
  fireEvent.pointerDown(el, at)
  act(() => {
    vi.advanceTimersByTime(LONG_PRESS_MS)
  })
  fireEvent.pointerUp(el, at)
  fireEvent.click(el)
  vi.useRealTimers()
}

const eyes = () => [...document.querySelectorAll<HTMLElement>('[data-feature-eye]')]
const rowOf = (id: string) => document.querySelector<HTMLElement>(`.panel-row[data-feature="${id}"]`)!

it('only sketch, plane and axis rows have an eye', async () => {
  await open('tree')
  const withEye = new Set(eyes().map((e) => e.dataset.featureEye))
  for (const r of document.querySelectorAll<HTMLElement>('.panel-row[data-feature]')) {
    expect(withEye.has(r.dataset.feature!), `${r.dataset.type} ${r.dataset.feature}`).toBe(['sketch', 'plane', 'axis'].includes(r.dataset.type!))
  }
  expect(withEye.size).toBeGreaterThan(0)
})

it('a hidden sketch shows a struck eye and "Show"; its eye sends set_features_hidden false', async () => {
  const edits = await open('tree')
  const hidden = document.querySelector<HTMLElement>('li[data-hidden] .panel-row[data-feature]')!
  expect(hidden.dataset.type).toBe('sketch')
  const eye = document.querySelector<HTMLElement>(`[data-feature-eye="${hidden.dataset.feature}"]`)!
  expect(eye.getAttribute('aria-label')).toBe("Show sketch")
  fireEvent.click(eye)
  await waitFor(() => expect(edits().length).toBe(1))
  expect(edits()[0]?.body).toEqual({ op: 'set_features_hidden', ids: [hidden.dataset.feature], value: false })
})

it('the row menu offers Hide for a shown sketch and Show for a hidden one', async () => {
  for (const name of ['bracket', 'tree'] as const) {
    cleanup()
    const edits = await open(name)
    for (const li of document.querySelectorAll<HTMLElement>('li.feature-eyed')) {
      const hidden = li.hasAttribute('data-hidden')
      const id = li.querySelector<HTMLElement>('.panel-row')!.dataset.feature!
      const before = edits().length
      longPress(rowOf(id))
      fireEvent.click(screen.getByRole('menuitem', { name: hidden ? 'Show' : 'Hide' }))
      await waitFor(() => expect(edits().length).toBe(before + 1))
      expect(edits()[before]?.body).toEqual({ op: 'set_features_hidden', ids: [id], value: !hidden })
    }
  }
})

it('a feature without an eye has no Hide or Show in its menu', async () => {
  await open('tree')
  longPress(document.querySelector<HTMLElement>('.panel-row[data-type="extrude"]')!)
  expect(screen.queryByRole('menuitem', { name: 'Hide' })).toBeNull()
  expect(screen.queryByRole('menuitem', { name: 'Show' })).toBeNull()
})
