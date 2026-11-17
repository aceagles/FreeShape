import { act } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { SketchHit } from '../pick'
import { SketchStore } from '../store'
import type { SketchTransport } from '../store'
import { openedMessage, rectAndCircle } from '../testFixture'
import { CopyTools, patternFloor } from './copy'

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
  const panel = { opened: 0, closed: 0 }
  const tool = new CopyTools({ sketch: store, transport, openPanel: () => panel.opened++, closePanel: () => panel.closed++, variables: () => ['gap'] })
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
  const commitAt = (hit: SketchHit | null, uv: [number, number], mmPerPx = 0.1) => act(() => void store.commitTools(hit, uv, 'pen', mmPerPx))
  const of = (type: string, preview: boolean) => sent.filter((m) => m['type'] === type && (m['preview'] === true) === preview)
  return { sent, say, store, tool, select, choose, commit, aim, ids, mirrors, answer, commitAt, of, panel }
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
  expect(r.tool.getState()).toEqual({ tool: null, hint: null, panel: null })
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

// ---------------------------------------------------------------------------------------------- the patterns (card I14.3)

const LP = 'sketch.linear_pattern'
const CP = 'sketch.circular_pattern'

/** A circle selected, Linear pattern on, a Commit on the bottom line e1 at (70, -2); the panel open. */
function linear() {
  const r = rig()
  r.select(E('e5'))
  r.choose('sk_lpattern')
  r.commitAt(E('e1'), [70, -2])
  return r
}
const previewed = (r: ReturnType<typeof rig>, m: Record<string, unknown>, made: Record<string, unknown>, outlines: unknown = [[[0, 0], [1, 1]]]) =>
  r.say({ type: 'sketch.previewed', for: m['type'], id: m['id'], made, outlines })
const edit = (r: ReturnType<typeof rig>, key: 'count' | 'second', text: string) => act(() => r.tool.edit(key, text))

it('linear: with a circle selected, a Commit on an unselected line opens the panel with Instances 3 and sends one preview with the ids, the line, the point as at, a floor and no spacing; made.spacing fills Spacing and the outlines are the ghost', () => {
  const r = linear()
  expect(r.panel.opened).toBe(1)
  const p = r.tool.getState().panel!
  expect(p.tool).toBe('sk_lpattern')
  expect(p.count).toEqual({ text: '3', error: null })
  expect(p.second.text).toBe('')
  expect(r.tool.getState().hint).toBeNull()
  // the selection is kept and nothing is sent but the preview
  expect(r.ids()).toEqual(['entity:e5'])
  expect(r.sent).toHaveLength(1)
  const first = r.of(LP, true)[0]!
  expect(first).toMatchObject({ type: LP, preview: true, ids: ['e5'], line: 'e1', at: [70, -2], floor: 5, count: 3 })
  expect(first).not.toHaveProperty('spacing')
  const outlines = [[[40, 24], [50, 24], [45, 30]]]
  previewed(r, first, { entities: ['e8', 'e9'], count: 3, spacing: 25, direction: [60, 0] }, outlines)
  expect(r.tool.getState().panel!.second).toEqual({ text: '25', error: null })
  expect(r.store.getState().preview).toEqual({ kind: 'copies', outlines })
})

it('floor is the desktop _nice of 40 pixels: 0.1, 0.07 and 0.26 mm per pixel give 5, 5 and 20', () => {
  expect(patternFloor(0.1)).toBe(5)
  expect(patternFloor(0.07)).toBe(5)
  expect(patternFloor(0.26)).toBe(20)
  expect(patternFloor(0.025)).toBe(1)
  expect(patternFloor(0.5)).toBe(20)
})

it('linear: changing Instances to 4 and Spacing to 12 sends previews with them; with one unanswered, two more changes send only the later one once the answer comes; an older id does not replace a newer ghost', () => {
  const r = linear()
  previewed(r, r.of(LP, true)[0]!, { entities: ['e8', 'e9'], spacing: 25 })
  edit(r, 'count', '4')
  expect(r.of(LP, true)).toHaveLength(2)
  expect(r.of(LP, true)[1]).toMatchObject({ count: 4, spacing: 25 })
  expect(r.of(LP, true)[1]).not.toHaveProperty('floor')
  previewed(r, r.of(LP, true)[1]!, { entities: ['e8', 'e9', 'e10'], spacing: 25 })
  edit(r, 'second', '12')
  expect(r.of(LP, true)).toHaveLength(3)
  const third = r.of(LP, true)[2]!
  expect(third).toMatchObject({ count: 4, spacing: 12 })
  // two changes while the third is out: nothing yet, then only the latest
  edit(r, 'second', '13')
  edit(r, 'second', '14')
  expect(r.of(LP, true)).toHaveLength(3)
  const ghost3 = [[[1, 1], [2, 2]]]
  previewed(r, third, { entities: ['e8'], spacing: 12 }, ghost3)
  expect(r.store.getState().preview).toEqual({ kind: 'copies', outlines: ghost3 })
  expect(r.of(LP, true)).toHaveLength(4)
  const fourth = r.of(LP, true)[3]!
  expect(fourth).toMatchObject({ count: 4, spacing: 14 })
  const ghost4 = [[[3, 3], [4, 4]]]
  previewed(r, fourth, { entities: ['e8'], spacing: 14 }, ghost4)
  expect(r.store.getState().preview).toEqual({ kind: 'copies', outlines: ghost4 })
  // a late answer to an older request is dropped
  previewed(r, third, { entities: ['e8'], spacing: 12 }, ghost3)
  expect(r.store.getState().preview).toEqual({ kind: 'copies', outlines: ghost4 })
  expect(r.of(LP, true)).toHaveLength(4)
  // the spacing the person typed is kept: a later made.spacing does not overwrite it
  expect(r.tool.getState().panel!.second.text).toBe('14')
})

