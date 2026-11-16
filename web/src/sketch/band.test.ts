import { describe, expect, it } from 'vitest'
import { ViewCamera } from '../viewport/camera'
import { BANDS, TOP_BAND, fitSketchView } from './band'
import { drawSketch } from './draw'
import { uvToView } from './frame'
import { layoutSketch } from './layout'
import type { Rect } from './layout'
import { FRONT, statusOf } from './testFixture'
import type { Bounds } from '../viewport/camera'

// F10: no hint or note over the view lies on a dimension label or on the sketch. The bracket's profile (an L, two 6 mm
// ends, 60 and 45 long) is laid out by the fit that opening a sketch uses, in the view the tablet gives it, with each hint
// state's box on top.

function profile() {
  const points: Record<string, [number, number]> = { origin: [0, 0], a: [0, 0], b: [60, 0], c: [60, 6], d: [6, 6], e: [6, 45], f: [0, 45] }
  const ids = ['a', 'b', 'c', 'd', 'e', 'f']
  const entities: Record<string, { type: string; p1: string; p2: string }> = {}
  ids.forEach((p, i) => (entities[`l${i}`] = { type: 'line', p1: p, p2: ids[(i + 1) % 6]! }))
  const constraints = [
    ...[0, 2, 4].map((i) => ({ id: `h${i}`, type: 'horizontal', refs: [`l${i}`] })),
    ...[1, 3, 5].map((i) => ({ id: `v${i}`, type: 'vertical', refs: [`l${i}`] })),
    { id: 'd0', type: 'distance', refs: ['l0'], value: 60 },
    { id: 'd1', type: 'distance', refs: ['l1'], value: 6 },
    { id: 'd2', type: 'distance', refs: ['l5'], value: 45 },
    { id: 'd3', type: 'distance', refs: ['l4'], value: 6 },
  ]
  return { points, entities, constraints }
}

function opened(width: number, height: number) {
  const data = profile()
  const drawing = drawSketch(data, statusOf(data))
  const view = new ViewCamera()
  view.resize(width, height)
  view.lookAtPlane(FRONT.normal, FRONT.ydir, 0)
  const box: Bounds = { lo: [-7.5, 0, -5.6], hi: [67.5, 0, 50.6] }
  fitSketchView(view, box, FRONT, drawing)
  return layoutSketch(drawing, (uv) => uvToView(view, FRONT, uv))
}

const meets = (a: Rect, b: Rect): boolean => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
const around = (x: number, y: number): Rect => ({ x: x - 3, y: y - 3, w: 6, h: 6 })

const states: [string, Rect[]][] = [
  ['the Dimension hint, or a Trim, Fillet or Use edge hint', [BANDS.chips, BANDS.hint]],
  ['the constraint note', [BANDS.chips, BANDS.note]],
  ['the note with a hint under it', [BANDS.chips, BANDS.note, BANDS.stackedHint]],
]

describe('hints never cover the sketch or its labels', () => {
  for (const [w, h] of [[744, 653], [1086, 909]] as const) {
    const layout = opened(w, h)
    it(`at ${w} x ${h} the profile has its four labels`, () => {
      expect(layout.dims.map((d) => d.label.text).sort()).toEqual(['45 mm', '6 mm', '6 mm', '60 mm'])
    })
    for (const [name, boxes] of states) {
      it(`at ${w} x ${h}, with ${name} up, no label, point or line meets it`, () => {
        for (const box of boxes) {
          for (const d of layout.dims) expect(meets(d.label, box), `label ${d.label.text} at ${Math.round(d.label.y)} on a box at ${box.y}`).toBe(false)
          for (const p of layout.points) expect(meets(around(p.x, p.y), box), `point ${p.id}`).toBe(false)
          for (const s of layout.strokes) for (const p of s.pts) expect(meets(around(p.x, p.y), box), `stroke ${s.id}`).toBe(false)
        }
      })
    }
    it(`at ${w} x ${h} everything lies under the top band, inside the view`, () => {
      for (const d of layout.dims) {
        expect(d.label.y).toBeGreaterThanOrEqual(TOP_BAND - 0.5)
        expect(d.label.y + d.label.h).toBeLessThanOrEqual(h)
        expect(d.label.x).toBeGreaterThanOrEqual(0)
        expect(d.label.x + d.label.w).toBeLessThanOrEqual(w)
      }
    })
  }
})
