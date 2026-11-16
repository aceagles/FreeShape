// @vitest-environment node
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import type { Group } from 'three'
import { Mesh } from 'three'
import type { Material, ShaderMaterial } from 'three'
import type { LineMaterial } from 'three/addons/lines/LineMaterial.js'
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js'
import { decodeOutputs, isStudio } from '../wire'
import type { Part, StudioOutputs } from '../wire'
import { buildScene, fitBoundsFor } from './scene'
import { HIGHLIGHT_AMOUNT } from './shading'
import { sceneDraw } from '../theme/tokens'
import { ViewCamera } from './camera'

const dir = new URL('../../fixtures/', import.meta.url)
const bytes = readFileSync(new URL('bracket.outputs.fsw', dir))
const expected = JSON.parse(readFileSync(new URL('bracket.expected.json', dir), 'utf8')) as {
  parts: number
  triangles: number
  edges: number
  faces: number
}

function studio(): StudioOutputs {
  const buf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
  const out = decodeOutputs(buf)
  if (!isStudio(out)) throw new Error('fixture is not a studio')
  return out
}

describe('buildScene', () => {
  it('makes one mesh and one line object per part with the fixture counts', () => {
    const built = buildScene(studio())
    const meshes = built.root.children.filter((c): c is Mesh => c instanceof Mesh && c.name.startsWith('part:'))
    const lines = built.root.children.filter((c): c is LineSegments2 => c instanceof LineSegments2)
    expect(meshes).toHaveLength(expected.parts)
    expect(lines).toHaveLength(expected.parts)
    expect(meshes[0]?.geometry.index?.count).toBe(expected.triangles * 3)
    expect(lines[0]?.userData['edgeCount']).toBe(expected.edges)
    expect(built.stats).toMatchObject({
      parts: expected.parts,
      faces: expected.faces,
      edges: expected.edges,
      triangles: expected.triangles,
    })
    built.dispose()
  })

  it('draws the three default planes and the origin', () => {
    const built = buildScene(studio())
    expect(built.stats.planes).toBe(3)
    expect(built.root.getObjectByName('origin')).toBeTruthy()
    built.dispose()
  })

  it('draws a plane and an axis the eye has not hidden, and leaves a hidden one out', () => {
    const base = studio()
    const plane = { o: [0, 0, 0], x: [1, 0, 0], n: [0, 0, 1] } as const
    const axis = { o: [0, 0, 0], d: [0, 0, 1], span: null } as never
    const feat = (id: string, type: string, hidden: boolean) => ({ id, type, label: type, name: id, suppressed: false, error: null, folder: null, hidden })
    const withEye = (hidden: boolean): StudioOutputs => ({ ...base, planes: { P1: plane }, axes: { A1: axis }, features: [feat('P1', 'plane', hidden), feat('A1', 'axis', hidden)] })
    const shown = buildScene(withEye(false))
    expect([shown.stats.planes, shown.stats.axes]).toEqual([4, 1])
    shown.dispose()
    const hid = buildScene(withEye(true))
    expect([hid.stats.planes, hid.stats.axes]).toEqual([3, 0])
    hid.dispose()
  })

  it('uses the typed arrays of the response without copying them', () => {
    const out = studio()
    const built = buildScene(out)
    const mesh = built.root.children.find((c): c is Mesh => c instanceof Mesh && c.name.startsWith('part:'))
    expect(mesh?.geometry.getAttribute('position').array).toBe(out.parts[0]?.mesh?.positions)
    built.dispose()
  })

  it('splits a part into colour groups where a face has its own colour', () => {
    const out = studio()
    const part = out.parts[0] as Part
    const coloured: Part = { ...part, faceColors: new Map([[0, [1, 0, 0] as const]]) }
    const built = buildScene({ ...out, parts: [coloured] })
    const mesh = built.root.children.find((c): c is Mesh => c instanceof Mesh && c.name.startsWith('part:')) as Mesh
    const mats = mesh.material as Material[]
    expect(Array.isArray(mats)).toBe(true)
    expect(mats.length).toBe(2)
    const total = mesh.geometry.groups.reduce((n, g) => n + g.count, 0)
    expect(total).toBe(expected.triangles * 3)
    built.dispose()
  })

  it('skips hidden parts and keeps a default box for an empty studio', () => {
    const out = studio()
    const hidden: Part = { ...(out.parts[0] as Part), hidden: true }
    const built = buildScene({ ...out, parts: [hidden] })
    expect(built.stats.parts).toBe(0)
    expect(built.bounds.hi[0]).toBe(75)
    built.dispose()
  })
})