it('linear: Accept sends count 4 and spacing 12 without preview; sketch.changed with made closes the panel, clears the selection and the tool is Select', () => {
  const r = linear()
  previewed(r, r.of(LP, true)[0]!, { entities: ['e8'], spacing: 25 })
  edit(r, 'count', '4')
  previewed(r, r.of(LP, true)[1]!, { entities: ['e8'], spacing: 25 })
  edit(r, 'second', '12')
  previewed(r, r.of(LP, true)[2]!, { entities: ['e8'], spacing: 12 })
  expect(r.tool.canAccept()).toBe(true)
  act(() => void r.tool.accept())
  const sent = r.of(LP, false)
  expect(sent).toHaveLength(1)
  expect(sent[0]).toMatchObject({ type: LP, ids: ['e5'], line: 'e1', at: [70, -2], count: 4, spacing: 12 })
  expect(sent[0]).not.toHaveProperty('preview')
  expect(sent[0]).not.toHaveProperty('floor')
  expect(r.tool.getState().panel!.pending).toBe(true)
  expect(r.tool.canAccept()).toBe(false)
  r.answer({ type: 'sketch.changed', id: sent[0]!['id'], made: { entities: ['e8', 'e9', 'e10'], count: 4, spacing: 12, direction: [60, 0] } })
  expect(r.tool.getState().panel).toBeNull()
  expect(r.panel.closed).toBe(1)
  expect(r.ids()).toEqual([])
  expect(r.store.getState().tool).toBe('select')
  expect(r.store.getState().preview).toBeNull()
})

it('linear: Cancel sends nothing more, clears the ghost and leaves the tool on with the selection; Esc does the same', () => {
  const r = linear()
  previewed(r, r.of(LP, true)[0]!, { entities: ['e8'], spacing: 25 })
  expect(r.store.getState().preview?.kind).toBe('copies')
  const before = r.sent.length
  act(() => r.tool.cancel())
  expect(r.sent).toHaveLength(before)
  expect(r.store.getState().preview).toBeNull()
  expect(r.tool.getState().panel).toBeNull()
  expect(r.panel.closed).toBe(1)
  expect(r.store.getState().tool).toBe('sk_lpattern')
  expect(r.ids()).toEqual(['entity:e5'])
  expect(r.tool.getState().hint).toBe('Linear pattern: tap a line for the direction.')
  // back at the pick step: the line again opens it again; Esc (Desktop mode) cancels it the same way
  r.commitAt(E('e1'), [70, -2])
  expect(r.tool.getState().panel).not.toBeNull()
  let took = false
  act(() => void (took = r.store.escapeTools()))
  expect(took).toBe(true)
  expect(r.tool.getState().panel).toBeNull()
  expect(r.store.getState().tool).toBe('sk_lpattern')
  expect(r.ids()).toEqual(['entity:e5'])
  // the tool pressed again leaves it and closes the panel
  r.commitAt(E('e1'), [70, -2])
  r.choose('sk_lpattern')
  expect(r.tool.getState().panel).toBeNull()
  expect(r.store.getState().tool).toBe('select')
  // a Commit while the panel is open takes no pick
  const s = linear()
  s.commitAt(E('e7'), [10, 10])
  expect(s.ids()).toEqual(['entity:e5'])
})

it('linear: with nothing selected the hint is the desktop words, and a Commit on a line selects it', () => {
  const r = rig()
  r.choose('sk_lpattern')
  expect(r.tool.getState().hint).toBe('Linear pattern: tap the geometry, then a line for the direction.')
  r.commitAt(E('e1'), [70, 0])
  expect(r.ids()).toEqual(['entity:e1'])
  expect(r.sent).toEqual([])
  expect(r.tool.getState().panel).toBeNull()
})

