import { describe, expect, it } from 'vitest'
import { drawSketch } from './draw'
import { layoutSketch } from './layout'
import { boxSelection, clickSelection, sketchBoxHits } from './select'
import { rectAndCircle, statusOf } from './testFixture'

// I9.9: in an open sketch (Desktop mode) a box selects the entities inside it, and a click toggles.
// The fixture is a 60 x 48 rectangle (e1 bottom, e2 right, e3 top, e4 left), a circle of radius 10 at (30, 24) and a
// construction diagonal e6, laid out at 4 px per mm with y up from (100, 400).

const data = rectAndCircle()
const toView = (uv: readonly [number, number]) => ({ x: 100 + 4 * uv[0], y: 400 - 4 * uv[1] })
const layout = layoutSketch(drawSketch(data, statusOf(data)), toView)
const ids = (hits: readonly { id: string }[]) => hits.map((h) => h.id).sort()

describe('a box in an open sketch', () => {
  it('left to right takes the entities wholly inside it', () => {
    // round the circle only (x 20..40 mm, y 14..34 mm), with 2 mm to spare
    const box = { x0: 100 + 4 * 18, y0: 400 - 4 * 36, x1: 100 + 4 * 42, y1: 400 - 4 * 12 }
    expect(ids(sketchBoxHits(layout, box))).toEqual(['e5'])
    // the whole rectangle: every entity (points are not taken)
    const all = sketchBoxHits(layout, { x0: 90, y0: 400 - 4 * 50, x1: 100 + 4 * 62, y1: 410 })
    expect(ids(all)).toEqual(['e1', 'e2', 'e3', 'e4', 'e5', 'e6'])
    expect(all.every((h) => h.kind === 'entity')).toBe(true)
  })

  it('right to left also takes what it crosses', () => {
    // the same box round the circle, dragged right to left: the diagonal e6 crosses it too
    const box = { x1: 100 + 4 * 18, y0: 400 - 4 * 36, x0: 100 + 4 * 42, y1: 400 - 4 * 12 }
    expect(ids(sketchBoxHits(layout, box))).toEqual(['e5', 'e6'])
    // a thin box across the left edge only
    const thin = { x0: 100 + 4 * 2, y0: 400 - 4 * 30, x1: 100 - 4 * 2, y1: 400 - 4 * 20 }
    expect(ids(sketchBoxHits(layout, thin))).toEqual(['e4'])
  })

  it('replaces the selection, or with Shift adds to it', () => {
    const was = [{ kind: 'point' as const, id: 'p2' }, { kind: 'entity' as const, id: 'e5' }]
    const found = [{ kind: 'entity' as const, id: 'e5' }, { kind: 'entity' as const, id: 'e1' }]
    expect(boxSelection(was, found, false)).toEqual(found)
    expect(boxSelection(was, found, true)).toEqual([...was, { kind: 'entity', id: 'e1' }])
  })
})

describe('a click in an open sketch (Desktop mode)', () => {
  it('toggles the item and keeps the rest; empty space clears unless Shift or Ctrl is held', () => {
    const a = { kind: 'entity' as const, id: 'e1' }
    const b = { kind: 'point' as const, id: 'p3' }
    let s = clickSelection([], a, false)
    s = clickSelection(s, b, false)
    expect(s).toEqual([a, b])
    s = clickSelection(s, a, false)
    expect(s).toEqual([b])
    expect(clickSelection(s, null, true)).toBe(s)
    expect(clickSelection(s, null, false)).toEqual([])
  })
})
