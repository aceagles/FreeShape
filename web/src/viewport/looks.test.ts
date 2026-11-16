// @vitest-environment node
import { Mesh, Points } from 'three'
import type { ShaderMaterial } from 'three'
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js'
import { describe, expect, it } from 'vitest'
import { fixture } from '../test/fixtures'
import type { PartMesh } from '../wire'
import { LOOK_SIZE } from './looks'
import type { LookItem } from './looks'
import { buildScene } from './scene'
import { HIGHLIGHT_AMOUNT, HOVER_AMOUNT } from './shading'

// What the selection and the Aim draw: faces, edges, vertices and planes, selected (amber, outlined) and Aimed (orange).

const out = fixture('bracket').outputs
const part = out.parts[0]!
const mesh = part.mesh as PartMesh
const face = (i: number): LookItem => ({ kind: 'face', part: part.id, index: i })
const edge = (i: number): LookItem => ({ kind: 'edge', part: part.id, index: i })
const vertex = (i: number): LookItem => ({ kind: 'vertex', part: part.id, index: i })
const overlay = (b: ReturnType<typeof buildScene>) => b.root.getObjectByName('looks')!.children

/** LineSegments2 is a Mesh too: a surface is a Mesh that is not a wide line. */
const isSurface = (k: unknown): k is Mesh => k instanceof Mesh && !(k instanceof LineSegments2)
const tint = (m: Mesh) => {
  const u = (m.material as ShaderMaterial).uniforms
  const t = (u['tint'] as { value: { x: number; y: number; z: number } }).value
  return { amount: (u['tintAmount'] as { value: number }).value, rgb: [t.x, t.y, t.z].map((v) => Math.round(v * 255)) }
}

