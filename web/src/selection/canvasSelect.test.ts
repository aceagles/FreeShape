import { describe, expect, it } from 'vitest'
import { fixture } from '../test/fixtures'
import { DEFAULT_FILTER, filterKinds, FILTER_KEYS } from './filter'
import type { SelectFilter } from './filter'
import { countText, itemOf, SelectionStore } from './store'

// Card I12.5: a canvas is a kind of pick that stands for its feature. A click selects it (single mode replaces, Desktop toggles); a
// rebuild keeps it while the canvas is there, Selectable and shown.
const outputs = fixture('bracket.canvas').outputs
const canvas = outputs.canvases[0]!
const hit = { kind: 'canvas', part: null, index: -1, name: null, key: canvas.id, point: [0, 0, 0], distancePx: null } as const

describe('a canvas pick', () => {
  it('is an item with the feature id as its key, and the chip counts it', () => {
    const item = itemOf(hit)
    expect(item).toMatchObject({ id: `canvas|${canvas.id}`, kind: 'canvas', part: null, key: canvas.id })
    expect(countText([item])).toBe('1 canvas')
    expect(countText([item, item])).toBe('2 canvases')
  })

  it('a click selects it; a click on empty space clears it', () => {
    const store = new SelectionStore()
    store.click(itemOf(hit), false)
    expect(store.getState().items.map((i) => i.id)).toEqual([`canvas|${canvas.id}`])
    store.click(null, false)
    expect(store.getState().items).toEqual([])
  })

  it("a canvas that is not Selectable is not picked, so a click lands on what is behind it or clears (the pick returns that, never a canvas)", () => {
    const store = new SelectionStore()
    store.click(itemOf(hit), false)
    // the click's pick is the plane behind it (or nothing)
    store.click({ id: 'plane|Top', kind: 'plane', part: null, index: -1, name: null, key: 'Top' }, false)
    expect(store.getState().items.map((i) => i.kind)).toEqual(['canvas', 'plane'])
    store.click(null, false)
    expect(store.getState().items).toEqual([])
  })

  it('survives a rebuild while the canvas is there, shown and Selectable, and is dropped when it is not', () => {
    const store = new SelectionStore()
    store.set([itemOf(hit)])
    store.reconcile(outputs)
    expect(store.getState().items).toHaveLength(1)
    store.reconcile({ ...outputs, canvases: [{ ...canvas, selectable: false }] })
    expect(store.getState().items).toHaveLength(0)
    store.set([itemOf(hit)])
    store.reconcile({ ...outputs, canvases: [{ ...canvas, hidden: true }] })
    expect(store.getState().items).toHaveLength(0)
    store.set([itemOf(hit)])
    store.reconcile({ ...outputs, canvases: [] })
    expect(store.getState().items).toHaveLength(0)
  })
})

describe('the Select filter', () => {
  it('has no item for a canvas, and always lets it through: the desktop picks a Selectable canvas as its feature whatever the menu says', () => {
    expect(FILTER_KEYS).not.toContain('canvas')
    expect(filterKinds(DEFAULT_FILTER)).toContain('canvas')
    const none = Object.fromEntries(FILTER_KEYS.map((k) => [k, false])) as unknown as SelectFilter
    expect(filterKinds(none)).toEqual(['canvas'])
  })
})
