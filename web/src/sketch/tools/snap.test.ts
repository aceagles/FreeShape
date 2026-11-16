import { describe, expect, it } from 'vitest'
import { drawSketch } from '../draw'
import { layoutSketch } from '../layout'
import { statusOf } from '../testFixture'
import type { SketchData, Uv } from '../wire'
import fixture from './snapFixture.json'
import { snapConstraints } from './shapes'
import type { DrawTool, Pick } from './shapes'
import { snapAim } from './snap'
import type { SnapContext } from './snap'

// The fixture is recorded from the desktop's Sketcher by scripts/web/record_snap_fixture.py: 10 px per mm, the origin at (400, 300), v up.
const toView = (uv: Uv) => ({ x: 400 + uv[0] * 10, y: 300 - uv[1] * 10 })
const toUv = (x: number, y: number): Uv => [(x - 400) / 10, (300 - y) / 10]

function context(): SnapContext {
  const sketch = fixture.base as unknown as SketchData
  return { sketch, layout: layoutSketch(drawSketch(sketch, statusOf(sketch)), toView), edges: [], toUv, toView, pointer: 'pen' }
}

const TOOL: Record<string, DrawTool> = { line: 'line', rect: 'rectangle', center_rect: 'center_rectangle', circle: 'circle', point: 'point' }

describe('the client snap gives what the desktop inference gave on the recorded pointer paths', () => {
  const ctx = context()
  for (const [name, c] of Object.entries(fixture.cases)) {
    it(name, () => {
      const picks: Pick[] = []
      c.steps.forEach((step, i) => {
        const anchor = c.tool === 'line' && picks[0] ? picks[0].uv : null
        const aim = snapAim(ctx, step.x, step.y, anchor)!
        expect(aim, `step ${i}`).not.toBeNull()
        expect(aim.uv[0], `u of step ${i}`).toBeCloseTo(step.uv[0]!, 6)
        expect(aim.uv[1], `v of step ${i}`).toBeCloseTo(step.uv[1]!, 6)
        expect(aim.snap, `snap of step ${i}`).toEqual(step.snap)
        expect(aim.infer, `inference of step ${i}`).toBe(step.infer ?? null)
        picks.push({ uv: aim.uv, snap: aim.snap, infer: aim.infer })
      })
      const got = snapConstraints(TOOL[c.tool]!, picks)
      const key = (k: { type: string; refs: string[] }) => JSON.stringify([k.type, k.refs])
      expect(got.map(key).sort()).toEqual(c.constraints.map(key).sort())
    })
  }
})

describe('snap order and reach', () => {
  const ctx = context()
  it('a point wins over the curve under it', () => {
    // the end of the base line is on the line itself
    const a = snapAim(ctx, 600.5, 300.5, null)!
    expect(a.snap).toEqual({ kind: 'point', id: expect.any(String) })
  })
  it('a pen reaches 10 px to a point, no further', () => {
    const near = snapAim(ctx, 400 + 9, 300 - 160, null)
    const far = snapAim(ctx, 400 + 13, 300 - 160, null)
    expect(near!.snap).toBeNull() // (0, 16) is on no point nor curve
    expect(far!.snap).toBeNull()
  })
  it('a model edge is snapped to and ties to nothing', () => {
    const edge = { pick: null, points: [[-20, -10] as Uv, [20, -10] as Uv], height: 0 }
    const a = snapAim({ ...ctx, edges: [edge] }, 450, 403, null)!
    expect(a.snap).toEqual({ kind: 'edge' })
    expect(a.uv[1]).toBeCloseTo(-10, 6)
    expect(snapConstraints('point', [{ uv: a.uv, snap: a.snap }])).toEqual([])
  })
  it('returns null when the view cannot place the pen on the plane', () => {
    expect(snapAim({ ...ctx, toUv: () => null }, 10, 10, null)).toBeNull()
  })
})
