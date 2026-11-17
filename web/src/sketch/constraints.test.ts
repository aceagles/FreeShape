import { expect, it } from 'vitest'
import { ConstraintStore, CONSTRAIN_TOOLS } from './constraints'
import { attachKeys } from '../input/keys'
import { sketchKeys } from './keys'
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
const enabled = (r: Rig) => CONSTRAIN_TOOLS.filter((t) => r.constraints.fits(t))
const fitted = (r: Rig, tools: string[]) => r.say({ type: 'sketch.fitted', selection: r.sent.filter((m) => m['type'] === 'sketch.fits').at(-1)!['selection'], tools })
const changed = (extra: Record<string, unknown> = {}) => {
  const data = rectAndCircle()
  return { type: 'sketch.changed', document: 'd1', tab: 't1', feature: null, name: 'Sketch 2', plane: {}, sketch: data, status: statusOf(data), can_undo: true, can_redo: false, ...extra }
}

it('the marked buttons are the tools the server lists for the selection, and change when the reply changes', () => {
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
  r.constraints.choose('horizontal')
  expect(r.sent.at(-1)).toEqual({ type: 'sketch.constrain', tool: 'horizontal', picks: ['e1'] })
  expect(r.sketch.getState().selected).toEqual([])
  // a tool the server did not list sends nothing yet: it waits for picks
  r.sketch.select({ kind: 'entity', id: 'e2' })
  const n = r.sent.length
  r.constraints.choose('vertical')
  expect(r.sent.slice(n).filter((m) => m['type'] === 'sketch.constrain')).toEqual([])
  expect(r.constraints.getState().picking).toBe('vertical')
})

it('picks go in the order they were tapped', () => {
  const r = rig()
  r.sketch.setMulti(true)
  for (const id of ['e2', 'p1', 'e1']) r.sketch.select({ kind: id.startsWith('p') ? 'point' : 'entity', id })
  fitted(r, ['symmetric'])
  r.constraints.choose('symmetric')
  expect(r.sent.at(-1)).toEqual({ type: 'sketch.constrain', tool: 'symmetric', picks: ['e2', 'p1', 'e1'] })
})

it('a conflict shows a message and leaves the drawn sketch as it was', () => {
  const r = rig()
  r.sketch.select({ kind: 'entity', id: 'e1' })
  fitted(r, ['vertical'])
  r.constraints.choose('vertical')
  const before = r.sketch.getState().view
  r.say({ ...changed({ can_undo: false }), type: 'sketch.constrained', accepted: false, made: [], conflicts: ['k9'] })
  expect(r.constraints.getState().message).toBe('Vertical conflicts with the constraints already there. Nothing changed.')
  expect(r.sketch.getState().view).toBe(before)
  // the next selection clears the message
  r.sketch.select({ kind: 'entity', id: 'e2' })
  expect(r.constraints.getState().message).toBeNull()
})

it('an "already" answer shows the note, adds nothing, and the next selection clears it', () => {
  const r = rig()
  r.sketch.select({ kind: 'entity', id: 'e1' })
  fitted(r, ['horizontal'])
  r.constraints.choose('horizontal')
  const before = r.sketch.getState().view
  r.say({ ...changed({ can_undo: false }), type: 'sketch.constrained', accepted: true, already: true, kind: 'horizontal', made: [], conflicts: [] })
  expect(r.constraints.getState().message).toBe('Already horizontal')
  expect(r.sketch.getState().view!.sketch.constraints).toEqual(before!.sketch.constraints)
  r.sketch.select({ kind: 'entity', id: 'e2' })
  expect(r.constraints.getState().message).toBeNull()
})

it('an accepted constraint replaces the sketch like any step', () => {
  const r = rig()
  r.sketch.select({ kind: 'entity', id: 'e1' })
  fitted(r, ['fix'])
  r.constraints.choose('fix')
  const data = rectAndCircle()
  data.constraints.push({ id: 'k4', type: 'fix', refs: ['p1'] })
  r.say({ ...changed({ sketch: data, status: statusOf(data) }), type: 'sketch.constrained', accepted: true, made: ['k4'], conflicts: [] })
  expect(r.sketch.getState().view!.sketch.constraints.map((c) => c.id)).toContain('k4')
  expect(r.constraints.getState().message).toBeNull()
})

