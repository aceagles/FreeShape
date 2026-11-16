import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { OUTSIDE_MARGIN } from './drawing'
import { rig } from './testRig'
import type { Rig } from './testRig'

let r: Rig
beforeEach(() => {
  r = rig()
})
afterEach(() => r.dispose())

const close = (a: unknown, b: readonly number[], digits = 1) => {
  const p = a as number[]
  expect(p[0]).toBeCloseTo(b[0]!, digits)
  expect(p[1]).toBeCloseTo(b[1]!, digits)
}

// the open sketch is the fixture's 60 x 48 rectangle with a circle (centre 30, 24, radius 10) and a diagonal;
// the points below are in empty places of it, a pen's reach (10 px, about a millimetre) from nothing
const A: [number, number] = [5, 30]
const B: [number, number] = [25, 40]

describe('the line tool', () => {
  beforeEach(() => r.store.chooseTool('line'))

  it('down-move-up at A then down-move-up at B sends one add-line message from A to B', async () => {
    r.stroke(r.at(A))
    expect(r.adds()).toHaveLength(0)
    expect(r.mode.report()!.draw.picks).toHaveLength(1)
    r.stroke(r.at(B))
    await r.flush()
    expect(r.adds()).toHaveLength(1)
    const m = r.adds()[0]!
    expect(m['shape']).toBe('line')
    const picks = m['picks'] as number[][]
    close(picks[0]!, A)
    close(picks[1]!, B)
    expect(m['constraints']).toBeUndefined()
  })

  it('shows the aim while the pen is down and places nothing until it lifts', () => {
    r.pen('pointerdown', r.at(A))
    r.pen('pointermove', r.at(B))
    r.mode.update()
    const aim = r.mode.report()!.draw.aim!
    close(aim.uv, B)
    expect(r.parent.querySelector('[data-kind="aim"]')).not.toBeNull()
    expect(r.mode.report()!.draw.picks).toHaveLength(0)
    r.pen('pointerup', r.at(B))
    expect(r.mode.report()!.draw.picks).toHaveLength(1)
    r.mode.update()
    expect(r.parent.querySelector('[data-kind="aim"]')).toBeNull()
  })

  it('an Aim that ends on an existing point sends a coincident constraint to it, and the preview showed the snap', async () => {
    r.stroke(r.at(A))
    const p4 = [0, 48] as const
    const near = r.at([p4[0] + 0.3, p4[1] - 0.2])
    r.pen('pointerdown', near)
    r.mode.update()
    expect(r.mode.report()!.draw.aim!.snap).toBe('point')
    expect(r.parent.querySelector('[data-kind="aim"][data-snap="point"]')).not.toBeNull()
    r.pen('pointerup', near)
    await r.flush()
    const m = r.adds()[0]!
    expect(m['constraints']).toEqual([{ type: 'coincident', refs: ['$p1', 'p4'] }])
    close((m['picks'] as number[][])[1]!, p4)
  })

  it('a second point within 3 degrees of horizontal sends a horizontal constraint on the line', async () => {
    r.stroke(r.at(A))
    const dv = 20 * Math.tan((2 * Math.PI) / 180)
    r.pen('pointerdown', r.at([25, A[1] + dv]))
    r.mode.update()
    expect(r.mode.report()!.draw.aim!.infer).toBe('H')
    expect(r.parent.querySelector('[data-kind="guide"]')).not.toBeNull()
    r.pen('pointerup', r.at([25, A[1] + dv]))
    await r.flush()
    const m = r.adds()[0]!
    expect(m['constraints']).toEqual([{ type: 'horizontal', refs: ['$e0'] }])
    close((m['picks'] as number[][])[1]!, [25, A[1]]) // snapped onto the level
  })

  it('chains: the next line starts from the end the server made, and a tap of the tool ends the chain', async () => {
    r.stroke(r.at(A))
    r.stroke(r.at(B))
    await r.flush()
    r.stroke(r.at([45, 42]))
    await r.flush()
    expect(r.adds()).toHaveLength(2)
    const second = r.adds()[1]!['picks'] as { at: number[]; point: string }[]
    expect(second[0]!.point).toBe('n2') // the end point of the first line
    close(second[0]!.at, B)
    r.store.chooseTool('line')
    expect(r.mode.report()!.draw.picks).toHaveLength(0)
  })

  it('a line that closes onto an existing point ends the chain', async () => {
    r.stroke(r.at(A))
    r.stroke(r.at([0, 48]))
    await r.flush()
    expect(r.mode.report()!.draw.picks).toHaveLength(0)
  })

  it('a finger tap places nothing', async () => {
    r.fire('pointerdown', { pointerId: 3, pointerType: 'touch', clientX: r.at(A).x, clientY: r.at(A).y })
    r.fire('pointerup', { pointerId: 3, pointerType: 'touch', clientX: r.at(A).x, clientY: r.at(A).y })
    await r.flush()
    expect(r.mode.report()!.draw.picks).toHaveLength(0)
    expect(r.adds()).toHaveLength(0)
    expect(r.store.getState().selected).toEqual([])
  })

  it('a pen lifted far outside the view cancels the point in progress', async () => {
    r.stroke(r.at(A))
    r.pen('pointerdown', r.at(B))
    r.pen('pointerup', { x: -OUTSIDE_MARGIN - 10, y: 300 })
    await r.flush()
    expect(r.adds()).toHaveLength(0)
    expect(r.mode.report()!.draw.picks).toHaveLength(1) // only A
  })

  it('a point on the same place as the start makes no line', async () => {
    r.stroke(r.at(A))
    r.stroke(r.at(A))
    await r.flush()
    expect(r.adds()).toHaveLength(0)
  })
})