/** Two parts: the bracket and a copy of it moved along x (so its box is different), under another id. */
function twoParts(secondHidden = false): StudioOutputs {
  const out = studio()
  const a = out.parts[0] as Part
  const m = a.mesh as NonNullable<Part['mesh']>
  const moved = new Float32Array(m.positions)
  for (let i = 0; i < moved.length; i += 3) moved[i] = (moved[i] as number) + 200
  const b: Part = { ...a, id: 'F9/1', name: 'Second', color: [0.93, 0.66, 0.45], hidden: secondHidden, mesh: { ...m, positions: moved } }
  return { ...out, parts: [a, b] }
}

const partMeshes = (built: ReturnType<typeof buildScene>) => built.root.children.filter((c): c is Mesh => c instanceof Mesh && c.name.startsWith('part:'))
const planeHalfOf = (built: ReturnType<typeof buildScene>): number => {
  const fill = (built.root.getObjectByName('planes')?.children[0] as Group).children[0] as Mesh
  const p = fill.geometry.getAttribute('position')
  return Math.abs(p.getX(1) - p.getX(0)) / 2
}

describe('the eye', () => {
  it('a hidden part is one fewer visible part; every other number of the hook means what it did', () => {
    const shown = buildScene(twoParts(false))
    const hidden = buildScene(twoParts(true))
    expect(shown.stats).toMatchObject({ parts: 2, visibleParts: 2, hiddenParts: 0, visiblePartIds: ['F2/1', 'F9/1'] })
    expect(hidden.stats).toMatchObject({ parts: 1, visibleParts: 1, hiddenParts: 1, visiblePartIds: ['F2/1'] })
    expect(hidden.stats.faces).toBe(shown.stats.faces / 2)
    expect(hidden.stats.edges).toBe(shown.stats.edges / 2)
    shown.dispose()
    hidden.dispose()
  })

  it('a hidden part has no mesh, no edge lines and no BVH in the scene, so nothing can pick it', () => {
    const built = buildScene(twoParts(true))
    expect(partMeshes(built).map((m) => m.userData['partId'])).toEqual(['F2/1'])
    expect(built.root.getObjectByName('part:F9/1')).toBeUndefined()
    expect(built.root.getObjectByName('edges:F9/1')).toBeUndefined()
    const withBvh: string[] = []
    built.root.traverse((o) => o.userData['bvh'] && withBvh.push(o.userData['partId'] as string))
    expect(withBvh).toEqual(['F2/1'])
    built.dispose()
  })

  it('draws a part in the colour of its outputs, and reports it', () => {
    const built = buildScene(twoParts())
    expect(built.stats.partColors['F9/1']).toEqual([0.93, 0.66, 0.45])
    const second = built.root.getObjectByName('part:F9/1') as Mesh
    const base = ((second.material as ShaderMaterial).uniforms['baseColor'] as { value: { x: number; y: number; z: number } }).value
    expect([base.x, base.y, base.z]).toEqual([0.93, 0.66, 0.45])
    built.dispose()
  })
})

describe('the highlight of a selected part', () => {
  const tint = (m: Mesh) => ((m.material as ShaderMaterial).uniforms['tintAmount'] as { value: number }).value
  const edge = (built: ReturnType<typeof buildScene>, id: string) => (built.root.getObjectByName(`edges:${id}`) as LineSegments2).material as LineMaterial

  it('tints the selected part with the selection colour and outlines it; the others are as before', () => {
    const built = buildScene(twoParts(), { selectedPart: 'F9/1' })
    const [a, b] = partMeshes(built) as [Mesh, Mesh]
    expect([tint(a), tint(b)]).toEqual([0, HIGHLIGHT_AMOUNT])
    const t = ((b.material as ShaderMaterial).uniforms['tint'] as { value: { x: number; y: number; z: number } }).value
    expect([t.x, t.y, t.z].map((v) => Math.round(v * 255))).toEqual([255, 199, 64]) // color.selection, #ffc740
    expect(edge(built, 'F9/1').linewidth).toBeGreaterThan(edge(built, 'F2/1').linewidth)
    expect(built.select('F2/1')).toBe(true)
    expect([tint(a), tint(b)]).toEqual([HIGHLIGHT_AMOUNT, 0])
    expect(edge(built, 'F9/1').linewidth).toBe(sceneDraw.edgeWidth) // the outline goes back to the plain width
    expect(edge(built, 'F2/1').linewidth).toBeGreaterThan(sceneDraw.edgeWidth)
    expect(built.select(null)).toBe(false)
    expect([tint(a), tint(b)]).toEqual([0, 0])
    built.dispose()
  })

  it('a selected part the eye hides is not drawn, and select says so', () => {
    const built = buildScene(twoParts(true), { selectedPart: 'F9/1' })
    expect(built.select('F9/1')).toBe(false)
    expect(built.select('F2/1')).toBe(true)
    built.dispose()
  })
})

