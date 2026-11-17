// @vitest-environment node
import { readFileSync } from 'node:fs'
import { Group } from 'three'
import { describe, expect, it } from 'vitest'
import { AssemblyPickIndex } from '../assembly/pick'
import { buildAssemblyScene } from '../assembly/scene'
import { decodeOutputs, isAssembly, isStudio } from '../wire'
import type { AssemblyOutputs, MateConnector, StudioOutputs } from '../wire'
import { STANDARD_VIEWS, ViewCamera } from './camera'
import { assemblyTriads, buildTriads, CONNECTOR_REACH, TRIAD_PX } from './connectors'
import { DEFAULT_KINDS, PickIndex } from './pick'
import { buildScene } from './scene'

// Mate connectors on screen (card I10.3): the triads a Part Studio and an Assembly draw, how big they stay, which flags hide them,
// and what a pick on one returns. The Assembly fixture is recorded from the engine (web/fixtures/make_connectors_fixture.py).
const dir = new URL('../../fixtures/', import.meta.url)
const buf = (name: string): ArrayBuffer => {
  const b = readFileSync(new URL(name, dir))
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
}

function bracket(connectors: readonly MateConnector[] = []): StudioOutputs {
  const out = decodeOutputs(buf('bracket.outputs.fsw'))
  if (!isStudio(out)) throw new Error('not a studio')
  return { ...out, connectors }
}

function hinge(): AssemblyOutputs {
  const out = decodeOutputs(buf('hinge.connectors.assembly.fsw'))
  if (!isAssembly(out)) throw new Error('not an assembly')
  return out
}

const triadGroup = (root: Group): Group | undefined => root.children.find((c): c is Group => c instanceof Group && c.name === 'connectors')

/** A connector of the bracket at its mesh's first vertex, so a face of the part is right behind it. */
function onTheBracket(id = 'F90'): MateConnector {
  const mesh = bracket().parts[0]!.mesh!
  const o: [number, number, number] = [mesh.vertices[0]!, mesh.vertices[1]!, mesh.vertices[2]!]
  return { id, name: 'Pin', part: bracket().parts[0]!.id, frame: { o, z: [0, 0, 1], x: [1, 0, 0] }, hash: 'h', sketch: '' }
}

describe('the recorded Assembly outputs', () => {
  it('carry the connectors each instance offers and the world frame of each mate end', () => {
    const out = hinge()
    const plate = out.instances.find((i) => i.name.startsWith('Base plate'))!
    const arm = out.instances.find((i) => i.name.startsWith('Arm'))!
    expect(plate.connectors.map((c) => [c.name, c.kind])).toEqual([['Top', 'connector']])
    expect(arm.connectors.map((c) => c.kind)).toEqual(['origin'])
    // a placed frame is the part's frame under the instance's transform: the arm's origin connector sits at its translation
    expect(arm.connectors[0]!.world.o).toEqual([arm.T[0]![3], arm.T[1]![3], arm.T[2]![3]])
    expect(out.mates).toHaveLength(2)
    for (const m of out.mates) expect(m.ends.every((e) => e !== null)).toBe(true)
  })
})

describe('the Part Studio view', () => {
  it('draws one triad for one connector, and none when K hides them', () => {
    const on = buildScene(bracket([onTheBracket()]))
    expect(on.stats.connectors).toBe(1)
    expect(triadGroup(on.root)?.children).toHaveLength(1)
    expect(on.triads.map((t) => t.id)).toEqual(['F90'])
    const off = buildScene(bracket([onTheBracket()]), { connectors: false })
    expect(off.stats.connectors).toBe(0)
    expect(triadGroup(off.root)?.children).toHaveLength(0)
  })

  it('leaves out a connector whose part the eye hides', () => {
    const out = bracket([onTheBracket()])
    const hidden = { ...out, parts: out.parts.map((p) => ({ ...p, hidden: true })) }
    expect(buildScene(hidden).stats.connectors).toBe(0)
  })

  it('keeps a triad the same size in pixels at any zoom', () => {
    const c = onTheBracket()
    const layer = buildTriads(buildScene(bracket([c])).triads)
    const node = layer.group.children[0]!
    layer.update(800, 600, 0.5)
    const near = node.matrix.elements[10]! // the z arm's length per unit: scale * size, since z is (0, 0, 1)
    layer.update(800, 600, 2)
    expect(node.matrix.elements[10]! / near).toBeCloseTo(4, 9)
    expect(near).toBeCloseTo(0.5 * TRIAD_PX, 9)
    // and it sits at the connector
    expect([node.matrix.elements[12], node.matrix.elements[13], node.matrix.elements[14]]).toEqual(c.frame.o)
  })
})

