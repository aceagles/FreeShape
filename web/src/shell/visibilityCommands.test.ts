// @vitest-environment node
import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import { partItem, SelectionStore } from '../selection/store'
import { instanceItem } from '../assembly/select'
import { fixture } from '../test/fixtures'
import { buildScene } from '../viewport/scene'
import { DEFAULT_VIEW, shapeAssembly, shapeStudio, TabView } from '../viewport/visibility'
import { decodeOutputs, isAssembly } from '../wire'
import type { AssemblyOutputs, StudioOutputs } from '../wire'
import { anyPlaneShown, visibilityCommands, visibilityTargets } from './visibilityCommands'
import type { VisibilityContext } from './visibilityCommands'

// The visibility keys' commands (card I9.8) without a window: what each one sends and what it flips.
const bracket = (): StudioOutputs => fixture('bracket').outputs
const part = (o: StudioOutputs) => o.parts[0]!.id
const hinge = (): AssemblyOutputs => {
  const b = readFileSync(new URL('../../fixtures/hinge.assembly.fsw', import.meta.url))
  const out = decodeOutputs(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer)
  if (!isAssembly(out)) throw new Error('the fixture is not an Assembly')
  return out
}

/** A studio with a second part, a sketch and a plane, the sketch hidden by the eye. */
function richer(): StudioOutputs {
  const o = bracket()
  const p1 = o.parts[0]!
  const p2 = { ...p1, id: 'P2', name: 'Second' }
  const feat = (id: string, type: string, hidden: boolean) => ({ ...o.features[0]!, id, type, hidden })
  return {
    ...o,
    parts: [p1, p2],
    features: [feat('S1', 'sketch', true), feat('S2', 'sketch', false), feat('PL', 'plane', false), feat('CX', 'context', true)],
    sketches: [{ id: 'S1' }, { id: 'S2' }] as never,
    planes: { PL: { o: [0, 0, 0], x: [1, 0, 0], n: [0, 0, 1] } } as never,
  }
}

function setup(over: Partial<VisibilityContext> = {}) {
  const sent: Record<string, unknown>[] = []
  const send = vi.fn(async (e: Record<string, unknown>) => void sent.push(e))
  const ctx: VisibilityContext = {
    tabKind: 'studio',
    readOnly: false,
    sketchOpen: false,
    dialogOpen: false,
    view: new TabView(),
    selection: new SelectionStore(),
    studio: bracket(),
    assembly: null,
    selectedFeature: null,
    send,
    fail: vi.fn(),
    ...over,
  }
  return { ctx, sent, run: (name: string) => visibilityCommands(ctx)[name]!() }
}
const settle = () => new Promise((r) => setTimeout(r, 0))

