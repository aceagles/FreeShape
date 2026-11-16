import { act } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { SketchHit } from '../pick'
import { SketchStore } from '../store'
import type { SketchTransport } from '../store'
import { openedMessage, rectAndCircle } from '../testFixture'
import { CopyTools } from './copy'

// Mirror (card I14.2) with a fake connection. The rectangle is 60 x 48 (e1 bottom, e2 right, e3 top, e4 left) with a circle e5 of
// radius 10 at c1 (30,24), a construction diagonal e6, and a second circle e7 at c2 (10,10) added here.
const E = (id: string): SketchHit => ({ kind: 'entity', id })
const P = (id: string): SketchHit => ({ kind: 'point', id })
const D = (id: string): SketchHit => ({ kind: 'dimension', id })

function rig() {
  const data = rectAndCircle()
  data.points['c2'] = [10, 10]
  data.entities['e7'] = { type: 'circle', c: 'c2', r: 3 }
  const sent: Record<string, unknown>[] = []
  const listeners = new Set<(m: Record<string, unknown>) => void>()
  const transport: SketchTransport = {
    send: (m) => (sent.push(m), true),
    onSketch: (l) => (listeners.add(l), () => listeners.delete(l)),
    onHello: () => () => {},
  }
  const store = new SketchStore(transport)
  const tool = new CopyTools({ sketch: store, transport })
  tool.attach()
  const say = (m: Record<string, unknown>) => act(() => listeners.forEach((l) => l(m)))
  say(openedMessage(data))
  const select = (...hits: SketchHit[]) => act(() => store.setSelection(hits))
  const choose = (id: string) => act(() => store.chooseTool(id))
  const commit = (hit: SketchHit | null) => act(() => void store.commitTools(hit, [0, 0], 'pen', 1))
  const aim = (hit: SketchHit | null) => act(() => store.aimTools(hit, [0, 0], 'pen', 1))
  const ids = () => store.getState().selected.map((h) => `${h.kind}:${h.id}`)
  const mirrors = (preview: boolean) => sent.filter((m) => m['type'] === 'sketch.mirror' && (m['preview'] === true) === preview)
  const answer = (m: Record<string, unknown>) => say({ ...openedMessage(data), ...m })
  return { sent, say, store, tool, select, choose, commit, aim, ids, mirrors, answer }
}

it('chosen with two entities selected the tool keeps them; a Commit on an unselected line sends sketch.mirror, and the answer clears the selection and returns to Select', () => {
  const r = rig()
  r.select(E('e5'), E('e2'))
  r.choose('sk_mirror')
  expect(r.tool.getState().tool).toBe('sk_mirror')
  expect(r.ids()).toEqual(['entity:e5', 'entity:e2'])
  expect(r.tool.getState().hint).toBe('Mirror: tap the line to mirror about.')
  r.commit(E('e4'))
  expect(r.mirrors(false)).toHaveLength(1)
  const m = r.mirrors(false)[0]!
  expect(m).toMatchObject({ type: 'sketch.mirror', ids: ['e5', 'e2'], line: 'e4' })
  expect(m).not.toHaveProperty('preview')
  expect(m).not.toHaveProperty('construction')
  // nothing changes until the server answers
  expect(r.ids()).toEqual(['entity:e5', 'entity:e2'])
  r.answer({ type: 'sketch.changed', id: m['id'], made: { entities: ['e8', 'e9'] } })
  expect(r.ids()).toEqual([])
  expect(r.store.getState().tool).toBe('select')
  expect(r.tool.getState()).toEqual({ tool: null, hint: null })
})