it('Delete on a selected glyph sends sketch.unconstrain with the constraint id', () => {
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

const lastFits = (r: Rig) => r.sent.filter((m) => m['type'] === 'sketch.fits').at(-1)!['selection']
const constrains = (r: Rig) => r.sent.filter((m) => m['type'] === 'sketch.constrain')

it('choosing a tool with nothing selected enters pick mode with a hint, and a pick that fits makes the constraint', () => {
  const r = rig()
  r.sketch.setMulti(false)
  r.constraints.choose('coincident')
  expect(r.constraints.getState().picking).toBe('coincident')
  expect(r.sketch.getState().picking).toBe(true)
  expect(constrains(r)).toEqual([])
  expect(r.constraints.hint()).toBe('Coincident: select two points, or a point and a curve')
  // the first pick: the server says nothing fits yet
  r.sketch.select({ kind: 'point', id: 'p1' })
  r.say({ type: 'sketch.fitted', selection: lastFits(r), tools: [] })
  expect(constrains(r)).toEqual([])
  expect(r.constraints.hint()).toContain('(1 picked)')
  // a second tap adds to the selection although Multi is off
  r.sketch.select({ kind: 'point', id: 'p2' })
  expect(r.sketch.getState().selected.map((i) => i.id)).toEqual(['p1', 'p2'])
  r.say({ type: 'sketch.fitted', selection: lastFits(r), tools: ['coincident'] })
  expect(constrains(r)).toEqual([{ type: 'sketch.constrain', tool: 'coincident', picks: ['p1', 'p2'] }])
  expect(r.sketch.getState().selected).toEqual([])
  // the tool stays lit for the next one
  expect(r.constraints.getState().picking).toBe('coincident')
})

it('a selection that fits is applied at once and no pick mode starts', () => {
  const r = rig()
  r.sketch.select({ kind: 'entity', id: 'e1' })
  fitted(r, ['horizontal'])
  r.constraints.choose('horizontal')
  expect(constrains(r)).toEqual([{ type: 'sketch.constrain', tool: 'horizontal', picks: ['e1'] }])
  expect(r.constraints.getState().picking).toBeNull()
  expect(r.sketch.getState().picking).toBe(false)
})

it('a pre-selection that does not fit yet is the first pick', () => {
  const r = rig()
  r.sketch.select({ kind: 'point', id: 'p1' })
  fitted(r, ['fix'])
  r.constraints.choose('coincident')
  expect(constrains(r)).toEqual([])
  expect(r.constraints.getState().picking).toBe('coincident')
  expect(r.sketch.getState().selected.map((i) => i.id)).toEqual(['p1'])
  r.sketch.select({ kind: 'point', id: 'p2' })
  r.say({ type: 'sketch.fitted', selection: lastFits(r), tools: ['coincident'] })
  expect(constrains(r)).toEqual([{ type: 'sketch.constrain', tool: 'coincident', picks: ['p1', 'p2'] }])
})

it('picks that cannot fit say so and wait for others; a refusal shows the conflict note and stays in pick mode', () => {
  const r = rig()
  r.constraints.choose('parallel')
  r.sketch.select({ kind: 'entity', id: 'e1' })
  r.sketch.select({ kind: 'point', id: 'p1' })
  r.say({ type: 'sketch.fitted', selection: lastFits(r), tools: [] })
  expect(r.constraints.getState().message).toBe('Those do not fit Parallel. Select two lines.')
  r.sketch.select({ kind: 'point', id: 'p1' })
  r.sketch.select({ kind: 'entity', id: 'e2' })
  r.say({ type: 'sketch.fitted', selection: lastFits(r), tools: ['parallel'] })
  expect(constrains(r).at(-1)).toEqual({ type: 'sketch.constrain', tool: 'parallel', picks: ['e1', 'e2'] })
  r.say({ ...changed({ can_undo: false }), type: 'sketch.constrained', accepted: false, made: [], conflicts: ['k9'] })
  expect(r.constraints.getState().message).toBe('Parallel conflicts with the constraints already there. Nothing changed.')
  expect(r.constraints.getState().picking).toBe('parallel')
})

it('Esc, another tool and the same tool again each leave pick mode', () => {
  const r = rig()
  const off = attachKeys(sketchKeys(r.sketch, r.constraints))
  r.constraints.choose('equal')
  r.sketch.select({ kind: 'entity', id: 'e1' })
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
  expect(r.constraints.getState().picking).toBeNull()
  expect(r.sketch.getState().picking).toBe(false)
  expect(r.sketch.getState().selected).toEqual([])
  // F13's order runs only after: with nothing picking and Select on, a further Esc has nothing left to do
  r.sketch.chooseTool('line')
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
  expect(r.sketch.getState().tool).toBe('select')
  off()
  // the same tool again
  r.constraints.choose('equal')
  r.constraints.leave()
  expect(r.constraints.getState().picking).toBeNull()
  // another tool: a new tool choice in the sketch store
  r.constraints.choose('equal')
  r.sketch.chooseTool('line')
  expect(r.constraints.getState().picking).toBeNull()
  expect(r.sketch.getState().picking).toBe(false)
  // choosing a different constraint tool moves the mode
  r.sketch.chooseTool('select')
  r.constraints.choose('equal')
  r.constraints.choose('tangent')
  expect(r.constraints.getState().picking).toBe('tangent')
})