describe('P and Shift+P', () => {
  it('P hides every plane and shows them all again: the default planes in the scene, the ones a person made by their eye', async () => {
    const o = richer()
    const { ctx, sent, run } = setup({ studio: o })
    expect(anyPlaneShown(ctx.view.getState(), o)).toBe(true)
    expect(run('view.planes')).toBe(true)
    await settle()
    expect(ctx.view.getState().hiddenPlanes).toEqual(['Top', 'Front', 'Right'])
    expect(sent).toEqual([{ op: 'set_features_hidden', ids: ['PL'], value: true }])
    const off = buildScene({ ...o, features: o.features.map((f) => (f.id === 'PL' ? { ...f, hidden: true } : f)) }, { hiddenPlanes: ['Top', 'Front', 'Right'] })
    expect(off.stats.planes).toBe(0)
    expect(off.stats.hiddenDefaultPlanes).toEqual(['Top', 'Front', 'Right'])
    // the server's answer: every plane hidden. P again shows them all
    const hidden = { ...o, features: o.features.map((f) => (f.id === 'PL' ? { ...f, hidden: true } : f)) }
    const next = setup({ studio: hidden, view: ctx.view })
    expect(anyPlaneShown(ctx.view.getState(), hidden)).toBe(false)
    next.run('view.planes')
    await settle()
    expect(ctx.view.getState().hiddenPlanes).toEqual([])
    expect(next.sent).toEqual([{ op: 'set_features_hidden', ids: ['PL'], value: false }])
  })

  it('with the planes hidden by P, one plane shown by its eye is drawn alone; P then hides it, and P again shows all', async () => {
    const o = richer()
    const hide = (on: boolean, ids: string[]) => ({ ...o, features: o.features.map((f) => (ids.includes(f.id) ? { ...f, hidden: on } : f)) })
    const view = new TabView()
    view.setHiddenPlanes(['Top', 'Front', 'Right'])
    // the eye of plane PL shows it alone
    const eyed = hide(false, ['PL'])
    const drawn = buildScene(eyed, { hiddenPlanes: view.getState().hiddenPlanes })
    expect(drawn.stats.planes).toBe(1)
    // one default plane's eye (an Origin row) shows it alone
    view.toggleDefaultPlane('Front')
    expect(buildScene(hide(true, ['PL']), { hiddenPlanes: view.getState().hiddenPlanes }).stats.planes).toBe(1)
    view.setHiddenPlanes(['Top', 'Front', 'Right'])
    expect(anyPlaneShown(view.getState(), eyed)).toBe(true)
    const a = setup({ studio: eyed, view })
    a.run('view.planes')
    await settle()
    expect(a.sent).toEqual([{ op: 'set_features_hidden', ids: ['PL'], value: true }])
    expect(view.getState().hiddenPlanes).toEqual(['Top', 'Front', 'Right'])
    const b = setup({ studio: hide(true, ['PL']), view })
    b.run('view.planes')
    await settle()
    expect(view.getState().hiddenPlanes).toEqual([])
    expect(b.sent).toEqual([{ op: 'set_features_hidden', ids: ['PL'], value: false }])
  })

  it('a read-only Document toggles the default planes and sends nothing', async () => {
    const { ctx, sent, run } = setup({ studio: richer(), readOnly: true })
    run('view.planes')
    await settle()
    expect(ctx.view.getState().hiddenPlanes).toEqual(['Top', 'Front', 'Right'])
    expect(sent).toEqual([])
  })

  it('Shift+P hides planes, axes and mate connectors together when any shows, else shows them all', async () => {
    const o = richer()
    const withAxis = { ...o, features: [...o.features, { ...o.features[0]!, id: 'AX', type: 'axis', hidden: false }] }
    const { ctx, sent, run } = setup({ studio: withAxis })
    run('view.construction')
    await settle()
    expect(ctx.view.getState()).toMatchObject({ hiddenPlanes: ['Top', 'Front', 'Right'], connectors: false })
    expect(sent).toEqual([{ op: 'set_features_hidden', ids: ['PL', 'AX'], value: true }])
    const allOff = { ...withAxis, features: withAxis.features.map((f) => (f.type === 'plane' || f.type === 'axis' ? { ...f, hidden: true } : f)) }
    const next = setup({ studio: allOff, view: ctx.view })
    next.run('view.construction')
    await settle()
    expect(ctx.view.getState()).toMatchObject({ hiddenPlanes: [], connectors: true })
    expect(next.sent).toEqual([{ op: 'set_features_hidden', ids: ['PL', 'AX'], value: false }])
  })

  it('in an Assembly P does nothing and Shift+P is the mate connectors and mates', () => {
    const { ctx, run } = setup({ tabKind: 'assembly', studio: null, assembly: hinge() })
    run('view.planes')
    expect(ctx.view.getState().hiddenPlanes).toEqual([])
    run('view.construction')
    expect(ctx.view.getState()).toMatchObject({ hiddenPlanes: [], connectors: false, mates: false })
  })

  it('K and J flip their flags', () => {
    const { ctx, run } = setup()
    run('view.connectors')
    expect(ctx.view.getState().connectors).toBe(false)
    run('view.mates')
    expect(ctx.view.getState().mates).toBe(false)
    run('view.connectors')
    run('view.mates')
    expect(ctx.view.getState()).toMatchObject({ connectors: true, mates: true })
  })
})