describe('the Assembly view', () => {
  it('draws a triad at each mate end and each connector, and J leaves the mate ends out', () => {
    const out = hinge()
    const all = buildAssemblyScene(out)
    // I3 (the pin) is hidden in the fixture: its mate end is not drawn; the two mates' other ends are
    expect(all.stats.connectors).toBe(2)
    expect(all.stats.mateEnds).toBe(out.mates.flatMap((m) => [m.a, m.b]).filter((i) => out.instances.find((x) => x.id === i && !x.hidden)).length)
    expect(all.stats.mateEnds).toBeGreaterThan(0)
    expect(triadGroup(all.root)?.children).toHaveLength(all.stats.connectors + all.stats.mateEnds)
    const noMates = buildAssemblyScene(out, { mates: false })
    expect(noMates.stats).toMatchObject({ connectors: 2, mateEnds: 0 })
    const noConnectors = buildAssemblyScene(out, { connectors: false })
    expect(noConnectors.stats).toMatchObject({ connectors: 0, mateEnds: all.stats.mateEnds })
    expect(assemblyTriads(out, { connectors: false, mates: false })).toEqual([])
  })

  it('a mate end is where the mate puts it: the world frame of that end', () => {
    const out = hinge()
    const ends = assemblyTriads(out, { connectors: false, mates: true })
    const first = out.mates[0]!
    const a = ends.find((t) => t.id === first.id && t.end === 'a')
    expect(a?.o).toEqual(first.ends[0]!.o)
    expect(a?.kind).toBe('mate')
  })

  it('an instance\'s connectors follow it while it is dragged', () => {
    const out = hinge()
    const scene = buildAssemblyScene(out)
    const arm = out.instances.find((i) => i.name.startsWith('Arm'))!
    const T = arm.T.map((r) => [...r])
    T[0]![3] = (T[0]![3] as number) + 25
    const was = scene.triads.find((t) => t.kind === 'connector' && t.instance === arm.id)!.o
    scene.place(arm.id, T)
    const now = scene.triads.find((t) => t.kind === 'connector' && t.instance === arm.id)!.o
    expect(now[0]! - was[0]!).toBeCloseTo(25, 9)
    expect([now[1], now[2]]).toEqual([was[1], was[2]])
  })
})

describe('picking a triad', () => {
  function rig() {
    const out = bracket([onTheBracket()])
    const built = buildScene(out)
    const view = new ViewCamera()
    view.resize(900, 700)
    view.setBack(STANDARD_VIEWS.iso)
    view.fit(built.fitBounds)
    return { out, built, view, index: new PickIndex(out, built), at: view.project(onTheBracket().frame.o) }
  }

  it('returns the connector with its feature id, ahead of the face behind it', () => {
    const r = rig()
    // the bare face is there to be found: without connectors in the filter the pick is a face of the part
    const face = r.index.pick(r.view, r.at.x, r.at.y, 'mouse', ['face'])
    expect(face?.kind).toBe('face')
    const hit = r.index.pick(r.view, r.at.x, r.at.y, 'mouse', ['face', 'edge', 'connector'])
    expect(hit).toMatchObject({ kind: 'connector', key: 'F90', part: null, index: -1, name: null })
    expect(hit?.point).toEqual(onTheBracket().frame.o)
    expect(hit && 'instance' in hit).toBe(false)
  })

  it('is never part of what a pick considers when nothing names it', () => {
    const r = rig()
    expect(DEFAULT_KINDS).not.toContain('connector')
    expect(r.index.pick(r.view, r.at.x, r.at.y, 'mouse')?.kind).not.toBe('connector')
  })

  it('reaches as far as the pointer does: a finger further than a mouse', () => {
    const r = rig()
    const miss = (px: number) => r.index.pick(r.view, r.at.x + px, r.at.y, 'mouse', ['connector'])
    expect(CONNECTOR_REACH.touch).toBeGreaterThan(CONNECTOR_REACH.mouse)
    expect(miss(CONNECTOR_REACH.mouse - 1)).not.toBeNull()
    expect(miss(CONNECTOR_REACH.mouse + 30)).toBeNull()
    expect(r.index.pick(r.view, r.at.x + CONNECTOR_REACH.mouse + 3, r.at.y, 'touch', ['connector'])).not.toBeNull()
  })

  it('in an Assembly returns the instance and the connector id', () => {
    const out = hinge()
    const built = buildAssemblyScene(out)
    const view = new ViewCamera()
    view.resize(900, 700)
    view.setBack(STANDARD_VIEWS.iso)
    view.fit(built.bounds)
    const index = new AssemblyPickIndex(out, built)
    const want = built.triads.find((t) => t.kind === 'connector' && t.id === out.instances.find((i) => i.name.startsWith('Base plate'))!.connectors[0]!.id)!
    const at = view.project(want.o)
    const hit = index.pick(view, at.x, at.y, 'mouse', ['face', 'edge', 'vertex', 'connector'])
    expect(hit).toMatchObject({ kind: 'connector', key: want.id, instance: want.instance })
    // without connectors in the filter, the same spot is a face or nothing
    expect(index.pick(view, at.x, at.y, 'mouse', ['face'])?.kind).not.toBe('connector')
  })
})