describe('the other drawing tools', () => {
  it('the rectangle tool sends one corner-rectangle message from two Commits', async () => {
    r.store.chooseTool('rectangle')
    r.stroke(r.at(A))
    r.stroke(r.at(B))
    await r.flush()
    expect(r.adds()).toHaveLength(1)
    expect(r.adds()[0]!['shape']).toBe('rect')
    close((r.adds()[0]!['picks'] as number[][])[1]!, B)
  })

  it('tapping Rectangle again with nothing in progress makes it a centre rectangle', async () => {
    r.store.chooseTool('rectangle')
    r.store.chooseTool('rectangle')
    expect(r.store.getState().tool).toBe('center_rectangle')
    r.stroke(r.at(A))
    r.stroke(r.at(B))
    await r.flush()
    expect(r.adds()[0]!['shape']).toBe('center_rect')
  })

  it('the circle tool sends the centre and the radius', async () => {
    r.store.chooseTool('circle')
    r.stroke(r.at(A))
    r.stroke(r.at([A[0] + 6, A[1]]))
    await r.flush()
    const m = r.adds()[0]!
    expect(m['shape']).toBe('circle')
    expect(m['picks']).toHaveLength(1)
    expect(m['radius'] as number).toBeCloseTo(6, 1)
  })

  it('the arc tool sends start, end and a point it passes through after three Commits', async () => {
    r.store.chooseTool('arc')
    r.stroke(r.at(A))
    r.stroke(r.at(B))
    expect(r.adds()).toHaveLength(0)
    r.stroke(r.at([15, 41]))
    await r.flush()
    expect(r.adds()).toHaveLength(1)
    expect(r.adds()[0]!['shape']).toBe('arc3')
    expect(r.adds()[0]!['picks']).toHaveLength(3)
  })

  it('the point tool sends one point, with a point_on when it lies on a curve', async () => {
    r.store.chooseTool('point')
    r.stroke(r.at([30, 34.2])) // on the circle (top: 30, 34)
    await r.flush()
    const m = r.adds()[0]!
    expect(m['shape']).toBe('point')
    expect(m['constraints']).toEqual([{ type: 'point_on', refs: ['$p0', 'e5'] }])
  })

  it('a refused message ends the wait: the next shape is still sent, and a refused line leaves a plain start for the next', async () => {
    r.store.chooseTool('line')
    r.refuse.on = true
    r.stroke(r.at(A))
    r.stroke(r.at(B))
    await r.flush()
    expect(r.store.getState().error).toBe('refused')
    r.refuse.on = false
    r.stroke(r.at([45, 42]))
    await r.flush()
    expect(r.adds()).toHaveLength(2)
    const start = (r.adds()[1]!['picks'] as unknown[])[0]
    expect(Array.isArray(start)).toBe(true) // the end the server never made is a plain point, not {at, point}
  })

  it('with Select on, the pen selects and draws nothing', () => {
    r.stroke(r.at([60, 0]))
    expect(r.store.getState().selected).toEqual([{ kind: 'point', id: 'p2' }])
    expect(r.adds()).toHaveLength(0)
  })

  it('switching tool drops the shape in progress', () => {
    r.store.chooseTool('line')
    r.stroke(r.at(A))
    expect(r.store.getState().drawing).toBe(1)
    r.store.chooseTool('circle')
    expect(r.mode.report()!.draw.picks).toHaveLength(0)
    expect(r.store.getState().drawing).toBe(0)
  })
})