describe('Y and Shift+Y in a Part Studio', () => {
  it('Y hides the selected part: set_part_override with hidden true', async () => {
    const o = bracket()
    const { ctx, sent, run } = setup({ studio: o })
    ctx.selection.set([partItem(part(o))])
    run('view.hide')
    await settle()
    expect(sent).toEqual([{ op: 'set_part_override', part: part(o), hidden: true }])
  })

  it('Y with a face of the part selected hides its part', async () => {
    const o = bracket()
    const { ctx, sent, run } = setup({ studio: o })
    ctx.selection.set([{ id: 'f', kind: 'face', part: part(o), index: 0, name: 'Face1', key: null }])
    run('view.hide')
    await settle()
    expect(sent).toEqual([{ op: 'set_part_override', part: part(o), hidden: true }])
  })

  it('Y with nothing selected hides the part under the pointer', async () => {
    const o = bracket()
    const { ctx, sent, run } = setup({ studio: o })
    ctx.selection.aim(partItem(part(o)))
    run('view.hide')
    await settle()
    expect(sent).toEqual([{ op: 'set_part_override', part: part(o), hidden: true }])
  })

  it('Y with nothing selected and nothing under the pointer sends nothing', async () => {
    const { sent, run } = setup()
    expect(run('view.hide')).toBe(true)
    await settle()
    expect(sent).toEqual([])
  })

  it('Y hides every selected part, one edit each, and the selected sketch, plane or axis in one', async () => {
    const o = richer()
    const { ctx, sent, run } = setup({ studio: o, selectedFeature: 'S2' })
    ctx.selection.set([partItem('P2'), partItem(part(o)), { id: 'plane|PL', kind: 'plane', part: null, index: -1, name: null, key: 'PL' }])
    run('view.hide')
    await settle()
    expect(sent).toEqual([
      { op: 'set_part_override', part: 'P2', hidden: true },
      { op: 'set_part_override', part: part(o), hidden: true },
      { op: 'set_features_hidden', ids: ['PL', 'S2'], value: true },
    ])
  })

  it('a part that is hidden already is not a target; with only hidden ones selected the pointer decides', async () => {
    const r = richer()
    const o = { ...r, parts: [{ ...r.parts[0]!, hidden: true }, r.parts[1]!] }
    const { ctx, sent, run } = setup({ studio: o })
    ctx.selection.set([partItem(o.parts[0]!.id)])
    ctx.selection.aim(partItem('P2'))
    run('view.hide')
    await settle()
    expect(sent).toEqual([{ op: 'set_part_override', part: 'P2', hidden: true }])
  })

  it('an open feature dialog leaves the selection alone: only what is under the pointer is hidden', () => {
    const o = bracket()
    const { ctx } = setup({ studio: o, dialogOpen: true })
    ctx.selection.set([partItem(part(o))])
    expect(visibilityTargets(ctx)).toEqual({ parts: [], features: [] })
    ctx.selection.aim(partItem(part(o)))
    expect(visibilityTargets(ctx).parts).toEqual([part(o)])
  })

  it('the default planes have no eye: aiming at one hides nothing', () => {
    const { ctx } = setup()
    ctx.selection.aim({ id: 'plane|Top', kind: 'plane', part: null, index: -1, name: null, key: 'Top' })
    expect(visibilityTargets(ctx)).toEqual({ parts: [], features: [] })
  })

  it('Shift+Y shows every hidden part and every hidden Context, and ends isolation', async () => {
    const r = richer()
    const o = { ...r, parts: r.parts.map((p) => ({ ...p, hidden: true })) }
    const { ctx, sent, run } = setup({ studio: o })
    ctx.view.toggleIsolate(['P2'])
    run('view.showAll')
    await settle()
    expect(ctx.view.getState().isolate).toBeNull()
    expect(sent).toEqual([
      { op: 'set_part_override', part: o.parts[0]!.id, hidden: false },
      { op: 'set_part_override', part: 'P2', hidden: false },
      { op: 'set_features_hidden', ids: ['CX'], value: false },
    ])
  })

  it('a refusal stops the rest and is reported once', async () => {
    const o = richer()
    const fail = vi.fn()
    const send = vi.fn().mockRejectedValue(new Error('read only'))
    const { ctx, run } = setup({ studio: o, send, fail })
    ctx.selection.set([partItem('P2'), partItem(part(o))])
    run('view.hide')
    await settle()
    expect(send).toHaveBeenCalledTimes(1)
    expect(fail).toHaveBeenCalledTimes(1)
  })

  it('a read-only Document sends nothing', async () => {
    const o = bracket()
    const { ctx, sent, run } = setup({ studio: o, readOnly: true })
    ctx.selection.set([partItem(part(o))])
    run('view.hide')
    run('view.showAll')
    run('view.sketches')
    await settle()
    expect(sent).toEqual([])
  })
})

