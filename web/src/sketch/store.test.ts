import { expect, it } from 'vitest'
import { SketchStore, selectionText, statusText } from './store'
import type { SketchTransport } from './store'
import { openedMessage, rectAndCircle, statusOf, TILTED } from './testFixture'
import { parseSketchView } from './wire'

function rig() {
  const sent: Record<string, unknown>[] = []
  let sketch: (m: Record<string, unknown>) => void = () => {}
  let hello: () => void = () => {}
  let ok = true
  const transport: SketchTransport = {
    send: (m) => (ok ? (sent.push(m), true) : false),
    onSketch: (l) => ((sketch = l), () => {}),
    onHello: (l) => ((hello = l), () => {}),
  }
  const store = new SketchStore(transport)
  return { store, sent, say: (m: Record<string, unknown>) => sketch(m), hello: () => hello(), down: () => (ok = false) }
}

it('open sends sketch.open with the Document and the target; the answer opens the sketch in 2D', () => {
  const r = rig()
  r.store.open({ document: 'd1', tab: 't1', plane: { kind: 'std', name: 'Front' } })
  expect(r.sent).toEqual([{ type: 'sketch.open', document: 'd1', tab: 't1', plane: { kind: 'std', name: 'Front' } }])
  expect(r.store.getState().opening).toBe(true)
  r.say(openedMessage())
  const s = r.store.getState()
  expect(s.open).toBe(true)
  expect(s.opening).toBe(false)
  expect(s.nav).toBe('2d')
  expect(s.view?.name).toBe('Sketch 2')
  expect(Object.keys(s.view!.sketch.entities)).toHaveLength(6)
})

it('a changed message replaces the sketch and keeps the frame the open gave', () => {
  const r = rig()
  r.store.open({ document: 'd1', tab: 't1', feature: 'f1' })
  r.say(openedMessage(rectAndCircle(), TILTED))
  const data = rectAndCircle()
  delete data.entities['e5']
  r.say({ type: 'sketch.changed', document: 'd1', tab: 't1', feature: null, name: 'Sketch 2', plane: {}, sketch: data, status: statusOf(data), can_undo: true, can_redo: false })
  const v = r.store.getState().view!
  expect(v.frame).toEqual(TILTED)
  expect(v.canUndo).toBe(true)
  expect(v.sketch.entities['e5']).toBeUndefined()
})

it('an item a step removed is dropped from the selection', () => {
  const r = rig()
  r.store.open({ document: 'd1', tab: 't1', feature: 'f1' })
  r.say(openedMessage())
  r.store.setMulti(true)
  r.store.select({ kind: 'entity', id: 'e5' })
  r.store.select({ kind: 'point', id: 'p1' })
  const data = rectAndCircle()
  delete data.entities['e5']
  r.say({ type: 'sketch.changed', document: 'd1', tab: 't1', feature: null, name: 'S', plane: {}, sketch: data, status: statusOf(data), can_undo: true, can_redo: false })
  expect(r.store.getState().selected).toEqual([{ kind: 'point', id: 'p1' }])
})

it('selection: single mode replaces, Multi toggles, a tap on nothing clears in single mode only', () => {
  const r = rig()
  r.store.open({ document: 'd1', tab: 't1', feature: 'f1' })
  r.say(openedMessage())
  r.store.select({ kind: 'point', id: 'p1' })
  r.store.select({ kind: 'point', id: 'p2' })
  expect(r.store.getState().selected).toEqual([{ kind: 'point', id: 'p2' }])
  r.store.select(null)
  expect(r.store.getState().selected).toEqual([])
  r.store.setMulti(true)
  r.store.select({ kind: 'point', id: 'p1' })
  r.store.select({ kind: 'entity', id: 'e1' })
  r.store.select(null)
  expect(r.store.getState().selected).toHaveLength(2)
  r.store.select({ kind: 'point', id: 'p1' })
  expect(r.store.getState().selected).toEqual([{ kind: 'entity', id: 'e1' }])
})

it('commit and cancel close the sketch; an error is kept as a sentence and the sketch stays open', () => {
  const r = rig()
  r.store.open({ document: 'd1', tab: 't1', feature: 'f1' })
  r.say(openedMessage())
  r.store.commit()
  expect(r.sent.at(-1)).toEqual({ type: 'sketch.commit' })
  r.say({ type: 'error', for: 'sketch.commit', message: 'The Document is read-only.' })
  expect(r.store.getState().open).toBe(true)
  expect(r.store.getState().error).toBe('The Document is read-only.')
  r.say({ type: 'sketch.committed', document: 'd1', tab: 't1', feature: 'f9', rebuilt: [], revision: 4 })
  expect(r.store.getState().open).toBe(false)
  expect(r.store.getState().committed).toBe(4)
  r.store.open({ document: 'd1', tab: 't1', feature: 'f1' })
  r.say(openedMessage())
  r.store.cancel()
  expect(r.sent.at(-1)).toEqual({ type: 'sketch.cancel' })
  r.say({ type: 'sketch.cancelled', document: 'd1', tab: 't1', feature: null })
  expect(r.store.getState().open).toBe(false)
})

it('a reconnect asks for the sketch again, and the answer keeps the mode and the selection', () => {
  const r = rig()
  r.store.open({ document: 'd1', tab: 't1', feature: 'f1' })
  r.say(openedMessage())
  r.store.setNav('3d')
  r.store.select({ kind: 'point', id: 'p1' })
  r.hello()
  expect(r.sent.at(-1)).toEqual({ type: 'sketch.state' })
  const state: Record<string, unknown> = { ...openedMessage(), type: 'sketch.state' }
  delete state['revision']
  r.say(state)
  expect(r.store.getState().nav).toBe('3d')
  expect(r.store.getState().selected).toEqual([{ kind: 'point', id: 'p1' }])
})

it('with the connection down, opening says so', () => {
  const r = rig()
  r.down()
  r.store.open({ document: 'd1', tab: 't1', feature: 'f1' })
  expect(r.store.getState().opening).toBe(false)
  expect(r.store.getState().error).toMatch(/connection/)
})

it('words for the selection and the solve status', () => {
  expect(selectionText([])).toBe('Nothing selected')
  expect(selectionText([{ kind: 'point', id: 'a' }])).toBe('1 point')
  expect(selectionText([{ kind: 'point', id: 'a' }, { kind: 'point', id: 'b' }, { kind: 'entity', id: 'c' }])).toBe('2 points, 1 item')
  const data = rectAndCircle()
  const view = (st: ReturnType<typeof statusOf>) => parseSketchView(openedMessage(data, undefined, { status: st }))!
  expect(statusText(view(statusOf(data, 'free')))).toEqual({ text: '6 items free', tone: 'free' })
  expect(statusText(view(statusOf(data, 'defined')))).toEqual({ text: 'Fully defined', tone: 'defined' })
  expect(statusText(view(statusOf(data, 'free', { e1: 'conflicting' })))).toEqual({ text: '1 conflicting', tone: 'conflicting' })
  const bad = statusOf(data, 'defined', { e1: 'conflicting' })
  bad.constraints['k1'] = 'conflicting'
  expect(statusText(view(bad))).toEqual({ text: '2 conflicting', tone: 'conflicting' })
})

it('parseSketchView refuses a message without a sketch or a frame', () => {
  expect(parseSketchView({ type: 'sketch.opened', document: 'd', tab: 't' })).toBeNull()
  const m = openedMessage()
  delete m['frame']
  expect(parseSketchView(m)).toBeNull()
})
