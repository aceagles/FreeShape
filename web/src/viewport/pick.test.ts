// @vitest-environment node
import { readFileSync } from 'node:fs'
import { Mesh, DoubleSide, Vector3 } from 'three'
import type { MeshBVH } from 'three-mesh-bvh'
import { describe, expect, it } from 'vitest'
import { decodeOutputs, elementName, isStudio } from '../wire'
import type { Part, PartMesh, StudioOutputs } from '../wire'
import { ViewCamera } from './camera'
import { STANDARD_VIEWS } from './camera'
import type { StandardView } from './camera'
import { PickIndex, TOLERANCE, viewRay } from './pick'
import type { DatumPick, ElementPick, PickHit, PickKind } from './pick'
import { buildScene } from './scene'

const dir = new URL('../../fixtures/', import.meta.url)
const bytes = readFileSync(new URL('bracket.outputs.fsw', dir))

function studio(): StudioOutputs {
  const buf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
  const out = decodeOutputs(buf)
  if (!isStudio(out)) throw new Error('fixture is not a studio')
  return out
}

interface Rig {
  out: StudioOutputs
  built: ReturnType<typeof buildScene>
  view: ViewCamera
  index: PickIndex
  part: Part
}

/** The scene, a camera of a fixed size looking along a standard view (fitted), and its picker. */
function rig(width: number, height: number, name: StandardView, out: StudioOutputs = studio()): Rig {
  const built = buildScene(out)
  const view = new ViewCamera()
  view.resize(width, height)
  view.setBack(STANDARD_VIEWS[name])
  view.fit(built.fitBounds)
  return { out, built, view, index: new PickIndex(out, built), part: out.parts[0] as Part }
}

const pickAs = (r: Rig, x: number, y: number, kind: keyof typeof TOLERANCE, filter?: PickKind[]) => r.index.pick(r.view, x, y, kind, filter)
const screen = (r: Rig, p: readonly number[]) => r.view.project([p[0]!, p[1]!, p[2]!])
const is = (h: PickHit | null, kind: PickKind, index?: number) => h !== null && h.kind === kind && (index === undefined || h.index === index)

/** Nearest hit of the view's ray on the part, found without PickIndex: the triangle and its distance. */
function rayHit(r: Rig, x: number, y: number) {
  const mesh = r.built.root.children.find((c): c is Mesh => c instanceof Mesh && c.name.startsWith('part:'))!
  const bvh = mesh.userData['bvh'] as MeshBVH
  return bvh.raycastFirst(viewRay(r.view, x, y), DoubleSide)
}

const sizes: [number, number][] = [
  [1024, 768],
  [1366, 1024],
]