describe('Y and Shift+Y in an Assembly', () => {
  it('Y sends set_hidden for the selected instance, else the one under the pointer', async () => {
    const o = hinge()
    const shown = o.instances.filter((i) => !i.hidden)
    const a = shown[0]!
    const b = shown[1]!
    const { ctx, sent, run } = setup({ tabKind: 'assembly', studio: null, assembly: o })
    ctx.selection.set([instanceItem(a.id, a.part)])
    run('view.hide')
    await settle()
    expect(sent).toEqual([{ op: 'set_hidden', instance: a.id, value: true }])
    ctx.selection.set([])
    ctx.selection.aim(instanceItem(b.id, b.part))
    run('view.hide')
    await settle()
    expect(sent[1]).toEqual({ op: 'set_hidden', instance: b.id, value: true })
  })

  it('Shift+Y shows every hidden instance', async () => {
    const o = hinge()
    const hidden = o.instances.filter((i) => i.hidden)
    expect(hidden.length).toBeGreaterThan(0)
    const { sent, run } = setup({ tabKind: 'assembly', studio: null, assembly: o })
    run('view.showAll')
    await settle()
    expect(sent).toEqual(hidden.map((i) => ({ op: 'set_hidden', instance: i.id, value: false })))
  })
})

describe('Shift+H', () => {
  it('hides every sketch when one shows, and shows them all when none does', async () => {
    const o = richer()
    const { sent, run } = setup({ studio: o })
    run('view.sketches')
    await settle()
    expect(sent[0]).toEqual({ op: 'set_features_hidden', ids: ['S1', 'S2'], value: true })
    const all = richer()
    const next = setup({ studio: { ...all, features: all.features.map((f) => (f.type === 'sketch' ? { ...f, hidden: true } : f)) } })
    next.run('view.sketches')
    await settle()
    expect(next.sent[0]).toEqual({ op: 'set_features_hidden', ids: ['S1', 'S2'], value: false })
  })

  it('does nothing in a Part Studio without sketches', async () => {
    const { sent, run } = setup({ studio: { ...bracket(), sketches: [] } })
    run('view.sketches')
    await settle()
    expect(sent).toEqual([])
  })
})

describe('Shift+I and Shift+T', () => {
  it('Shift+I draws only the selected parts; again draws all', () => {
    const o = richer()
    const { ctx, run } = setup({ studio: o })
    ctx.selection.set([partItem('P2')])
    run('view.isolate')
    expect(ctx.view.getState().isolate).toEqual(['P2'])
    const drawn = buildScene(shapeStudio(o, ctx.view.getState()))
    expect(drawn.stats.visiblePartIds).toEqual(['P2'])
    run('view.isolate')
    expect(ctx.view.getState().isolate).toBeNull()
    expect(buildScene(shapeStudio(o, ctx.view.getState())).stats.visiblePartIds).toEqual([part(o), 'P2'])
  })

  it('Shift+I with nothing to isolate does nothing', () => {
    const { ctx, run } = setup()
    run('view.isolate')
    expect(ctx.view.getState().isolate).toBeNull()
  })

  it('an isolation whose parts are gone hides nothing', () => {
    const o = bracket()
    expect(shapeStudio(o, { ...DEFAULT_VIEW, isolate: ['gone'] })).toBe(o)
  })

  it('isolates an instance in an Assembly', () => {
    const o = hinge()
    const a = o.instances.find((i) => !i.hidden)!
    const shaped = shapeAssembly(o, { ...DEFAULT_VIEW, isolate: [a.id] })
    expect(shaped.instances.filter((i) => !i.hidden).map((i) => i.id)).toEqual([a.id])
  })

  it('Shift+T draws the selected part transparent, again solid, and with nothing selected makes all solid', () => {
    const o = richer()
    const { ctx, run } = setup({ studio: o })
    ctx.selection.set([partItem('P2')])
    run('view.transparent')
    expect(ctx.view.getState().transparent).toEqual(['P2'])
    expect(buildScene(o, { transparent: new Set(ctx.view.getState().transparent) }).stats.transparentParts).toBe(1)
    run('view.transparent')
    expect(ctx.view.getState().transparent).toEqual([])
    run('view.transparent')
    expect(ctx.view.getState().transparent).toEqual(['P2'])
    ctx.selection.set([])
    run('view.transparent')
    expect(ctx.view.getState().transparent).toEqual([])
  })
})

describe('where the keys act', () => {
  it('none acts with a sketch open: each leaves its key to the sketch tools', () => {
    const { ctx } = setup({ sketchOpen: true })
    const cmds = visibilityCommands(ctx)
    for (const name of Object.keys(cmds)) expect(cmds[name]!()).toBe(false)
    expect(ctx.view.getState()).toBe(DEFAULT_VIEW)
  })

  it('none acts on a tab that is not a Part Studio or an Assembly', () => {
    const { ctx } = setup({ tabKind: 'variables', studio: null })
    for (const run of Object.values(visibilityCommands(ctx))) expect(run()).toBe(false)
  })
})
