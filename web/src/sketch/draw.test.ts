import { describe, expect, it } from 'vitest'
import { drawSketch, formatValue } from './draw'
import { layoutSketch } from './layout'
import { pickSketch } from './pick'
import { sketchColor, sketchMarkup } from './svg'
import { rectAndCircle, statusOf } from './testFixture'

const identity = (uv: readonly [number, number]) => ({ x: 100 + uv[0] * 4, y: 400 - uv[1] * 4 })

describe('drawing a session sketch', () => {
  const data = rectAndCircle()
  const d = drawSketch(data, statusOf(data))

  it('a rectangle, a circle and a dimension draw four lines, one circle and one label', () => {
    // the construction diagonal is a fifth line entity, drawn dashed and apart from the four of the rectangle
    expect(d.strokes.filter((s) => s.kind === 'line' && !s.construction)).toHaveLength(4)
    expect(d.stats.circles).toBe(1)
    expect(d.stats.labels).toBe(1)
    expect(d.dims[0]!.text).toBe('60 mm')
  })

  it('draws the same counts as SVG elements', () => {
    const svg = sketchMarkup(layoutSketch(d, identity), { selected: [], aimed: null }, 800, 600)
    const host = document.createElement('div')
    host.innerHTML = svg
    expect(host.querySelectorAll('path[data-kind="line"]:not([data-dashed])')).toHaveLength(4)
    expect(host.querySelectorAll('path[data-kind="circle"]')).toHaveLength(1)
    expect(host.querySelectorAll('g[data-kind="dimension"] text')).toHaveLength(1)
  })

  it('a construction line is dashed', () => {
    const svg = sketchMarkup(layoutSketch(d, identity), { selected: [], aimed: null }, 800, 600)
    const host = document.createElement('div')
    host.innerHTML = svg
    const diagonal = host.querySelector('[data-id="e6"]')!
    expect(diagonal.hasAttribute('data-dashed')).toBe(true)
    expect(diagonal.getAttribute('stroke-dasharray')).toBeTruthy()
    expect(host.querySelector('[data-id="e1"]')!.getAttribute('stroke-dasharray')).toBeNull()
    expect(d.stats.dashed).toBe(1)
  })

  it('a closed loop of lines and a circle are shaded regions; the diagonal makes none', () => {
    expect(d.regions).toHaveLength(2)
  })

  it('puts a glyph beside each constrained line and none beside a dimension', () => {
    expect(d.glyphs.map((g) => g.type).sort()).toEqual(['horizontal', 'vertical'])
  })

  it('formats dimension values without trailing zeros', () => {
    expect(formatValue(60)).toBe('60')
    expect(formatValue(12.5)).toBe('12.5')
    expect(formatValue(1 / 3)).toBe('0.333')
  })
})

describe('status colours', () => {
  const data = rectAndCircle()
  const stroke = (svg: string, id: string): string => {
    const host = document.createElement('div')
    host.innerHTML = svg
    return host.querySelector(`path[data-id="${id}"]`)!.getAttribute('stroke')!
  }
  const markup = (status: ReturnType<typeof statusOf>) => sketchMarkup(layoutSketch(drawSketch(data, status), identity), { selected: [], aimed: null }, 800, 600)

  it('a fully defined entity is drawn in the defined colour and a free one in the free colour', () => {
    const svg = markup(statusOf(data, 'free', { e1: 'defined' }))
    expect(stroke(svg, 'e1')).toBe(sketchColor.defined)
    expect(stroke(svg, 'e2')).toBe(sketchColor.free)
  })

  it('a conflicting entity is red, and a conflicting dimension is marked', () => {
    const st = statusOf(data, 'free', { e3: 'conflicting' })
    st.constraints['k3'] = 'conflicting'
    const svg = markup(st)
    expect(stroke(svg, 'e3')).toBe(sketchColor.conflicting)
    expect(svg).toContain('data-status="conflicting"')
  })
})

describe('picking in a sketch', () => {
  const data = rectAndCircle()
  const layout = layoutSketch(drawSketch(data, statusOf(data)), identity)
  // e1 runs from p1 (100, 400) to p2 (340, 400) on screen

  it('a pick near the end of a line returns the point', () => {
    for (const pointer of ['pen', 'touch', 'mouse'] as const) expect(pickSketch(layout, 104, 403, pointer)).toEqual({ kind: 'point', id: 'p1' })
  })

  it('a pick 20 pixels along the line returns the line', () => {
    for (const pointer of ['pen', 'touch'] as const) expect(pickSketch(layout, 120, 401, pointer)).toEqual({ kind: 'entity', id: 'e1' })
  })

  it('a pick on empty space returns nothing', () => {
    expect(pickSketch(layout, 700, 100)).toBeNull()
  })

  it('a dimension label and a constraint glyph are picked, ahead of the curve under them', () => {
    const dim = layout.dims[0]!
    expect(pickSketch(layout, dim.label.x + dim.label.w / 2, dim.label.y + dim.label.h / 2)).toEqual({ kind: 'dimension', id: 'k3' })
    const g = layout.glyphs.find((x) => x.id === 'k1')!
    expect(pickSketch(layout, g.x, g.y)).toEqual({ kind: 'constraint', id: 'k1' })
  })

  it('a circle is picked on its rim', () => {
    expect(pickSketch(layout, 100 + 30 * 4 + 40, 400 - 24 * 4, 'pen')).toEqual({ kind: 'entity', id: 'e5' })
  })
})

describe('glyphs and labels never overlap', () => {
  // An L-shaped profile like the bracket's: two 6 mm thicknesses at the ends of the legs, 60 and 45 long, every edge
  // horizontal or vertical, so glyphs and labels crowd the two short end edges.
  const profile = () => {
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
  const boxes = (scale: number, turn: number) => {
    const data = profile()
    const cos = Math.cos(turn)
    const sin = Math.sin(turn)
    const layout = layoutSketch(drawSketch(data, statusOf(data)), (uv) => ({ x: 600 + scale * (uv[0] * cos - uv[1] * sin), y: 400 - scale * (uv[0] * sin + uv[1] * cos) }))
    return layout
  }
  for (const [scale, turn] of [[4, 0], [8, 0], [3, 0.52], [6, 1.1], [10, 3.1]] as const) {
    it(`no glyph box meets a label box or another glyph at ${scale} px per mm turned ${turn}`, () => {
      const { glyphs, dims } = boxes(scale, turn)
      expect(glyphs.length).toBeGreaterThan(0)
      for (const g of glyphs) {
        for (const l of dims) {
          const hit = Math.abs(g.x - (l.label.x + l.label.w / 2)) < (g.size + l.label.w) / 2 && Math.abs(g.y - (l.label.y + l.label.h / 2)) < (g.size + l.label.h) / 2
          expect(hit, `glyph ${g.id} on label ${l.label.text}`).toBe(false)
        }
        for (const o of glyphs) if (o !== g) expect(Math.abs(g.x - o.x) >= g.size || Math.abs(g.y - o.y) >= g.size, `glyphs ${g.id} and ${o.id}`).toBe(true)
      }
    })
  }
})