it('with nothing selected a Commit on an entity selects it and a second takes it out; a circle selected takes a second circle; a Commit on nothing changes nothing', () => {
  const r = rig()
  r.choose('sk_mirror')
  expect(r.tool.getState().hint).toBe('Mirror: tap the geometry to mirror, then the mirror line.')
  r.commit(E('e5'))
  expect(r.ids()).toEqual(['entity:e5'])
  expect(r.tool.getState().hint).toBe('Mirror: tap the line to mirror about.')
  r.commit(E('e5'))
  expect(r.ids()).toEqual([])
  r.commit(E('e5'))
  r.commit(E('e7'))
  expect(r.ids()).toEqual(['entity:e5', 'entity:e7'])
  r.commit(null)
  expect(r.ids()).toEqual(['entity:e5', 'entity:e7'])
  expect(r.sent).toEqual([])
})

it('with nothing selected a Commit on a line selects it; once something is selected a line is the mirror line; a Commit on a selected line takes it out and sends nothing', () => {
  const r = rig()
  r.choose('sk_mirror')
  r.commit(E('e1'))
  expect(r.ids()).toEqual(['entity:e1'])
  expect(r.sent).toEqual([])
  r.commit(E('e1'))
  expect(r.ids()).toEqual([])
  r.select(E('e1'), E('e3'))
  r.commit(E('e3'))
  expect(r.ids()).toEqual(['entity:e1'])
  expect(r.sent).toEqual([])
})

it('a selected point and a selected dimension are not in ids, and a Commit on a point or a dimension changes nothing', () => {
  const r = rig()
  r.select(P('p1'), E('e5'), D('k3'))
  r.choose('sk_mirror')
  r.commit(P('p2'))
  r.commit(D('k3'))
  expect(r.ids()).toEqual(['point:p1', 'entity:e5', 'dimension:k3'])
  r.commit(E('e4'))
  expect(r.mirrors(false)[0]).toMatchObject({ ids: ['e5'], line: 'e4' })
  // only points selected: a line is picked as geometry, not as the mirror line
  const s = rig()
  s.select(P('p1'))
  s.choose('sk_mirror')
  s.commit(E('e4'))
  expect(s.sent).toEqual([])
  expect(s.ids()).toEqual(['point:p1', 'entity:e4'])
})

it('Aim on a line that would be the mirror line sends one preview; its answer is the copies preview; the same line again sends nothing; an older id is dropped', () => {
  const r = rig()
  r.select(E('e5'))
  r.choose('sk_mirror')
  r.aim(E('e4'))
  expect(r.mirrors(true)).toHaveLength(1)
  const first = r.mirrors(true)[0]!
  expect(first).toMatchObject({ type: 'sketch.mirror', ids: ['e5'], line: 'e4', preview: true })
  r.aim(E('e4'))
  r.aim(E('e4'))
  expect(r.mirrors(true)).toHaveLength(1)
  const outlines = [[[0, 0], [1, 1], [2, 0]]]
  r.say({ type: 'sketch.previewed', for: 'sketch.mirror', id: first['id'], made: { entities: ['e8'] }, outlines })
  expect(r.store.getState().preview).toEqual({ kind: 'copies', outlines })
  // the aim moves to another line: a new request, and the first one's late answer is dropped
  r.aim(E('e2'))
  expect(r.mirrors(true)).toHaveLength(2)
  expect(r.store.getState().preview).toBeNull()
  r.say({ type: 'sketch.previewed', for: 'sketch.mirror', id: first['id'], made: { entities: ['e8'] }, outlines })
  expect(r.store.getState().preview).toBeNull()
  r.say({ type: 'sketch.previewed', for: 'sketch.mirror', id: r.mirrors(true)[1]!['id'], made: { entities: ['e8'] }, outlines })
  expect(r.store.getState().preview?.kind).toBe('copies')
  // not a line that would be the mirror line: a selected entity, a circle, nothing
  r.aim(E('e5'))
  expect(r.store.getState().preview).toBeNull()
  r.aim(E('e2'))
  r.say({ type: 'sketch.previewed', for: 'sketch.mirror', id: r.mirrors(true)[2]!['id'], made: { entities: ['e8'] }, outlines })
  expect(r.store.getState().preview?.kind).toBe('copies')
  r.aim(null)
  expect(r.store.getState().preview).toBeNull()
  r.aim(E('e7'))
  expect(r.mirrors(true)).toHaveLength(3)
})

