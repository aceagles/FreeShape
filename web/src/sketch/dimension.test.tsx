import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { DimensionTool, dimensionSpec, valueOf } from './dimension'
import { DimensionPad } from './DimensionPad'
import { SketchStore } from './store'
import type { SketchTransport } from './store'
import { openedMessage, rectAndCircle } from './testFixture'

afterEach(cleanup)

// The Dimension tool (card I7.11) with a fake connection: what it sends for the picks and the label's place, and how the pad
// ends. The rectangle is 60 x 48 with a circle of radius 10; e1 is its bottom line, e2 its right line, k3 the 60 mm dimension.
function rig() {
  const sent: Record<string, unknown>[] = []
  const listeners = new Set<(m: Record<string, unknown>) => void>()
  const transport: SketchTransport = {
    send: (m) => (sent.push(m), true),
    onSketch: (l) => (listeners.add(l), () => listeners.delete(l)),
    onHello: () => () => {},
  }
  const store = new SketchStore(transport)
  let t = 1000
  const dock = { opened: 0, closed: 0 }
  const tool = new DimensionTool({ sketch: store, transport, openPad: () => dock.opened++, closePad: () => dock.closed++, now: () => t })
  tool.attach()
  const say = (m: Record<string, unknown>) => act(() => listeners.forEach((l) => l(m)))
  say(openedMessage())
  render(<DimensionPad tool={tool} variables={['width']} />)
  const tap = (...names: string[]) => names.forEach((n) => act(() => screen.getByRole('button', { name: n }).click()))
  const field = () => screen.getByTestId('pad-field').textContent
  const pick = (kind: 'point' | 'entity' | 'dimension', id: string, uv: [number, number] | null = [30, 24], pointer: 'pen' | 'touch' | 'mouse' = 'pen') =>
    act(() => void tool.commit({ kind, id }, uv, pointer))
  const place = (uv: [number, number]) => act(() => void tool.commit(null, uv, 'pen'))
  const changed = (made: Record<string, unknown>) => say({ ...openedMessage(), type: 'sketch.changed', made })
  return { sent, say, store, tool, dock, tap, field, pick, place, changed, advance: (ms: number) => (t += ms) }
}

it('a line, then a Commit to place, 25 and Enter sends a distance of 25 on that line', () => {
  const r = rig()
  act(() => r.tool.setActive(true))
  r.pick('entity', 'e1')
  expect(r.store.getState().selected).toEqual([{ kind: 'entity', id: 'e1' }])
  expect(r.tool.getState().pad).toBeNull()
  r.place([30, -12])
  expect(r.dock.opened).toBe(1)
  // the pad opens on the measured length, selected: typing replaces it
  expect(r.field()).toBe('60 mm')
  r.tap('2', '5')
  r.tap('Enter')
  expect(r.sent).toEqual([{ type: 'sketch.dimension', picks: ['e1'], pos: [30, -12], value: 25 }])
  // the answer closes the pad and starts the next dimension
  r.changed({ constraint: 'k4', type: 'distance', value: 25 })
  expect(r.tool.getState().pad).toBeNull()
  expect(r.dock.closed).toBe(1)
  expect(r.store.getState().selected).toEqual([])
  expect(r.tool.getState().hint).toContain('Pick a line')
})

it('Enter on the measured value sends no value, so the server keeps the exact measure; Cancel sends nothing', () => {
  const r = rig()
  act(() => r.tool.setActive(true))
  r.pick('entity', 'e1')
  r.place([30, -12])
  r.tap('Enter')
  expect(r.sent).toEqual([{ type: 'sketch.dimension', picks: ['e1'], pos: [30, -12] }])
  r.changed({ constraint: 'k4', type: 'distance', value: 60 })
  r.pick('entity', 'e5')
  r.place([50, 40])
  r.tap('Cancel')
  expect(r.sent).toHaveLength(1)
  expect(r.tool.getState().pad).toBeNull()
  expect(r.store.getState().selected).toEqual([])
})

