// @vitest-environment node
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { Mesh } from 'three'
import type { Material } from 'three'
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js'
import { decodeOutputs, isStudio } from '../wire'
import type { Part, StudioOutputs } from '../wire'
import { buildScene } from './scene'
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