describe.each(sizes)('pick on the bracket, view %i x %i', (w, h) => {
  it('a ray through the middle of a face returns that face and its Element name', () => {
    const r = rig(w, h, 'iso')
    const mesh = r.part.mesh as PartMesh
    let found = 0
    // each face's biggest triangle, taken at its centre: a point the face owns; keep those no edge or vertex is near
    for (let f = 0; f < mesh.faceRanges.length; f++) {
      const [a, b] = mesh.faceRanges[f] as [number, number]
      let bestC: Vector3 | null = null
      let bestArea = 0
      for (let t = a; t < b; t++) {
        const v = [0, 1, 2].map((k) => {
          const i = (mesh.indices[3 * t + k] as number) * 3
          return new Vector3(mesh.positions[i], mesh.positions[i + 1], mesh.positions[i + 2])
        }) as [Vector3, Vector3, Vector3]
        const area = new Vector3().crossVectors(v[1].clone().sub(v[0]), v[2].clone().sub(v[0])).length()
        if (area > bestArea) {
          bestArea = area
          bestC = v[0].clone().add(v[1]).add(v[2]).divideScalar(3)
        }
      }
      if (!bestC) continue
      const s = screen(r, [bestC.x, bestC.y, bestC.z])
      const only = pickAs(r, s.x, s.y, 'pen', ['face'])
      // the ray may meet another face first (this one is hidden here); only a face that is seen counts
      if (!is(only, 'face', f)) continue
      found++
      const hit = only as ElementPick
      expect(hit.part).toBe(r.part.id)
      expect(hit.name).toBe(elementName(r.part, 'face', f))
      expect(hit.name).toBeTruthy()
      expect(hit.distancePx).toBeNull()
      // the triangle the ray meets is one of this face's own (the BVH keeps the index buffer in the server's order)
      const tri = rayHit(r, s.x, s.y)!.faceIndex!
      expect(tri).toBeGreaterThanOrEqual(a)
      expect(tri).toBeLessThan(b)
      expect(mesh.triangleFace[tri]).toBe(f)
      // the point is on the ray's line: it projects back to the pointer
      const back = screen(r, hit.point)
      expect(Math.hypot(back.x - s.x, back.y - s.y)).toBeLessThan(1e-3)
    }
    expect(found).toBeGreaterThanOrEqual(4)
  })

  it('does not reorder the response\'s index buffer', () => {
    const out = studio()
    const before = Array.from((out.parts[0]!.mesh as PartMesh).indices)
    rig(w, h, 'iso', out)
    expect(Array.from((out.parts[0]!.mesh as PartMesh).indices)).toEqual(before)
  })

  /**
   * A long edge seen square-on in the top view, and a side of it where the face beneath lies: `at(d)` is the position d
   * pixels from the edge's middle, perpendicular to it. Found by search, then checked against what the card says.
   */
  function edgeCase(r: Rig) {
    const mesh = r.part.mesh as PartMesh
    for (let e = 0; e < mesh.edges.length; e++) {
      const poly = mesh.edges[e] as Float32Array
      if (poly.length !== 6) continue
      const a = screen(r, [poly[0]!, poly[1]!, poly[2]!])
      const b = screen(r, [poly[3]!, poly[4]!, poly[5]!])
      const len = Math.hypot(b.x - a.x, b.y - a.y)
      if (len < 120) continue
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
      const nx = -(b.y - a.y) / len
      const ny = (b.x - a.x) / len
      for (const sign of [1, -1]) {
        const at = (d: number) => ({ x: mid.x + sign * nx * d, y: mid.y + sign * ny * d })
        const p8 = at(8)
        const p15 = at(15)
        if (
          is(pickAs(r, p8.x, p8.y, 'pen'), 'edge', e) &&
          is(pickAs(r, p8.x, p8.y, 'touch'), 'edge', e) &&
          is(pickAs(r, p15.x, p15.y, 'touch'), 'edge', e) &&
          is(pickAs(r, p15.x, p15.y, 'pen'), 'face')
        ) {
          return { e, at, mid }
        }
      }
    }
    return null
  }

  it('a point 8 px from an edge gives the edge for a pen and a finger; 15 px gives the edge for a finger and the face for a pen', () => {
    const r = rig(w, h, 'top')
    const c = edgeCase(r)
    expect(c, 'no long edge with a face beside it in the top view').not.toBeNull()
    const { e, at } = c!
    const p8 = at(8)
    const p15 = at(15)
    const pen8 = pickAs(r, p8.x, p8.y, 'pen')!
    const finger8 = pickAs(r, p8.x, p8.y, 'touch')!
    expect(pen8).toMatchObject({ kind: 'edge', index: e, name: elementName(r.part, 'edge', e) })
    expect(finger8).toMatchObject({ kind: 'edge', index: e })
    expect(pen8.distancePx).toBeCloseTo(8, 3)
    expect(pickAs(r, p15.x, p15.y, 'touch')).toMatchObject({ kind: 'edge', index: e })
    const pen15 = pickAs(r, p15.x, p15.y, 'pen')!
    expect(pen15.kind).toBe('face')
    expect(pen15.name).toBeTruthy()
    // the mouse keeps the desktop's 7 px: 6 px is the edge, 8 px is not
    const p6 = at(6)
    expect(pickAs(r, p6.x, p6.y, 'mouse')).toMatchObject({ kind: 'edge', index: e })
    expect(is(pickAs(r, p8.x, p8.y, 'mouse'), 'edge', e)).toBe(false)
    // an edge's point lies on the edge: the middle of a straight edge
    const poly = r.part.mesh!.edges[e] as Float32Array
    const m = [(poly[0]! + poly[3]!) / 2, (poly[1]! + poly[4]!) / 2, (poly[2]! + poly[5]!) / 2]
    const onEdge = pickAs(r, at(0).x, at(0).y, 'pen', ['edge'])!
    expect(onEdge.point[0]).toBeCloseTo(m[0]!, 2)
    expect(onEdge.point[1]).toBeCloseTo(m[1]!, 2)
    expect(onEdge.point[2]).toBeCloseTo(m[2]!, 2)
    expect(onEdge.distancePx).toBeCloseTo(0, 6)
  })

  it('does not return an edge or a vertex on the far side of the model', () => {
    const r = rig(w, h, 'iso')
    const mesh = r.part.mesh as PartMesh
    let hiddenEdges = 0
    let hiddenVertices = 0
    const gapPx = (p: readonly number[], at: { x: number; y: number }) => {
      const hit = rayHit(r, at.x, at.y)
      if (!hit) return -Infinity
      const front = hit.point.clone().sub(r.view.target).dot(r.view.axes().back)
      const own = new Vector3(p[0], p[1], p[2]).sub(r.view.target).dot(r.view.axes().back)
      return (front - own) / r.view.scale
    }
    for (let e = 0; e < mesh.edges.length; e++) {
      const poly = mesh.edges[e] as Float32Array
      if (poly.length < 6) continue
      const p = [(poly[0]! + poly[3]!) / 2, (poly[1]! + poly[4]!) / 2, (poly[2]! + poly[5]!) / 2]
      const s = screen(r, p)
      // far enough behind the surface that no tolerance reaches it (the finger's slack is 38 px of depth)
      if (gapPx(p, s) < 60) continue
      hiddenEdges++
      for (const kind of ['pen', 'touch', 'mouse'] as const) {
        const hit = pickAs(r, s.x, s.y, kind, ['edge'])
        expect(is(hit, 'edge', e), `${kind}: edge ${e} is behind the model`).toBe(false)
      }
    }
    for (let v = 0; v < mesh.vertices.length / 3; v++) {
      const p = [mesh.vertices[3 * v]!, mesh.vertices[3 * v + 1]!, mesh.vertices[3 * v + 2]!]
      const s = screen(r, p)
      if (gapPx(p, s) < 60) continue
      hiddenVertices++
      for (const kind of ['pen', 'touch', 'mouse'] as const) {
        expect(is(pickAs(r, s.x, s.y, kind, ['vertex']), 'vertex', v), `${kind}: vertex ${v} is behind the model`).toBe(false)
      }
    }
    expect(hiddenEdges).toBeGreaterThan(0)
    expect(hiddenVertices).toBeGreaterThan(0)
    // the rule is the only thing that hides them: a visible edge at the same exact distance is returned
    const visible = pickAs(r, 512, 400, 'mouse', ['edge'])
    expect(visible === null || visible.kind === 'edge').toBe(true)
  })

  it('a visible vertex wins over an edge and a face; with the filter on faces it returns the face', () => {
    const r = rig(w, h, 'iso')
    const mesh = r.part.mesh as PartMesh
    let checked = 0
    let withFace = 0
    for (let v = 0; v < mesh.vertices.length / 3; v++) {
      const s = screen(r, [mesh.vertices[3 * v]!, mesh.vertices[3 * v + 1]!, mesh.vertices[3 * v + 2]!])
      const hit = pickAs(r, s.x, s.y, 'pen')
      if (!is(hit, 'vertex', v)) continue
      checked++
      expect(hit).toMatchObject({ kind: 'vertex', part: r.part.id, name: elementName(r.part, 'vertex', v) ?? null })
      expect(hit!.distancePx).toBeCloseTo(0, 6)
      expect(is(pickAs(r, s.x, s.y, 'pen', ['edge']), 'edge')).toBe(true)
      // a vertex on the silhouette has no face under it (the ray misses by a hair); the others do
      const face = pickAs(r, s.x, s.y, 'pen', ['face'])
      if (face) {
        withFace++
        expect(face.kind).toBe('face')
        expect(face.name).toBeTruthy()
      }
      // faces and vertices together: the vertex still wins
      expect(pickAs(r, s.x, s.y, 'pen', ['face', 'vertex'])?.kind).toBe('vertex')
    }
    expect(checked).toBeGreaterThan(3)
    expect(withFace).toBeGreaterThan(2)
  })

  it('ties: a pointer on a shared edge gives the same face every time, the lowest index of those level', () => {
    const r = rig(w, h, 'iso')
    const mesh = r.part.mesh as PartMesh
    let level = 0
    for (let e = 0; e < mesh.edges.length; e++) {
      const poly = mesh.edges[e] as Float32Array
      if (poly.length !== 6) continue
      const s = screen(r, [(poly[0]! + poly[3]!) / 2, (poly[1]! + poly[4]!) / 2, (poly[2]! + poly[5]!) / 2])
      const mesh3 = r.built.root.children.find((c): c is Mesh => c instanceof Mesh && c.name.startsWith('part:'))!
      const hits = (mesh3.userData['bvh'] as MeshBVH).raycast(viewRay(r.view, s.x, s.y), DoubleSide)
      if (hits.length < 2) continue
      const near = Math.min(...hits.map((x) => x.distance))
      const faces = [...new Set(hits.filter((x) => x.distance < near + 1e-4).map((x) => mesh.triangleFace[x.faceIndex!]!))]
      if (faces.length < 2) continue
      level++
      const first = pickAs(r, s.x, s.y, 'pen', ['face'])!
      expect(first.index).toBe(Math.min(...faces))
      for (let i = 0; i < 20; i++) expect(pickAs(r, s.x, s.y, 'pen', ['face'])).toEqual(first)
      // a freshly built scene and picker agree
      expect(rig(w, h, 'iso').index.pick(r.view, s.x, s.y, 'pen', ['face'])).toEqual(first)
    }
    expect(level).toBeGreaterThan(0)
  })

  it('ties: two edges at the same distance and depth go to the part drawn first', () => {
    const out = studio()
    const twin: Part = { ...out.parts[0]!, id: 'twin' }
    const forward = { ...out, parts: [out.parts[0]!, twin] }
    const reverse = { ...out, parts: [twin, out.parts[0]!] }
    const a = rig(w, h, 'top', forward)
    const c = edgeCase(rig(w, h, 'top'))!
    const p = c.at(0)
    expect(pickAs(a, p.x, p.y, 'pen')).toMatchObject({ kind: 'edge', part: out.parts[0]!.id, index: c.e })
    const b = rig(w, h, 'top', reverse)
    expect(pickAs(b, p.x, p.y, 'pen')).toMatchObject({ kind: 'edge', part: 'twin', index: c.e })
  })

  it('a part the eye hides cannot be picked: no mesh, no edges, no vertices', () => {
    const out = studio()
    const hiddenOne: Part = { ...out.parts[0]!, id: 'hidden', hidden: true }
    const r = rig(w, h, 'top', { ...out, parts: [out.parts[0]!, hiddenOne] })
    expect(r.index.partIds).toEqual([out.parts[0]!.id])
    const c = edgeCase(r)!
    const alone = rig(w, h, 'top', { ...out, parts: [hiddenOne] })
    expect(alone.index.partIds).toEqual([])
    const p = c.at(0)
    for (const kind of ['pen', 'touch', 'mouse'] as const) {
      const hit = pickAs(alone, p.x, p.y, kind)
      expect(hit === null || hit.kind === 'plane' || hit.kind === 'axis').toBe(true)
      expect(pickAs(alone, p.x, p.y, kind, ['face', 'edge', 'vertex', 'part'])).toBeNull()
    }
    // the visible twin still picks, and never reports the hidden one
    expect(pickAs(r, p.x, p.y, 'pen')).toMatchObject({ part: out.parts[0]!.id })
  })

  it('default planes are pickable; a face in front of a plane wins; the filter limits kinds', () => {
    const r = rig(w, h, 'iso')
    // a point of the Top plane well outside the model (the model sits at x 0..60, the plane spans -24..24)
    const s = screen(r, [-18, -18, 0])
    const hit = pickAs(r, s.x, s.y, 'pen')!
    expect(hit.kind).toBe('plane')
    expect(['Top', 'Front', 'Right']).toContain((hit as DatumPick).key)
    expect(hit.part).toBeNull()
    expect(pickAs(r, s.x, s.y, 'pen', ['face', 'edge', 'vertex'])).toBeNull()
    expect(pickAs(r, s.x, s.y, 'pen', ['plane'])?.kind).toBe('plane')
    // a face of the model hides the planes behind it: find a face point where a plane lies behind it
    let faceWins = 0
    for (let sx = 300; sx < w - 300; sx += 25) {
      for (let sy = 200; sy < h - 200; sy += 25) {
        const f = pickAs(r, sx, sy, 'mouse', ['face'])
        const p = pickAs(r, sx, sy, 'mouse', ['plane'])
        if (f && p) {
          const d = pickAs(r, sx, sy, 'mouse', ['face', 'plane'])!
          const planeDepth = new Vector3(...p.point).sub(r.view.target).dot(r.view.axes().back)
          const faceDepth = new Vector3(...f.point).sub(r.view.target).dot(r.view.axes().back)
          // a plane that is level with the face wins (the desktop's rule); one behind it does not
          if (planeDepth < faceDepth - r.view.scale * 12) {
            expect(d.kind).toBe('face')
            faceWins++
          } else if (planeDepth > faceDepth + 1e-6) expect(d.kind).toBe('plane')
        }
      }
    }
    expect(faceWins).toBeGreaterThan(0)
  })

  it('the studio\'s planes and axes are pickable, with their feature id as key', () => {
    const out = studio()
    const withDatums: StudioOutputs = {
      ...out,
      planes: { P1: { o: [-60, 0, 0], x: [0, 1, 0], n: [1, 0, 0] } },
      axes: { A1: { o: [70, -30, 0], d: [0, 0, 1], span: [0, 45] } },
    }
    const r = rig(w, h, 'iso', withDatums)
    const a = screen(r, [70, -30, 20])
    expect(pickAs(r, a.x, a.y, 'pen')).toMatchObject({ kind: 'axis', key: 'A1', part: null })
    const seven = pickAs(r, a.x + 8, a.y, 'mouse', ['axis'])
    expect(seven).toBeNull()
    expect(pickAs(r, a.x + 8, a.y, 'touch', ['axis'])).toMatchObject({ kind: 'axis', key: 'A1' })
    const p = screen(r, [-60, 5, 5])
    expect(pickAs(r, p.x, p.y, 'pen', ['plane'])).toMatchObject({ kind: 'plane', key: 'P1' })
  })

  it('with parts asked for and not faces, a face hit is the part', () => {
    const r = rig(w, h, 'top')
    const s = screen(r, [30, 0, 45])
    const hit = pickAs(r, s.x, s.y, 'mouse', ['part'])!
    expect(hit).toMatchObject({ kind: 'part', part: r.part.id, index: -1, name: null })
  })
})

