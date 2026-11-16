import type { APIRequestContext, Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { decodeOutputs, elementName, isStudio } from '../src/wire/index.ts'
import type { Part, PartMesh, StudioOutputs } from '../src/wire/index.ts'
import type { PickHit, PickKind, PickPointer } from '../src/viewport/pick.ts'
import { openApp } from './support/app.ts'
import { closeAll, idOf } from './support/docs.ts'
import { readViewport, settled } from './support/viewport.ts'

// Slice S4, picking (card I4.1) against `freeshape serve`: the viewport hook's `pickAt(x, y, pointer, filter)` is asked
// about positions computed from the Bracket's own geometry (fetched from the server and decoded here), in the fitted
// start view and after a standard view from the cube. Positions are CSS pixels from the canvas's top left.

test.afterEach(async ({ page }) => closeAll(page.request))

type Camera = Awaited<ReturnType<typeof readViewport>>['camera']
type Ask = { x: number; y: number; pointer: PickPointer; filter?: PickKind[] }
type P3 = readonly [number, number, number]

async function bracket(request: APIRequestContext): Promise<{ out: StudioOutputs; part: Part; mesh: PartMesh }> {
  const doc = await idOf(request, 'Bracket')
  const tab = (await (await request.get(`/documents/${doc}/tabs`)).json()).tabs[0].id as string
  const res = await request.get(`/documents/${doc}/tabs/${tab}/outputs`)
  expect(res.status()).toBe(200)
  const body = await res.body()
  const out = decodeOutputs(body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength) as ArrayBuffer)
  if (!isStudio(out)) throw new Error('the Bracket tab is not a Part Studio')
  const part = out.parts[0] as Part
  return { out, part, mesh: part.mesh as PartMesh }
}

/** Where a model point lands in the view, from the camera the hook reports (the same maths as ViewCamera.project). */
function project(c: Camera, p: P3): { x: number; y: number; z: number } {
  const [ux, uy, uz] = c.up
  const [bx, by, bz] = c.back
  const right = [uy * bz - uz * by, uz * bx - ux * bz, ux * by - uy * bx]
  const d = [p[0] - c.target[0], p[1] - c.target[1], p[2] - c.target[2]]
  const dot = (a: number[], b: readonly number[]) => a[0]! * b[0]! + a[1]! * b[1]! + a[2]! * b[2]!
  return { x: c.width / 2 + dot(d, right) / c.scale, y: c.height / 2 - dot(d, c.up) / c.scale, z: dot(d, c.back) }
}

async function ask(page: Page, reqs: Ask[]): Promise<(PickHit | null)[]> {
  return page.evaluate((rs) => rs.map((r) => window.__freeshapeViewport!.pickAt(r.x, r.y, r.pointer, r.filter)), reqs)
}

const vertexAt = (mesh: PartMesh, v: number): P3 => [mesh.vertices[3 * v]!, mesh.vertices[3 * v + 1]!, mesh.vertices[3 * v + 2]!]

/** Centre of each face's biggest triangle: a point the face owns. */
function faceMiddle(mesh: PartMesh, f: number): P3 | null {
  const [a, b] = mesh.faceRanges[f] as [number, number]
  let best: P3 | null = null
  let bestArea = 0
  for (let t = a; t < b; t++) {
    const v = [0, 1, 2].map((k) => {
      const i = (mesh.indices[3 * t + k] as number) * 3
      return [mesh.positions[i]!, mesh.positions[i + 1]!, mesh.positions[i + 2]!]
    })
    const e1 = [v[1]![0]! - v[0]![0]!, v[1]![1]! - v[0]![1]!, v[1]![2]! - v[0]![2]!]
    const e2 = [v[2]![0]! - v[0]![0]!, v[2]![1]! - v[0]![1]!, v[2]![2]! - v[0]![2]!]
    const area = Math.hypot(e1[1]! * e2[2]! - e1[2]! * e2[1]!, e1[2]! * e2[0]! - e1[0]! * e2[2]!, e1[0]! * e2[1]! - e1[1]! * e2[0]!)
    if (area > bestArea) {
      bestArea = area
      best = [0, 1, 2].map((k) => (v[0]![k]! + v[1]![k]! + v[2]![k]!) / 3) as unknown as P3
    }
  }
  return best
}

