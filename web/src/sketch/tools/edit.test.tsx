import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { FilletPad } from '../EditPad'
import { SketchStore } from '../store'
import type { SketchTransport } from '../store'
import { openedMessage, rectAndCircle } from '../testFixture'
import { EditTools, edgeAt } from './edit'
import { cornerOf, trimPiece } from './trim'

afterEach(cleanup)

// Trim, Corner fillet and Use edge (card I7.12) with a fake connection. The rectangle is 60 x 48 (p1 0,0 / p2 60,0 / p3 60,48 /
// p4 0,48; e1 bottom, e2 right, e3 top, e4 left) with a circle e5 of radius 10 at c1 (30,24) and a construction diagonal e6.
// A model edge runs along v = -5 from u = -10 to 70.
const EDGES = [{ pick: { part: 'P1', index: 7 }, points: [[-10, -5], [70, -5]] as [number, number][], height: 0 }]

function rig(extra: Record<string, unknown> = { edges: EDGES }, data = rectAndCircle()) {
  const sent: Record<string, unknown>[] = []
  const listeners = new Set<(m: Record<string, unknown>) => void>()
  const transport: SketchTransport = {
    send: (m) => (sent.push(m), true),
    onSketch: (l) => (listeners.add(l), () => listeners.delete(l)),
    onHello: () => () => {},
  }
  const store = new SketchStore(transport)
  const dock = { opened: 0, closed: 0 }
  const tool = new EditTools({ sketch: store, transport, openPad: () => dock.opened++, closePad: () => dock.closed++ })
  tool.attach()
  const say = (m: Record<string, unknown>) => act(() => listeners.forEach((l) => l(m)))
  say(openedMessage(data, undefined, extra))
  render(<FilletPad tool={tool} variables={[]} />)
  const choose = (id: string) => act(() => store.chooseTool(id))
  const commit = (hit: { kind: 'point' | 'entity'; id: string } | null, uv: [number, number] | null, pointer: 'pen' | 'touch' | 'mouse' = 'touch') =>
    act(() => void store.commitTools(hit, uv, pointer, 1))
  const aim = (hit: { kind: 'point' | 'entity'; id: string } | null, uv: [number, number] | null) => act(() => store.aimTools(hit, uv, 'pen', 1))
  const tap = (...names: string[]) => names.forEach((n) => act(() => screen.getByRole('button', { name: n }).click()))
  return { sent, say, store, tool, dock, choose, commit, aim, tap }
}

it('Trim: a Commit on a line between two crossings sends sketch.trim with that entity and the point', () => {
  const r = rig()
  r.choose('trim')
  expect(r.tool.getState().tool).toBe('trim')
  r.commit({ kind: 'entity', id: 'e1' }, [25, 0])
  expect(r.sent).toEqual([{ type: 'sketch.trim', entity: 'e1', at: [25, 0] }])
  // nothing is selected by a Commit with the tool on
  expect(r.store.getState().selected).toEqual([])
})

it('Trim: Aim marks the piece between the crossings; a point or nothing is no trim and says what to tap', () => {
  const r = rig()
  r.choose('trim')
  // the circle (centre 30,24, radius 10) and the construction diagonal cross e1? no: only e6 meets e1 at its end. Use e6: it crosses the circle twice
  r.aim({ kind: 'entity', id: 'e6' }, [30, 24])
  const p = r.store.getState().preview
  expect(p?.kind).toBe('trim')
  if (p?.kind === 'trim') {
    // the diagonal runs from (0,0) to (60,48); the circle cuts it 10 from the centre either side
    const dir = 10 / Math.hypot(30, 24)
    expect(p.from[0]).toBeCloseTo(30 - 30 * dir, 5)
    expect(p.to[0]).toBeCloseTo(30 + 30 * dir, 5)
  }
  r.aim(null, [5, 5])
  expect(r.store.getState().preview).toBeNull()
  r.commit({ kind: 'point', id: 'p2' }, [60, 0])
  r.commit(null, [5, 5])
  expect(r.sent).toEqual([])
  expect(r.tool.getState().hint).toContain('Tap on the line')
})

it('trimPiece: a line with no crossing goes whole, a circle goes whole, an arc crossing counts only within the arc', () => {
  const sk = rectAndCircle()
  const whole = trimPiece(sk, 'e1', [10, 0])
  expect(whole).toEqual({ whole: false, from: [0, 0], to: [60, 0] })
  expect(trimPiece(sk, 'e5', [40, 24])).toEqual({ whole: true })
  expect(trimPiece(sk, 'nope', [0, 0])).toBeNull()
  sk.points['a1'] = [10, 20]
  sk.points['a2'] = [10, 28]
  sk.points['ac'] = [0, 24]
  sk.entities['arc'] = { type: 'arc', c: 'ac', p1: 'a1', p2: 'a2' }
  // the arc (centre 0,24; radius about 10.2) starts below the diagonal's crossing and ends above: it meets e6 at most once
  const piece = trimPiece(sk, 'e6', [0.5, 0.4])
  expect(piece && !piece.whole && piece.from).toEqual([0, 0])
})

