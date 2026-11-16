// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'
import { fixture } from '../test/fixtures'
import type { Part, StudioOutputs } from '../wire'
import { countText, idOfItem, partItem, SelectionStore } from './store'
import type { SelectionItem } from './store'

const face = (part: string, name: string | null, index: number): SelectionItem => ({ id: idOfItem('face', part, name, index, null), kind: 'face', part, index, name, key: null })
const A = face('P', 'FaceA', 0)
const B = face('P', 'FaceB', 1)
const ids = (s: SelectionStore) => s.getState().items.map((i) => i.id)

describe('single mode (the default)', () => {
  it('a Commit on an item replaces the selection; on the one selected item it deselects; over empty space it clears', () => {
    const s = new SelectionStore()
    s.commit(A)
    s.commit(B)
    expect(ids(s)).toEqual([B.id])
    s.commit(B)
    expect(ids(s)).toEqual([])
    s.commit(A)
    s.commit(null)
    expect(ids(s)).toEqual([])
  })

  it('a Commit on an item of a selection of several replaces it with that item, and does not deselect it', () => {
    const s = new SelectionStore()
    s.setMulti(true)
    s.commit(A)
    s.commit(B)
    s.setMulti(false)
    s.commit(B)
    expect(ids(s)).toEqual([B.id])
  })
})

describe('Multi mode', () => {
  it('a Commit adds or removes; over empty space it cancels that pick and leaves the selection; Clear empties it', () => {
    const s = new SelectionStore()
    s.setMulti(true)
    s.commit(A)
    s.commit(B)
    expect(ids(s)).toEqual([A.id, B.id])
    s.commit(A)
    expect(ids(s)).toEqual([B.id])
    s.commit(null)
    expect(ids(s)).toEqual([B.id])
    s.commit(A)
    s.clear()
    expect(ids(s)).toEqual([])
  })

  it('turning Multi off keeps several items until the next Commit', () => {
    const s = new SelectionStore()
    s.setMulti(true)
    s.commit(A)
    s.commit(B)
    s.setMulti(false)
    expect(s.multi).toBe(false)
    expect(ids(s)).toEqual([A.id, B.id])
    s.commit(A)
    expect(ids(s)).toEqual([A.id])
  })

  it('is on while a dialog field that takes several items is active, whatever the toggle says', () => {
    const s = new SelectionStore()
    s.setDialogMulti(true)
    expect(s.multi).toBe(true)
    expect(s.getState().multi).toBe(true)
    s.commit(A)
    s.commit(B)
    expect(ids(s)).toEqual([A.id, B.id])
    s.setDialogMulti(false)
    expect(s.multi).toBe(false)
    s.setMulti(true)
    s.setDialogMulti(true)
    s.setDialogMulti(false)
    expect(s.multi).toBe(true)
  })
})

describe('the Aim', () => {
  it('tells nobody when it lands on the item that is already Aimed, and once when it changes', () => {
    const s = new SelectionStore()
    const seen = vi.fn()
    s.subscribe(seen)
    s.aim(A)
    s.aim({ ...A })
    s.aim(A)
    expect(seen).toHaveBeenCalledTimes(1)
    s.aim(B)
    expect(seen).toHaveBeenCalledTimes(2)
    s.aim(null)
    s.aim(null)
    expect(seen).toHaveBeenCalledTimes(3)
  })

  it('does not change the state object (so a component that reads the selection does not render again)', () => {
    const s = new SelectionStore()
    const before = s.getState()
    s.aim(A)
    s.aim(B)
    expect(s.getState()).toBe(before)
    s.commit(B)
    expect(s.getState()).not.toBe(before)
    expect(s.getAimed()).toBeNull()
  })

  it('a Commit over empty space with nothing selected and nothing Aimed tells nobody', () => {
    const s = new SelectionStore()
    const seen = vi.fn()
    s.subscribe(seen)
    s.commit(null)
    s.clear()
    expect(seen).not.toHaveBeenCalled()
  })
})

describe('the count text', () => {
  const kind = (...k: SelectionItem['kind'][]) => k.map((kind) => ({ kind }))
  it('names one kind, two kinds, and says items for more', () => {
    expect(countText([])).toBe('Nothing selected')
    expect(countText(kind('face'))).toBe('1 face')
    expect(countText(kind('edge', 'edge', 'edge'))).toBe('3 edges')
    expect(countText(kind('vertex', 'vertex'))).toBe('2 vertices')
    expect(countText(kind('face', 'face', 'edge'))).toBe('2 faces, 1 edge')
    expect(countText(kind('face', 'edge', 'vertex'))).toBe('3 items')
  })
})

describe('across a rebuild', () => {
  const out = fixture('bracket').outputs
  const part = out.parts[0] as Part
  const nameOf = (kind: 'face' | 'edge', i: number) => part.names[kind].get(i)!
  const pick = (kind: 'face' | 'edge', i: number): SelectionItem => ({ id: idOfItem(kind, part.id, nameOf(kind, i), i, null), kind, part: part.id, index: i, name: nameOf(kind, i), key: null })

  it('keeps an item whose part and Element name are still there, and follows the name to its new index', () => {
    const s = new SelectionStore()
    s.setMulti(true)
    s.commit(pick('face', 1))
    s.commit(pick('edge', 2))
    // the next outputs number the faces in the other order: the name moves to a new index
    const faces = new Map([...part.names.face].map(([i, n]) => [i === 1 ? 0 : i === 0 ? 1 : i, n] as [number, string]))
    const next: StudioOutputs = { ...out, parts: [{ ...part, names: { ...part.names, face: faces } }] }
    s.reconcile(next)
    expect(s.getState().items.map((i) => [i.kind, i.name, i.index])).toEqual([
      ['face', nameOf('face', 1), 0],
      ['edge', nameOf('edge', 2), 2],
    ])
  })

  it('drops an item whose Element name is gone, whose part is gone, or whose plane is gone, and keeps the rest', () => {
    const s = new SelectionStore()
    s.setMulti(true)
    s.commit(pick('face', 0))
    s.commit(pick('face', 1))
    s.commit(partItem(part.id))
    s.commit({ id: 'plane|Top', kind: 'plane', part: null, index: -1, name: null, key: 'Top' })
    s.commit({ id: 'plane|F9', kind: 'plane', part: null, index: -1, name: null, key: 'F9' })
    const faces = new Map([...part.names.face].filter(([i]) => i !== 1))
    s.reconcile({ ...out, parts: [{ ...part, names: { ...part.names, face: faces } }] })
    expect(ids(s)).toEqual([pick('face', 0).id, `part|${part.id}`, 'plane|Top'])
    s.reconcile({ ...out, parts: [] })
    expect(ids(s)).toEqual(['plane|Top'])
  })

  it('leaves the selection alone, and tells nobody, when every item is still there', () => {
    const s = new SelectionStore()
    s.commit(pick('face', 0))
    const seen = vi.fn()
    s.subscribe(seen)
    s.reconcile(out)
    expect(seen).not.toHaveBeenCalled()
    expect(s.getState().items).toHaveLength(1)
  })
})