it('two points with the label to the side send a vertical distance, above a horizontal one, between them a plain distance', () => {
  const kinds = (uv: [number, number]) => {
    const r = rig()
    act(() => r.tool.setActive(true))
    r.pick('point', 'p1', [0, 0])
    r.pick('point', 'p3', [60, 48])
    expect(r.tool.getState().picks.map((p) => p.id)).toEqual(['p1', 'p3'])
    r.place(uv)
    const label = screen.getByTestId('pad-field').parentElement!.querySelector('.pad-field-label')!.textContent
    const shown = r.field()
    r.tap('Enter')
    return { msg: r.sent[0], label, shown }
  }
  expect(kinds([90, 24])).toEqual({ msg: { type: 'sketch.dimension', picks: ['p1', 'p3'], pos: [90, 24], kind: 'vdistance' }, label: 'Vertical distance', shown: '48 mm' })
  cleanup()
  expect(kinds([30, 70])).toEqual({ msg: { type: 'sketch.dimension', picks: ['p1', 'p3'], pos: [30, 70], kind: 'hdistance' }, label: 'Horizontal distance', shown: '60 mm' })
  cleanup()
  expect(kinds([90, 70])).toEqual({ msg: { type: 'sketch.dimension', picks: ['p1', 'p3'], pos: [90, 70] }, label: 'Distance', shown: '76.837 mm' })
})

it('a circle gives a diameter, an arc a radius, two lines an angle in degrees', () => {
  const sk = rectAndCircle()
  expect(dimensionSpec(sk, 'e5', null, null)).toEqual({ type: 'diameter', value: 20 })
  expect(dimensionSpec(sk, 'e1', 'e2', null)).toEqual({ type: 'angle', value: 90 })
  expect(dimensionSpec(sk, 'e1', 'e3', null)?.type).toBe('distance')
  expect(dimensionSpec(sk, 'e1', null, null)).toEqual({ type: 'distance', value: 60 })
  expect(dimensionSpec(sk, 'p3', 'e1', null)).toEqual({ type: 'distance', value: 48 })
  expect(dimensionSpec(sk, 'c1', 'p1', null)?.type).toBe('distance')
  expect(dimensionSpec(sk, 'p1', null, null)).toBeNull()
  sk.entities['a1'] = { type: 'arc', c: 'c1', p1: 'p2', p2: 'p3' }
  expect(dimensionSpec(sk, 'a1', null, null)?.type).toBe('radius')

  const r = rig()
  act(() => r.tool.setActive(true))
  r.pick('entity', 'e5')
  r.place([50, 40])
  expect(r.field()).toBe('20 mm')
  r.tap('Enter')
  expect(r.sent.at(-1)).toEqual({ type: 'sketch.dimension', picks: ['e5'], pos: [50, 40] })
  r.changed({ constraint: 'k5', type: 'diameter', value: 20 })
  r.pick('entity', 'e1')
  r.pick('entity', 'e2')
  expect(r.tool.getState().picks).toHaveLength(2)
  r.place([70, -10])
  expect(r.field()).toBe('90 deg')
  r.tap('4', '5')
  r.tap('Enter')
  expect(r.sent.at(-1)).toEqual({ type: 'sketch.dimension', picks: ['e1', 'e2'], pos: [70, -10], value: 45 })
})

it('a double tap on a label opens the pad on its value; 2*10 goes to sketch.dimension.set as that text', () => {
  const r = rig()
  // with the Select tool on: the tool takes no single Commit, but a double tap works
  expect(r.tool.commit({ kind: 'dimension', id: 'k3' }, null, 'touch')).toBe(false)
  act(() => void r.tool.doubleTap({ kind: 'dimension', id: 'k3' }))
  expect(r.field()).toBe('60 mm')
  r.tap('2', 'Multiply', '1', '0')
  r.tap('Enter')
  expect(r.sent).toEqual([{ type: 'sketch.dimension.set', constraint: 'k3', value: '2*10' }])
  r.changed({ constraint: 'k3', value: 20 })
  expect(r.tool.getState().pad).toBeNull()
})

it('a pen double Commit on a label does the same, and leaving the value alone sends nothing', () => {
  const r = rig()
  r.pick('dimension', 'k3', null)
  expect(r.tool.getState().pad).toBeNull()
  r.advance(500)
  r.pick('dimension', 'k3', null)
  expect(r.tool.getState().pad).toBeNull()
  r.advance(100)
  r.pick('dimension', 'k3', null)
  expect(r.tool.getState().pad?.mode).toBe('set')
  r.tap('Enter')
  expect(r.sent).toEqual([])
  expect(r.tool.getState().pad).toBeNull()
})