it('circular: a Commit on a point sends it as the centre, on empty space and on an unselected entity the tapped [u, v]; on a selected entity it only deselects; the panel opens with 6 and 360; Accept with 4 and 90 sends them', () => {
  const at = (hit: SketchHit | null, uv: [number, number]) => {
    const r = rig()
    r.select(E('e5'))
    r.choose('sk_cpattern')
    expect(r.tool.getState().hint).toBe('Circular pattern: tap the centre point.')
    r.commitAt(hit, uv)
    return r
  }
  const onPoint = at(P('p2'), [60.4, 0.3])
  expect(onPoint.of(CP, true)[0]).toMatchObject({ type: CP, preview: true, ids: ['e5'], centre: { at: [60.4, 0.3], point: 'p2' }, count: 6, angle: 360 })
  expect(onPoint.tool.getState().panel).toMatchObject({ tool: 'sk_cpattern', count: { text: '6', error: null }, second: { text: '360', error: null } })
  expect(at(null, [5, 6]).of(CP, true)[0]).toMatchObject({ centre: [5, 6] })
  expect(at(E('e7'), [12, 9]).of(CP, true)[0]).toMatchObject({ centre: [12, 9] })
  const onSelected = at(E('e5'), [40, 24])
  expect(onSelected.sent).toEqual([])
  expect(onSelected.tool.getState().panel).toBeNull()
  expect(onSelected.ids()).toEqual([])
  // nothing selected: a tap on empty space does nothing
  const none = rig()
  none.choose('sk_cpattern')
  expect(none.tool.getState().hint).toBe('Circular pattern: tap the geometry, then the centre point.')
  none.commitAt(null, [1, 1])
  expect(none.sent).toEqual([])
  // 4 over 90
  edit(onPoint, 'count', '4')
  previewed(onPoint, onPoint.of(CP, true)[0]!, { entities: ['e8'], count: 6, angle: 360, centre: [60, 0] })
  edit(onPoint, 'second', '90')
  previewed(onPoint, onPoint.of(CP, true).at(-1)!, { entities: ['e8'], count: 4, angle: 90, centre: [60, 0] })
  expect(onPoint.of(CP, true).at(-1)).toMatchObject({ count: 4, angle: 90 })
  act(() => void onPoint.tool.accept())
  expect(onPoint.of(CP, false)).toHaveLength(1)
  expect(onPoint.of(CP, false)[0]).toMatchObject({ type: CP, ids: ['e5'], centre: { at: [60.4, 0.3], point: 'p2' }, count: 4, angle: 90 })
  expect(onPoint.of(CP, false)[0]).not.toHaveProperty('preview')
})

it('Instances of 1, 2.5 and empty mark the row invalid and send nothing; a spacing of #gap * 2 is sent as that text; a refusal is shown under the rows and the ghost is cleared', () => {
  const r = linear()
  previewed(r, r.of(LP, true)[0]!, { entities: ['e8'], spacing: 25 })
  const n = r.sent.length
  for (const bad of ['1', '2.5', '']) {
    edit(r, 'count', bad)
    expect(r.tool.getState().panel!.count.error, bad).not.toBeNull()
    expect(r.tool.canAccept()).toBe(false)
    let sentOk = true
    act(() => void (sentOk = r.tool.accept()))
    expect(sentOk).toBe(false)
  }
  expect(r.sent).toHaveLength(n)
  edit(r, 'second', '#nope')
  expect(r.tool.getState().panel!.second.error).not.toBeNull()
  expect(r.sent).toHaveLength(n)
  edit(r, 'count', '3')
  expect(r.tool.getState().panel!.count.error).toBeNull()
  // Spacing is still marked: the count is not sent until both rows can be
  expect(r.sent).toHaveLength(n)
  edit(r, 'second', '#gap * 2')
  expect(r.sent).toHaveLength(n + 1)
  expect(r.of(LP, true).at(-1)).toMatchObject({ count: 3, spacing: '#gap * 2' })
  expect(r.store.getState().preview?.kind).toBe('copies')
  r.say({ type: 'error', id: r.of(LP, true).at(-1)!['id'], for: LP, message: 'spacing: unknown variable #gap' })
  expect(r.tool.getState().panel!.refusal).toBe('spacing: unknown variable #gap')
  expect(r.store.getState().preview).toBeNull()
  expect(r.store.getState().error).toBeNull()
  expect(r.store.getState().tool).toBe('sk_lpattern')
  expect(r.ids()).toEqual(['entity:e5'])
})

it('a refused Accept stays in the panel in the server\'s words, with the ghost cleared and the selection kept', () => {
  const r = linear()
  previewed(r, r.of(LP, true)[0]!, { entities: ['e8'], spacing: 25 })
  act(() => void r.tool.accept())
  r.say({ type: 'error', id: r.of(LP, false)[0]!['id'], for: LP, message: 'count: the copies would make the sketch hold more than 5000 points and entities' })
  const p = r.tool.getState().panel!
  expect(p.pending).toBe(false)
  expect(p.refusal).toMatch(/^count: /)
  expect(r.store.getState().preview).toBeNull()
  expect(r.ids()).toEqual(['entity:e5'])
})

it('construction mode has no effect on the pattern message', () => {
  const off = linear()
  const on = rig()
  act(() => on.store.toggleConstruction())
  on.select(E('e5'))
  on.choose('sk_lpattern')
  on.commitAt(E('e1'), [70, -2])
  const strip = (m: Record<string, unknown>) => ({ ...m, id: undefined })
  expect(strip(on.of(LP, true)[0]!)).toEqual(strip(off.of(LP, true)[0]!))
})