it('Corner fillet: a Commit on a point shared by two lines opens the pad; Enter with 3 sends sketch.fillet with radius 3', () => {
  const r = rig()
  r.choose('corner_fillet')
  r.commit({ kind: 'point', id: 'p2' }, [60, 0])
  expect(r.dock.opened).toBe(1)
  expect(r.tool.getState().pad?.label).toBe('Radius')
  expect(r.store.getState().selected).toEqual([{ kind: 'point', id: 'p2' }])
  r.tap('3')
  r.tap('Enter')
  expect(r.sent).toEqual([{ type: 'sketch.fillet', point: 'p2', radius: 3 }])
  // the answer closes the pad and drops the selection
  r.say({ ...openedMessage(), type: 'sketch.changed', made: { arc: 'e9', constraint: 'k9' } })
  expect(r.tool.getState().pad).toBeNull()
  expect(r.dock.closed).toBe(1)
  expect(r.store.getState().selected).toEqual([])
})

it('Corner fillet: a refusal keeps the pad open with the server words; Cancel sends nothing', () => {
  const r = rig()
  r.choose('corner_fillet')
  r.commit({ kind: 'point', id: 'p4' }, [0, 48])
  r.tap('9', '9')
  r.tap('Enter')
  r.say({ type: 'error', for: 'sketch.fillet', message: 'Radius is too large for these lines' })
  expect(r.tool.getState().pad?.error).toBe('Radius is too large for these lines')
  expect(r.tool.getState().pad?.pending).toBe(false)
  expect(screen.getByTestId('fillet-refusal').textContent).toContain('too large')
  r.tap('Cancel')
  expect(r.tool.getState().pad).toBeNull()
  expect(r.sent.filter((m) => m['type'] === 'sketch.fillet')).toHaveLength(1)
})

it('Corner fillet: Enter on the opening radius sends it as a number; a point that is not a corner opens nothing', () => {
  const r = rig()
  r.choose('corner_fillet')
  r.commit({ kind: 'point', id: 'c1' }, [30, 24])
  r.commit({ kind: 'entity', id: 'e1' }, [30, 0])
  expect(r.dock.opened).toBe(0)
  expect(r.tool.getState().hint).toContain('not a corner')
  r.commit({ kind: 'point', id: 'p4' }, [0, 48])
  expect(r.tool.getState().pad?.initial).toBe('5 mm')
  r.tap('Enter')
  expect(r.sent).toEqual([{ type: 'sketch.fillet', point: 'p4', radius: 5 }])
})

it('cornerOf: two lines only; a third user, a circle centre or an unknown point is no corner', () => {
  const sk = rectAndCircle()
  expect(cornerOf(sk, 'p2')).toBe(48)
  expect(cornerOf(sk, 'p1')).toBeNull() // the diagonal meets there too
  expect(cornerOf(sk, 'c1')).toBeNull()
  expect(cornerOf(sk, 'zz')).toBeNull()
})

it('Use edge: a Commit on a model edge sends sketch.use_edge with that edge pick', () => {
  const r = rig()
  r.choose('use_edge')
  r.commit(null, [20, -4.5])
  expect(r.sent).toEqual([{ type: 'sketch.use_edge', pick: { part: 'P1', index: 7 } }])
})

it('Use edge: Aim marks the edge and a Commit away from any edge says so and sends nothing', () => {
  const r = rig()
  r.choose('use_edge')
  r.aim(null, [20, -4])
  expect(r.store.getState().preview).toEqual({ kind: 'edge', points: EDGES[0]!.points })
  r.aim(null, [20, 30])
  expect(r.store.getState().preview).toBeNull()
  r.commit(null, [20, 30])
  expect(r.sent).toEqual([])
  expect(r.tool.getState().hint).toContain('No model edge')
})

it('edgeAt: the reach is the pointer reach on screen, so zooming out widens it in mm', () => {
  expect(edgeAt(EDGES, [20, -2], 'touch', 0.1)).toBeNull()
  expect(edgeAt(EDGES, [20, -2], 'touch', 1)?.pick).toEqual({ part: 'P1', index: 7 })
  expect(edgeAt(EDGES, [20, 8], 'pen', 1)).toBeNull()
})

it('with another tool on, the tools take nothing and a tool change drops an open pad', () => {
  const r = rig()
  r.commit({ kind: 'entity', id: 'e1' }, [25, 0])
  expect(r.sent).toEqual([])
  r.choose('corner_fillet')
  r.commit({ kind: 'point', id: 'p2' }, [60, 0])
  expect(r.tool.getState().pad).not.toBeNull()
  r.choose('line')
  expect(r.tool.getState().pad).toBeNull()
  expect(r.dock.closed).toBe(1)
  expect(r.tool.getState().tool).toBeNull()
})

it('choosing Select again (as a Constrain tool does) keeps the selection', () => {
  const r = rig()
  act(() => r.store.setSelection([{ kind: 'entity', id: 'e1' }]))
  r.choose('select')
  expect(r.store.getState().selected).toEqual([{ kind: 'entity', id: 'e1' }])
})
