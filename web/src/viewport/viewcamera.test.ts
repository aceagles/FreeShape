import { describe, expect, it } from 'vitest'
import { STANDARD_VIEWS, ViewCamera } from './camera'
import type { StandardView } from './camera'

const near = (a: readonly number[], b: readonly number[], tol = 1e-3) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i]!, -Math.log10(tol)))

describe('standard views', () => {
  const expected: Record<StandardView, { right: number[]; up: number[] }> = {
    front: { right: [1, 0, 0], up: [0, 0, 1] },
    back: { right: [-1, 0, 0], up: [0, 0, 1] },
    left: { right: [0, -1, 0], up: [0, 0, 1] },
    right: { right: [0, 1, 0], up: [0, 0, 1] },
    top: { right: [1, 0, 0], up: [0, 1, 0] },
    bottom: { right: [1, 0, 0], up: [0, -1, 0] },
    iso: { right: [0.7071, 0.7071, 0], up: [-0.4082, 0.4082, 0.8165] },
  }
  for (const name of Object.keys(STANDARD_VIEWS) as StandardView[]) {
    it(`${name} looks the right way with the right axes on screen`, () => {
      const v = new ViewCamera()
      v.roll = 1
      v.setBack(STANDARD_VIEWS[name])
      expect(v.roll).toBe(0)
      const { right, up } = v.axes()
      near(right.toArray(), expected[name].right)
      near(up.toArray(), expected[name].up)
    })
  }

  it('top and bottom orbit without a jump', () => {
    const v = new ViewCamera()
    v.setBack(STANDARD_VIEWS.top)
    const right = v.axes().right.clone()
    v.orbit(0, 0.001, v.target.clone())
    near(v.axes().right.toArray(), right.toArray(), 1e-2)
  })

  it('Normal to snaps to the nearest axis view, or looks at a given normal', () => {
    const v = new ViewCamera()
    v.setBack([0.3, -0.2, 1])
    v.normalTo()
    near(v.state().back, [0, 0, 1])
    v.setBack([0.2, -1, 0.3])
    v.normalTo()
    near(v.state().back, [0, -1, 0])
    v.normalTo([-0.1, 0.1, -0.9])
    near(v.state().back, [0, 0, -1])
  })
})

describe('fit', () => {
  it('keeps a fitted model out from under the view cube in every standard view', () => {
    const lo = [0, -20, 0] as const
    const hi = [60, 20, 45] as const
    for (const [w, h] of [
      [1024, 768],
      [1366, 1024],
    ] as const) {
      for (const name of Object.keys(STANDARD_VIEWS) as StandardView[]) {
        const v = new ViewCamera()
        v.resize(w, h)
        v.setBack(STANDARD_VIEWS[name])
        v.fit({ lo, hi })
        for (const x of [lo[0], hi[0]])
          for (const y of [lo[1], hi[1]])
            for (const z of [lo[2], hi[2]]) {
              const p = v.project([x, y, z])
              expect(p.y).toBeGreaterThanOrEqual(84 - 1e-6)
              expect(p.y).toBeLessThanOrEqual(h - 60 + 1e-6)
            }
      }
    }
  })
})