/** The same checks in whichever view is on screen. */
async function checkPicks(page: Page, request: APIRequestContext, label: string): Promise<void> {
  const { part, mesh } = await bracket(request)
  const cam = (await settled(page)).camera
  const at = (p: P3) => project(cam, p)
  const inside = (p: { x: number; y: number }) => p.x > 5 && p.y > 5 && p.x < cam.width - 5 && p.y < cam.height - 5

  // vertices: the position of a vertex, for a pen, gives that vertex with its Element name
  const vertices = Array.from({ length: mesh.vertices.length / 3 }, (_, v) => v)
  const vAsk = vertices.map((v) => ({ ...at(vertexAt(mesh, v)), pointer: 'pen' as const }))
  const vHits = await ask(page, vAsk)
  const seen = vertices.filter((v) => vHits[v]?.kind === 'vertex' && vHits[v]?.index === v)
  expect(seen.length, `${label}: vertices picked at their own position`).toBeGreaterThan(3)
  for (const v of seen) {
    expect(vHits[v]).toMatchObject({ kind: 'vertex', part: part.id, name: elementName(part, 'vertex', v) ?? null })
    expect(vHits[v]!.point[0]).toBeCloseTo(vertexAt(mesh, v)[0], 3)
  }
  console.log(`${label}: ${seen.length} of ${vertices.length} vertices picked at their own position; e.g. vertex ${seen[0]} = ${vHits[seen[0]!]?.name}`)

  // faces: the middle of a face, with only faces asked for, gives that face and its name (a face the view hides gives another)
  const faces = Array.from({ length: mesh.faceRanges.length }, (_, f) => f)
  const mids = faces.map((f) => faceMiddle(mesh, f))
  const fHits = await ask(page, mids.map((m) => ({ ...(m ? at(m) : { x: -1, y: -1 }), pointer: 'pen' as const, filter: ['face' as const] })))
  const seenFaces = faces.filter((f) => mids[f] && fHits[f]?.kind === 'face' && fHits[f]?.index === f && inside(at(mids[f]!)))
  expect(seenFaces.length, `${label}: faces picked at their middle`).toBeGreaterThan(2)
  for (const f of seenFaces) expect(fHits[f]).toMatchObject({ part: part.id, name: elementName(part, 'face', f) })
  console.log(`${label}: ${seenFaces.length} of ${faces.length} faces picked at their middle; e.g. face ${seenFaces[0]} = ${fHits[seenFaces[0]!]?.name?.slice(0, 40)}`)

  // the filter: a vertex position with faces only gives a face, and with everything gives the vertex
  const v0 = seen[0]!
  const p0 = at(vertexAt(mesh, v0))
  const [onlyFaces, anything, onlyEdges] = await ask(page, [
    { ...p0, pointer: 'pen', filter: ['face'] },
    { ...p0, pointer: 'pen' },
    { ...p0, pointer: 'pen', filter: ['edge'] },
  ])
  expect(anything?.kind).toBe('vertex')
  expect(onlyEdges?.kind).toBe('edge')
  expect(onlyFaces == null || onlyFaces.kind === 'face').toBe(true)

  // edges: the middle of a straight edge, with only edges asked for
  const edgeIds = mesh.edges.map((_, e) => e).filter((e) => mesh.edges[e]!.length === 6)
  const mid = (e: number): P3 => {
    const p = mesh.edges[e] as Float32Array
    return [(p[0]! + p[3]!) / 2, (p[1]! + p[4]!) / 2, (p[2]! + p[5]!) / 2]
  }
  const eHits = await ask(page, edgeIds.map((e) => ({ ...at(mid(e)), pointer: 'pen' as const, filter: ['edge' as const] })))
  const seenEdges = edgeIds.filter((e, i) => eHits[i]?.kind === 'edge' && eHits[i]?.index === e)
  expect(seenEdges.length, `${label}: edges picked at their middle`).toBeGreaterThan(3)
  for (const e of seenEdges) {
    const hit = eHits[edgeIds.indexOf(e)]!
    expect(hit).toMatchObject({ part: part.id, name: elementName(part, 'edge', e) ?? null })
    expect(hit.distancePx!).toBeLessThan(0.01)
  }
  console.log(`${label}: ${seenEdges.length} of ${edgeIds.length} straight edges picked at their middle`)

  // pen against finger: a vertex 15 px away is the finger's and not the pen's
  let differs = 0
  for (const v of seen) {
    const p = at(vertexAt(mesh, v))
    const dirs = [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
    ] as const
    const reqs = dirs.flatMap(([dx, dy]) => [
      { x: p.x + dx * 15, y: p.y + dy * 15, pointer: 'pen' as const, filter: ['vertex' as const] },
      { x: p.x + dx * 15, y: p.y + dy * 15, pointer: 'touch' as const, filter: ['vertex' as const] },
    ])
    const hits = await ask(page, reqs)
    for (let i = 0; i < dirs.length; i++) if (hits[2 * i] === null && hits[2 * i + 1]?.kind === 'vertex') differs++
  }
  expect(differs, `${label}: a vertex 15 px away: finger yes, pen no`).toBeGreaterThan(0)
}