describe('the overlay of the selection and the Aim', () => {
  it('draws nothing when nothing is selected or Aimed', () => {
    const b = buildScene(out)
    expect(b.setLook([], null)).toBeNull()
    expect(overlay(b)).toHaveLength(0)
    b.dispose()
  })

  it('a selected face is its own triangles, tinted with the selection colour, with an outline; the part itself is not tinted', () => {
    const b = buildScene(out)
    b.setLook([face(2)], null)
    const kids = overlay(b)
    const surface = kids.find(isSurface)!
    const [a, z] = mesh.faceRanges[2] as [number, number]
    expect(surface.geometry.index!.count).toBe(3 * (z - a))
    expect(tint(surface)).toEqual({ amount: HIGHLIGHT_AMOUNT, rgb: [255, 199, 64] })
    expect(kids.filter((k) => k instanceof LineSegments2)).toHaveLength(1) // the outline
    const partMesh = b.root.getObjectByName(`part:${part.id}`) as Mesh
    for (const m of Array.isArray(partMesh.material) ? partMesh.material : [partMesh.material]) expect((m as ShaderMaterial).uniforms['tintAmount']!.value).toBe(0)
    b.dispose()
  })

  it('an Aimed face is orange, less strongly tinted, and its outline is orange, not dark: it cannot be taken for a selected one', () => {
    const b = buildScene(out)
    b.setLook([], face(2))
    const kids = overlay(b)
    const surface = kids.find(isSurface)!
    expect(tint(surface)).toEqual({ amount: HOVER_AMOUNT, rgb: [255, 158, 51] })
    expect(HOVER_AMOUNT).toBeLessThan(HIGHLIGHT_AMOUNT)
    const outline = kids.find((k): k is LineSegments2 => k instanceof LineSegments2)!
    expect(outline.material.color.getHex()).toBe(0xff9e33) // orange, not the selected face's dark brown
    b.dispose()
  })

  it('an item that is selected and Aimed draws once, as Aimed', () => {
    const b = buildScene(out)
    b.setLook([face(2), face(3)], face(2))
    const surfaces = overlay(b).filter(isSurface)
    expect(surfaces.map((s) => tint(s).amount).sort()).toEqual([HOVER_AMOUNT, HIGHLIGHT_AMOUNT].sort())
    b.dispose()
  })

  it('a selected edge is a thick amber line over a dark casing; an Aimed edge is one thicker orange line', () => {
    const b = buildScene(out)
    const e = mesh.edges.findIndex((p) => p.length >= 6)
    b.setLook([edge(e)], null)
    const lines = overlay(b).filter((k): k is LineSegments2 => k instanceof LineSegments2)
    expect(lines.map((l) => l.material.linewidth)).toEqual([LOOK_SIZE.edgeCasing, LOOK_SIZE.edgeCore])
    expect(lines[1]!.material.color.getHex()).toBe(0xffc740)
    b.setLook([], edge(e))
    const aimed = overlay(b).filter((k): k is LineSegments2 => k instanceof LineSegments2)
    expect(aimed.map((l) => l.material.linewidth)).toEqual([LOOK_SIZE.edgeHover])
    expect(aimed[0]!.material.color.getHex()).toBe(0xff9e33)
    // wide enough to read on a tablet, and wider than the model's own edges
    expect(LOOK_SIZE.edgeCore).toBeGreaterThanOrEqual(4)
    b.dispose()
  })

  it('a selected vertex is an amber dot in a dark ring, a few pixels across; an Aimed one a single orange dot', () => {
    const b = buildScene(out)
    b.setLook([vertex(1)], null)
    const dots = overlay(b).filter((k): k is Points => k instanceof Points)
    expect(dots).toHaveLength(2)
    expect(dots.map((d) => (d.material as unknown as { size: number }).size)).toEqual([LOOK_SIZE.vertexCasing, LOOK_SIZE.vertexCore])
    const p = dots[0]!.geometry.getAttribute('position')
    expect([p.getX(0), p.getY(0), p.getZ(0)]).toEqual([mesh.vertices[3]!, mesh.vertices[4]!, mesh.vertices[5]!].map((v) => Math.fround(v)))
    b.setLook([], vertex(1))
    expect(overlay(b).filter((k) => k instanceof Points)).toHaveLength(1)
    b.dispose()
  })

  it('a selected plane is a tinted square with an outline; the default planes are known by name', () => {
    const b = buildScene(out)
    b.setLook([{ kind: 'plane', part: null, index: -1, key: 'Front' }], null)
    expect(overlay(b).filter(isSurface)).toHaveLength(1)
    expect(overlay(b).filter((k) => k instanceof LineSegments2)).toHaveLength(1)
    b.dispose()
  })

  it('a part is drawn as a whole: selected tints the part, Aimed tints it orange; setLook says which part is selected', () => {
    const b = buildScene(out)
    const m = b.root.getObjectByName(`part:${part.id}`) as Mesh
    expect(b.setLook([{ kind: 'part', part: part.id, index: -1 }], null)).toBe(part.id)
    expect(tint(m).amount).toBe(HIGHLIGHT_AMOUNT)
    b.setLook([], { kind: 'part', part: part.id, index: -1 })
    expect(tint(m)).toEqual({ amount: HOVER_AMOUNT, rgb: [255, 158, 51] })
    expect(b.setLook([], null)).toBeNull()
    expect(tint(m).amount).toBe(0)
    b.dispose()
  })

  it('items of a part that is not drawn, and an index out of range, draw nothing', () => {
    const b = buildScene(out)
    b.setLook([{ kind: 'face', part: 'nope', index: 0 }, face(999), edge(999), vertex(999)], null)
    expect(overlay(b)).toHaveLength(0)
    b.dispose()
  })

  it('replaces what it drew before and keeps the part\'s own buffers when it does', () => {
    const b = buildScene(out)
    b.setLook([face(0), face(1)], null)
    expect(overlay(b).filter(isSurface)).toHaveLength(2)
    b.setLook([face(1)], null)
    expect(overlay(b).filter(isSurface)).toHaveLength(1)
    const partMesh = b.root.getObjectByName(`part:${part.id}`) as Mesh
    expect(partMesh.geometry.getAttribute('position')).toBeTruthy()
    expect(partMesh.geometry.getAttribute('normal')).toBeTruthy()
    b.dispose()
  })
})
