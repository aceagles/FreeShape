import { expect, it } from 'vitest'
import { ConstraintStore, CONSTRAIN_TOOLS } from './constraints'
import { SketchStore } from './store'
import type { SketchTransport } from './store'
import { openedMessage, rectAndCircle, statusOf } from './testFixture'

function rig() {
  const sent: Record<string, unknown>[] = []
  const ears = new Set<(m: Record<string, unknown>) => void>()
  const heard = (m: Record<string, unknown>) => ears.forEach((l) => l(m))
  const transport: SketchTransport = {
    send: (m) => (sent.push(m), true),
    onSketch: (l) => (ears.add(l), () => ears.delete(l)),
    onHello: () => () => {},
  }
  const sketch = new SketchStore(transport)
  const constraints = new ConstraintStore(sketch, transport)
  sketch.open({ document: 'd1', tab: 't1', feature: 'f1' })
  heard(openedMessage())
  sent.length = 0
  return { sketch, constraints, sent, say: (m: Record<string, unknown>) => heard(m) }
}
type Rig = ReturnType<typeof rig>
const enabled = (r: Rig) => CONSTRAIN_TOOLS.filter((t) => r.constraints.stateOf(t) === 'ok')
const fitted = (r: Rig, tools: string[]) => r.say({ type: 'sketch.fitted', selection: r.sent.filter((m) => m['type'] === 'sketch.fits').at(-1)!['selection'], tools })
const changed = (extra: Record<string, unknown> = {}) => {
  const data = rectAndCircle()
  return { type: 'sketch.changed', document: 'd1', tab: 't1', feature: null, name: 'Sketch 2', plane: {}, sketch: data, status: statusOf(data), can_undo: true, can_redo: false, ...extra }
}

it('the enabled buttons are the tools the server lists for the selection, and change when the reply changes', () => {
  const r = rig()
  expect(enabled(r)).toEqual([])
  r.sketch.select({ kind: 'entity', id: 'e1' })
  expect(r.sent).toEqual([{ type: 'sketch.fits', selection: ['e1'] }])
  expect(enabled(r)).toEqual([])
  fitted(r, ['horizontal', 'vertical', 'fix'])
  expect(enabled(r)).toEqual(['horizontal', 'vertical', 'fix'])
  r.sketch.setMulti(true)
  r.sketch.select({ kind: 'entity', id: 'e3' })
  expect(r.sent.at(-1)).toEqual({ type: 'sketch.fits', selection: ['e1', 'e3'] })
  fitted(r, ['parallel', 'perpendicular', 'equal'])
  expect(enabled(r)).toEqual(['parallel', 'perpendicular', 'equal'])
  // a reply for an older selection is ignored
  r.say({ type: 'sketch.fitted', selection: ['e1'], tools: ['fix'] })
  expect(enabled(r)).toEqual(['parallel', 'perpendicular', 'equal'])
  r.sketch.clearSelection()
  expect(enabled(r)).toEqual([])
})

it('tapping Horizontal with a line selected sends sketch.constrain with the tool name and that line, and clears the selection', () => {
  const r = rig()
  r.sketch.select({ kind: 'entity', id: 'e1' })
  fitted(r, ['horizontal'])
  r.constraints.apply('horizontal')
  expect(r.sent.at(-1)).toEqual({ type: 'sketch.constrain', tool: 'horizontal', picks: ['e1'] })
  expect(r.sketch.getState().selected).toEqual([])
  // a tool the server did not list sends nothing
  r.sketch.select({ kind: 'entity', id: 'e2' })
  const n = r.sent.length
  r.constraints.apply('vertical')
  expect(r.sent).toHaveLength(n)
})

it('picks go in the order they were tapped', () => {
  const r = rig()
  r.sketch.setMulti(true)
  for (const id of ['e2', 'p1', 'e1']) r.sketch.select({ kind: id.startsWith('p') ? 'point' : 'entity', id })
  fitted(r, ['symmetric'])
  r.constraints.apply('symmetric')
  expect(r.sent.at(-1)).toEqual({ type: 'sketch.constrain', tool: 'symmetric', picks: ['e2', 'p1', 'e1'] })
})

it('a conflict shows a message and leaves the drawn sketch as it was', () => {
  const r = rig()
  r.sketch.select({ kind: 'entity', id: 'e1' })
  fitted(r, ['vertical'])
  r.constraints.apply('vertical')
  const before = r.sketch.getState().view
  r.say({ ...changed({ can_undo: false }), type: 'sketch.constrained', accepted: false, made: [], conflicts: ['k9'] })
  expect(r.constraints.getState().message).toBe('Vertical conflicts with the constraints already there. Nothing changed.')
  expect(r.sketch.getState().view).toBe(before)
  // the next selection clears the message
  r.sketch.select({ kind: 'entity', id: 'e2' })
  expect(r.constraints.getState().message).toBeNull()
})

it('an accepted constraint replaces the sketch like any step', () => {
  const r = rig()
  r.sketch.select({ kind: 'entity', id: 'e1' })
  fitted(r, ['fix'])
  r.constraints.apply('fix')
  const data = rectAndCircle()
  data.constraints.push({ id: 'k4', type: 'fix', refs: ['p1'] })
  r.say({ ...changed({ sketch: data, status: statusOf(data) }), type: 'sketch.constrained', accepted: true, made: ['k4'], conflicts: [] })
  expect(r.sketch.getState().view!.sketch.constraints.map((c) => c.id)).toContain('k4')
  expect(r.constraints.getState().message).toBeNull()
})

it('Delete on a selected glyph sends sketch.unconstrain with the constraint id', async () => {
  const { attachKeys } = await import('../input/keys')
  const r = rig()
  const off = attachKeys({ delete: () => r.constraints.removeSelected() })
  r.sketch.select({ kind: 'constraint', id: 'k1' })
  // a constraint is not a pick: no fits question
  expect(r.sent.some((m) => m['type'] === 'sketch.fits')).toBe(false)
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete' }))
  expect(r.sent.at(-1)).toEqual({ type: 'sketch.unconstrain', constraint: 'k1' })
  off()
  r.sent.length = 0
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete' }))
  expect(r.sent).toEqual([])
})