test('picks in the fitted start view land on the right faces, edges and vertices', async ({ page, request }) => {
  await openApp(page)
  await checkPicks(page, request, 'start view')
})

test('picks in a standard view from the cube land on the right faces, edges and vertices', async ({ page, request }) => {
  await openApp(page)
  await page.getByRole('button', { name: /View cube/ }).click()
  await page.getByRole('menuitem', { name: 'Top', exact: true }).click()
  await expect.poll(async () => (await readViewport(page)).camera.back[2]).toBeGreaterThan(0.99)
  await checkPicks(page, request, 'top view')
})

test('an edge or vertex on the far side of the model is not picked through it', async ({ page, request }) => {
  await openApp(page)
  const { mesh } = await bracket(request)
  const cam = (await settled(page)).camera
  const edgeMids = mesh.edges
    .map((p, e) => ({ e, p }))
    .filter(({ p }) => p.length >= 6)
    .map(({ e, p }): { e: number; at: P3 } => ({ e, at: [(p[0]! + p[3]!) / 2, (p[1]! + p[4]!) / 2, (p[2]! + p[5]!) / 2] }))
  // the face the pointer is over, at the position of each edge's middle: an edge far behind that face is hidden
  const over = await ask(page, edgeMids.map(({ at }) => ({ ...project(cam, at), pointer: 'mouse' as const, filter: ['face' as const] })))
  let hidden = 0
  const asks: Ask[] = []
  const hiddenIds: number[] = []
  edgeMids.forEach(({ e, at }, i) => {
    const face = over[i]
    if (!face) return
    const gap = (project(cam, face.point as P3).z - project(cam, at).z) / cam.scale // pixels of depth
    if (gap < 45) return
    hidden++
    hiddenIds.push(e)
    for (const pointer of ['pen', 'touch', 'mouse'] as const) asks.push({ ...project(cam, at), pointer, filter: ['edge'] })
  })

  expect(hidden, 'edges well behind a face in the start view').toBeGreaterThan(0)
  const hits = await ask(page, asks)
  hits.forEach((h, i) => expect(h?.kind === 'edge' && h.index === hiddenIds[Math.floor(i / 3)], `edge ${hiddenIds[Math.floor(i / 3)]} is behind the model`).toBe(false))
  // and the same for vertices
  const vs = Array.from({ length: mesh.vertices.length / 3 }, (_, v) => v)
  const vOver = await ask(page, vs.map((v) => ({ ...project(cam, vertexAt(mesh, v)), pointer: 'mouse' as const, filter: ['face' as const] })))
  const vAsks: Ask[] = []
  const vIds: number[] = []
  vs.forEach((v) => {
    const face = vOver[v]
    if (!face || (project(cam, face.point as P3).z - project(cam, vertexAt(mesh, v)).z) / cam.scale < 45) return
    vIds.push(v)
    vAsks.push({ ...project(cam, vertexAt(mesh, v)), pointer: 'touch', filter: ['vertex'] })
  })
  expect(vIds.length).toBeGreaterThan(0)
  const vHits = await ask(page, vAsks)
  vHits.forEach((h, i) => expect(h?.kind === 'vertex' && h.index === vIds[i], `vertex ${vIds[i]} is behind the model`).toBe(false))
})
