// @vitest-environment node
import { readFileSync } from 'node:fs'
import { Mesh, Vector3 } from 'three'
import { describe, expect, it } from 'vitest'
import { itemOf, idOfItem } from '../selection/store'
import { STANDARD_VIEWS, ViewCamera } from '../viewport/camera'
import { decodeOutputs, isAssembly } from '../wire'
import type { AssemblyOutputs, InstanceEntry } from '../wire'
import { AssemblyPickIndex } from './pick'
import { buildAssemblyScene } from './scene'

const dir = new URL('../../fixtures/', import.meta.url)

function hinge(): AssemblyOutputs {
  const b = readFileSync(new URL('hinge.assembly.fsw', dir))
  const out = decodeOutputs(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer)
  if (!isAssembly(out)) throw new Error('the fixture is not an Assembly')
  return out
}

interface Want {
  instances: { id: string; name: string; part: string; T: number[][]; hidden: boolean; fixed: boolean }[]
  mates: { id: string; name: string; type: string; suppressed: boolean; a: string; b: string }[]
  dof: number
  errors: Record<string, string>
}
const want = JSON.parse(readFileSync(new URL('hinge.assembly.expected.json', dir), 'utf8')) as Want

/** The hinge with a second instance of the first part, `shift` mm along x from it. */
function twoOfOne(): AssemblyOutputs {
  const out = hinge()
  const first = out.instances[0] as InstanceEntry
  const T = first.T.map((r) => [...r])
  T[0]![3] = (T[0]![3] as number) + 60
  const copy: InstanceEntry = { ...first, id: 'I9', name: `${first.name} copy`, T, hidden: false }
  return { ...out, instances: [{ ...first, hidden: false }, copy] }
}

describe('the Assembly outputs, decoded', () => {
  it('lists the hinge instances with transforms and flags, and its mates with their types', () => {
    const out = hinge()
    expect(out.instances.map((i) => ({ id: i.id, name: i.name, part: i.part, T: i.T, hidden: i.hidden, fixed: i.fixed }))).toEqual(want.instances)
    expect(out.mates.map((m) => ({ id: m.id, name: m.name, type: m.type, suppressed: m.suppressed, a: m.a, b: m.b }))).toEqual(want.mates)
    expect(out.dof).toBe(want.dof)
    expect(out.errors).toEqual(want.errors)
    expect(want.mates.map((m) => m.type)).toEqual(['fastened', 'revolute'])
    // the instance that is hidden in the fixture, and the one that is fixed
    expect(out.instances.filter((i) => i.hidden).map((i) => i.id)).toEqual(['I3'])
    expect(out.instances.some((i) => i.fixed)).toBe(true)
    for (const i of out.instances) expect(i.part !== null && out.parts[i.part]?.mesh).toBeTruthy()
  })
})

describe('the Assembly scene', () => {
  it('two instances of one part draw two objects that share one geometry', () => {
    const scene = buildAssemblyScene(twoOfOne())
    const meshes = scene.root.children.filter((c): c is Mesh => c instanceof Mesh && c.name.startsWith('instance:'))
    expect(meshes.length).toBe(2)
    expect(meshes[0]!.geometry).toBe(meshes[1]!.geometry)
    expect(scene.stats).toMatchObject({ instances: 2, drawn: 2, hidden: 0, geometries: 1, drawnIds: ['I1', 'I9'] })
    // each under its own placement
    expect(new Vector3().setFromMatrixPosition(meshes[1]!.matrix).x - new Vector3().setFromMatrixPosition(meshes[0]!.matrix).x).toBeCloseTo(60)
    scene.dispose()
  })

  it('a hidden instance has no object and cannot be picked; the parts of the others are drawn', () => {
    const out = hinge()
    const scene = buildAssemblyScene(out)
    expect(scene.stats).toMatchObject({ instances: 3, drawn: 2, hidden: 1, geometries: 2 })
    expect(scene.objects.has('I3')).toBe(false)
    expect(new AssemblyPickIndex(out, scene).instanceIds).toEqual(['I1', 'I2'])
    scene.dispose()
  })
})

describe('picking in an Assembly', () => {
  it('a pick on an instance\'s face returns the instance id with the Element name; a second instance of the part is told apart', () => {
    const out = twoOfOne()
    const scene = buildAssemblyScene(out)
    const index = new AssemblyPickIndex(out, scene)
    const view = new ViewCamera()
    view.resize(1024, 768)
    view.setBack(STANDARD_VIEWS.iso)
    view.fit(scene.bounds)
    const first = out.instances[0] as InstanceEntry
    const part = out.parts[first.part as string]!
    const named = [...part.names.face.entries()]
    expect(named.length).toBeGreaterThan(0)
    // the centre of each instance's biggest-face triangle, as a pointer position
    const seen = new Map<string, Set<string | null>>()
    for (const id of ['I1', 'I9']) {
      const obj = scene.objects.get(id)!
      const mesh = part.mesh!
      for (let t = 0; t < mesh.indices.length / 3; t += 7) {
        const c = new Vector3()
        for (let k = 0; k < 3; k++) {
          const i = (mesh.indices[3 * t + k] as number) * 3
          c.add(new Vector3(mesh.positions[i], mesh.positions[i + 1], mesh.positions[i + 2]))
        }
        c.divideScalar(3).applyMatrix4(obj.matrix)
        const p = view.project([c.x, c.y, c.z])
        const hit = index.pick(view, p.x, p.y, 'mouse', ['face'])
        if (hit === null) continue
        expect(hit.kind).toBe('face')
        expect(hit.part).toBe(first.part)
        if (hit.instance === id) {
          if (!seen.has(id)) seen.set(id, new Set())
          seen.get(id)!.add(hit.name)
          expect(hit.name).toBe(part.names.face.get(hit.index) ?? null)
        }
      }
    }
    expect([...seen.keys()].sort()).toEqual(['I1', 'I9'])
    // some face was found with a name, in both instances
    for (const names of seen.values()) expect([...names].some((n) => n !== null)).toBe(true)
    scene.dispose()
  })

  it('the selection item of a hit carries the instance; items of a Part Studio keep their old ids', () => {
    const hit = { kind: 'face', part: 'abc', instance: 'I2', index: 3, name: 'F2/1.f7', point: [0, 0, 0], distancePx: null } as const
    const item = itemOf(hit)
    expect(item.instance).toBe('I2')
    expect(item.id).toBe('face|I2|F2/1.f7')
    expect(itemOf({ ...hit, instance: 'I3' }).id).not.toBe(item.id)
    const { instance: _drop, ...studioHit } = hit
    void _drop
    expect(itemOf(studioHit).id).toBe(idOfItem('face', 'abc', 'F2/1.f7', 3, null))
    expect('instance' in itemOf(studioHit)).toBe(false)
  })
})