it('a refusal shows the server words, keeps the pad open on what was typed and the sketch as it was', () => {
  const r = rig()
  act(() => void r.tool.doubleTap({ kind: 'dimension', id: 'k3' }))
  r.tap('0', 'Enter')
  expect(r.sent).toEqual([{ type: 'sketch.dimension.set', constraint: 'k3', value: 0 }])
  r.say({ type: 'error', for: 'sketch.dimension.set', message: 'dimension.value: 0 must be greater than 0' })
  expect(screen.getByTestId('dimension-refusal').textContent).toBe('dimension.value: 0 must be greater than 0')
  expect(r.tool.getState().pad?.pending).toBe(false)
  expect(r.field()).toBe('0')
  // the pad takes another try
  r.tap('Backspace', '3', '0', 'Enter')
  expect(r.sent.at(-1)).toEqual({ type: 'sketch.dimension.set', constraint: 'k3', value: 30 })
  r.changed({ constraint: 'k3', value: 30 })
  expect(screen.queryByTestId('dimension-refusal')).toBeNull()
  expect(r.tool.getState().pad).toBeNull()
})

it('a point alone cannot be placed; the end of a line is not a second item', () => {
  const r = rig()
  act(() => r.tool.setActive(true))
  r.pick('point', 'p1', [0, 0])
  r.place([5, 5])
  expect(r.tool.getState().pad).toBeNull()
  expect(r.tool.getState().hint).toContain('second')
  act(() => r.tool.setActive(false))
  r.pick('entity', 'e1')
  r.pick('point', 'p1', [0, 0])
  // Select is on: the tool took none of these
  expect(r.tool.getState().picks).toEqual([])
  act(() => r.tool.setActive(true))
  r.pick('entity', 'e1')
  r.pick('point', 'p2', [60, 0])
  expect(r.tool.getState().picks).toHaveLength(1)
  expect(r.tool.getState().pad?.mode).toBe('new')
})

it('closing the dialog area under the pad gives the dimension up; the sketch ending clears the tool', () => {
  const r = rig()
  act(() => r.tool.setActive(true))
  r.pick('entity', 'e1')
  r.place([30, -12])
  act(() => r.tool.padGone())
  expect(r.tool.getState().pad).toBeNull()
  expect(r.store.getState().selected).toEqual([])
  expect(r.sent).toEqual([])
  r.say({ type: 'sketch.cancelled', document: 'd1', tab: 't1', feature: null })
  expect(r.tool.getState().active).toBe(false)
})

it('plain numbers go as numbers, anything else as the typed text', () => {
  expect(valueOf(' 25 ')).toBe(25)
  expect(valueOf('2.5')).toBe(2.5)
  expect(valueOf('25 mm')).toBe('25 mm')
  expect(valueOf('2*10')).toBe('2*10')
  expect(valueOf('#width/2')).toBe('#width/2')
})

it('a refusal is shown by the pad alone: the sketch chip does not say it again', () => {
  const r = rig()
  act(() => void r.tool.doubleTap({ kind: 'dimension', id: 'k3' }))
  expect(screen.getByTestId('pad-field').parentElement!.querySelector('.pad-field-label')!.textContent).toBe('Length')
  r.tap('0', 'Enter')
  r.say({ type: 'error', for: 'sketch.dimension.set', message: 'dimension.value: must be more than 0' })
  expect(r.store.getState().error).toBeNull()
  expect(screen.getByTestId('dimension-refusal')).toBeTruthy()
})

it('a dimension made from an expression opens the pad on that text, not on its number', () => {
  const r = rig()
  const open = openedMessage()
  const sk = open['sketch'] as { constraints: Record<string, unknown>[] }
  sk.constraints = sk.constraints.map((c) => (c['id'] === 'k3' ? { ...c, value: 40, expr: '#w*2' } : c))
  r.say(open)
  act(() => void r.tool.doubleTap({ kind: 'dimension', id: 'k3' }))
  expect(r.field()).toBe('#w*2')
  // leaving it alone sends nothing; typing over it sends the new text
  r.tap('Enter')
  expect(r.sent).toEqual([])
  act(() => void r.tool.doubleTap({ kind: 'dimension', id: 'k3' }))
  r.tap('3', 'Multiply', '4')
  r.tap('Enter')
  expect(r.sent).toEqual([{ type: 'sketch.dimension.set', constraint: 'k3', value: '3*4' }])
})