describe('the default planes of an emptied studio', () => {
  it('keep the size they had when every part is gone (a failed base feature) instead of jumping to the empty size', () => {
    const full = buildScene(studio())
    const before = planeHalfOf(full)
    expect(before).toBeGreaterThan(0)
    expect(full.stats.planeHalf).toBe(before)
    const emptied = buildScene({ ...studio(), parts: [] }, { keepPlaneHalf: full.stats.planeHalf })
    expect(planeHalfOf(emptied)).toBe(before)
    expect(emptied.hasModel).toBe(false)
    // what it did before: the size of the default box, here a different number
    expect(planeHalfOf(buildScene({ ...studio(), parts: [] }))).not.toBe(before)
    full.dispose()
    emptied.dispose()
  })

  it('keep their size when the eye hides every part', () => {
    const full = buildScene(studio())
    const out = studio()
    const allHidden = buildScene({ ...out, parts: out.parts.map((p) => ({ ...p, hidden: true })) }, { keepPlaneHalf: full.stats.planeHalf })
    expect(allHidden.stats.parts).toBe(0)
    expect(planeHalfOf(allHidden)).toBe(planeHalfOf(full))
    full.dispose()
    allHidden.dispose()
  })

  it('a studio opened empty gets the empty-studio size, and a model that comes after sizes them to itself', () => {
    const fresh = buildScene({ ...studio(), parts: [] })
    expect(planeHalfOf(fresh)).toBe(60) // 0.4 of the default 150 mm box
    const back = buildScene(studio(), { keepPlaneHalf: 60 })
    expect(planeHalfOf(back)).not.toBe(60)
    fresh.dispose()
    back.dispose()
  })
})

describe('fit', () => {
  it('puts the whole bounding box inside the view with a margin', () => {
    const built = buildScene(studio())
    for (const [w, h] of [
      [1024, 768],
      [1366, 1024],
      [600, 900],
    ] as const) {
      const cam = new ViewCamera()
      cam.resize(w, h)
      cam.fit(built.bounds)
      const { lo, hi } = built.bounds
      for (const x of [lo[0], hi[0]])
        for (const y of [lo[1], hi[1]])
          for (const z of [lo[2], hi[2]]) {
            const p = cam.project([x, y, z])
            expect(p.x).toBeGreaterThanOrEqual(30)
            expect(p.x).toBeLessThanOrEqual(w - 30)
            expect(p.y).toBeGreaterThanOrEqual(30)
            expect(p.y).toBeLessThanOrEqual(h - 30)
          }
    }
    built.dispose()
  })

  it('keeps Z up on the screen', () => {
    const cam = new ViewCamera()
    cam.resize(800, 600)
    const a = cam.project([0, 0, 0])
    const b = cam.project([0, 0, 10])
    expect(b.y).toBeLessThan(a.y)
    expect(cam.state().up[2]).toBeGreaterThan(0.5)
  })

  it('gives the three.js camera a frustum that matches the scale', () => {
    const cam = new ViewCamera()
    cam.resize(1000, 500)
    cam.scale = 0.5
    cam.apply()
    expect(cam.camera.right - cam.camera.left).toBeCloseTo(500)
    expect(cam.camera.top - cam.camera.bottom).toBeCloseTo(250)
  })
})

describe('fitBoundsFor', () => {
  it('takes in the default planes round the origin when the model sits beside it', () => {
    const model = { lo: [0, 0, 0], hi: [100, 60, 20] } as const
    expect(fitBoundsFor(model, 40)).toEqual({ lo: [-40, -40, -40], hi: [100, 60, 40] })
  })
  it('leaves the planes out when the origin is far from the model', () => {
    const model = { lo: [900, 900, 0], hi: [1000, 960, 20] } as const
    expect(fitBoundsFor(model, 40)).toBe(model)
  })
})