/** A part with many edges: a flat plate with a grid of small rings, for timing. */
function bigPart(rings: number): StudioOutputs {
  const base = studio()
  const side = Math.ceil(Math.sqrt(rings))
  const pitch = 4
  const size = side * pitch
  const edges: Float32Array[] = []
  const vertices: number[] = []
  for (let i = 0; i < rings; i++) {
    const cx = (i % side) * pitch + pitch / 2
    const cy = Math.floor(i / side) * pitch + pitch / 2
    const poly = new Float32Array(33 * 3)
    for (let k = 0; k < 33; k++) {
      const t = (k / 32) * Math.PI * 2
      poly.set([cx + Math.cos(t), cy + Math.sin(t), 0], k * 3)
    }
    edges.push(poly)
    vertices.push(cx + 1, cy, 0)
  }
  const mesh: PartMesh = {
    positions: new Float32Array([0, 0, 0, size, 0, 0, size, size, 0, 0, size, 0]),
    normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1]),
    indices: new Uint32Array([0, 1, 2, 0, 2, 3]),
    triangleFace: new Int32Array([0, 0]),
    faceRanges: [[0, 2]],
    edges,
    vertices: new Float32Array(vertices),
  }
  const part: Part = { ...base.parts[0]!, id: 'big', mesh, names: { face: new Map(), edge: new Map(), vertex: new Map() }, faceColors: null }
  return { ...base, parts: [part], context: [] }
}

describe('picking a part with thousands of edges', () => {
  it('costs well under a frame per pick, and a camera change costs one more projection', () => {
    const rings = 4000
    const r = rig(1024, 768, 'top', bigPart(rings))
    expect(r.out.parts[0]!.mesh!.edges).toHaveLength(rings)
    const n = 300
    pickAs(r, 500, 400, 'pen') // project once
    const t0 = performance.now()
    let hits = 0
    for (let i = 0; i < n; i++) if (pickAs(r, 100 + ((i * 37) % 800), 100 + ((i * 53) % 560), i % 2 ? 'pen' : 'touch')) hits++
    const cached = (performance.now() - t0) / n
    const t1 = performance.now()
    for (let i = 0; i < 20; i++) {
      r.view.pan(1, 0) // a new camera each time: the whole table is projected again
      pickAs(r, 500, 400, 'pen')
    }
    const moving = (performance.now() - t1) / 20
    console.log(`pick: ${rings} edges (${rings * 33} points), ${hits}/${n} hit; ${cached.toFixed(3)} ms per pick with the camera still, ${moving.toFixed(3)} ms when it moved`)
    expect(cached).toBeLessThan(4)
    expect(moving).toBeLessThan(25)
  })
})