it('leaving the tool clears the preview, and a Commit clears it too', () => {
  const r = rig()
  r.select(E('e5'))
  r.choose('sk_mirror')
  r.aim(E('e4'))
  r.say({ type: 'sketch.previewed', for: 'sketch.mirror', id: r.mirrors(true)[0]!['id'], made: { entities: ['e8'] }, outlines: [[[0, 0], [1, 1]]] })
  expect(r.store.getState().preview?.kind).toBe('copies')
  r.choose('sk_mirror') // pressed again: back to Select
  expect(r.store.getState().tool).toBe('select')
  expect(r.store.getState().preview).toBeNull()
  expect(r.ids()).toEqual(['entity:e5'])
  // a late answer after the tool is gone draws nothing
  r.say({ type: 'sketch.previewed', for: 'sketch.mirror', id: r.mirrors(true)[0]!['id'], made: { entities: ['e8'] }, outlines: [[[0, 0], [1, 1]]] })
  expect(r.store.getState().preview).toBeNull()
  r.choose('sk_mirror')
  r.aim(E('e4'))
  r.say({ type: 'sketch.previewed', for: 'sketch.mirror', id: r.mirrors(true)[1]!['id'], made: { entities: ['e8'] }, outlines: [[[0, 0], [1, 1]]] })
  act(() => r.store.stepBack()) // Esc
  expect(r.store.getState().preview).toBeNull()
  expect(r.store.getState().tool).toBe('select')
})

it('a refusal shows the server\'s words in the hint; the tool is still on, the selection is as it was, and the refusal is not also a chip', () => {
  const r = rig()
  r.select(E('e5'))
  r.choose('sk_mirror')
  r.commit(E('e4'))
  r.say({ type: 'error', id: r.mirrors(false)[0]!['id'], for: 'sketch.mirror', message: 'line: the line has no length' })
  expect(r.tool.getState().hint).toBe('line: the line has no length')
  expect(r.store.getState().tool).toBe('sk_mirror')
  expect(r.ids()).toEqual(['entity:e5'])
  expect(r.store.getState().error).toBeNull()
  // the next pick takes the tool's own words back
  r.commit(E('e5'))
  expect(r.tool.getState().hint).toBe('Mirror: tap the geometry to mirror, then the mirror line.')
})

it('a refused preview is no refusal: no hint, no chip, no ghost', () => {
  const r = rig()
  r.select(E('e5'))
  r.choose('sk_mirror')
  r.aim(E('e4'))
  r.say({ type: 'error', id: r.mirrors(true)[0]!['id'], for: 'sketch.mirror', message: 'line: the line has no length' })
  expect(r.tool.getState().hint).toBe('Mirror: tap the line to mirror about.')
  expect(r.store.getState().error).toBeNull()
  expect(r.store.getState().preview).toBeNull()
})

it('construction mode on sends the same message as with it off', () => {
  const off = rig()
  off.select(E('e5'))
  off.choose('sk_mirror')
  off.commit(E('e4'))
  const on = rig()
  act(() => on.store.toggleConstruction())
  expect(on.store.getState().construction).toBe(true)
  on.select(E('e5'))
  on.choose('sk_mirror')
  expect(on.store.getState().construction).toBe(true)
  on.commit(E('e4'))
  const strip = (m: Record<string, unknown>) => ({ ...m, id: undefined })
  expect(strip(on.mirrors(false)[0]!)).toEqual(strip(off.mirrors(false)[0]!))
  expect(on.mirrors(false)[0]).not.toHaveProperty('construction')
})

it('the tool takes no Commit when another tool is on', () => {
  const r = rig()
  r.select(E('e5'))
  r.choose('trim')
  expect(r.store.commitTools(E('e4'), [0, 0], 'pen', 1)).toBe(false)
  expect(r.sent).toEqual([])
})
