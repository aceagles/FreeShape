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

describe('the middle of a line (F24)', () => {
  // l1 runs (-20, 10) to (20, 10): its middle (0, 10) has no point. l2 runs (-20, -10) to (20, -10) and has a point at its middle.
  const sketch: SketchData = {
    points: { origin: [0, 0], a: [-20, 10], b: [20, 10], c: [-20, -10], d: [20, -10], m: [0, -10] },
    entities: {
      l1: { type: 'line', p1: 'a', p2: 'b', construction: false },
      l2: { type: 'line', p1: 'c', p2: 'd', construction: false },
    } as SketchData['entities'],
    constraints: [{ id: 'k1', type: 'midpoint', refs: ['m', 'l2'] }],
  }
  const ctx: SnapContext = { sketch, layout: layoutSketch(drawSketch(sketch, statusOf(sketch)), toView), edges: [], toUv, toView, pointer: 'pen' }
  const mid1 = toView([0, 10])

  it('near the middle it snaps to it, and the pick is constrained to the line', () => {
    const a = snapAim(ctx, mid1.x + 4, mid1.y + 3, null)!
    expect(a.snap).toEqual({ kind: 'mid', id: 'l1' })
    expect(a.uv[0]).toBeCloseTo(0, 9)
    expect(a.uv[1]).toBeCloseTo(10, 9)
    expect(snapConstraints('point', [{ uv: a.uv, snap: a.snap }])).toEqual([{ type: 'midpoint', refs: ['$p0', 'l1'] }])
  })
  it('an existing point at the middle wins, so none is duplicated', () => {
    const a = snapAim(ctx, 400 + 2, 300 + 100 + 2, null)!
    expect(a.snap).toEqual({ kind: 'point', id: 'm' })
    expect(snapConstraints('point', [{ uv: a.uv, snap: a.snap }])).toEqual([{ type: 'coincident', refs: ['$p0', 'm'] }])
  })
  it('wins over an alignment with the anchor', () => {
    // the anchor is level with the middle: the pen is level with it, and the middle still wins
    const a = snapAim(ctx, mid1.x + 3, mid1.y, [-30, 10])!
    expect(a.snap).toEqual({ kind: 'mid', id: 'l1' })
    expect(a.infer).toBeNull()
  })
  it('a line with an end at the anchor still offers its middle', () => {
    const a = snapAim(ctx, mid1.x + 3, mid1.y + 2, [-20, 10])!
    expect(a.snap).toEqual({ kind: 'mid', id: 'l1' })
  })
  it('away from the middle the snap on the line is unchanged', () => {
    const a = snapAim(ctx, mid1.x + 60, mid1.y + 3, null)!
    expect(a.snap).toEqual({ kind: 'on', id: 'l1' })
    expect(a.uv[1]).toBeCloseTo(10, 9)
  })
  it('a mouse reaches 9 px to a middle, no further', () => {
    const m = { ...ctx, pointer: 'mouse' as const }
    expect(snapAim(m, mid1.x + 8, mid1.y, null)!.snap).toEqual({ kind: 'mid', id: 'l1' })
    expect(snapAim(m, mid1.x + 12, mid1.y, null)!.snap).toEqual({ kind: 'on', id: 'l1' })
  })
})
