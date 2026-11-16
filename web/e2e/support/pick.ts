import type { APIRequestContext, Page } from '@playwright/test'
import type { PickHit, PickKind, PickPointer } from '../../src/viewport/pick.ts'
import { readViewport, settled } from './viewport.ts'

/**
 * Positions on the Bracket for pointer sequences: where a face, an edge, a vertex or a plane is on screen in the view the
 * page is showing, found by asking the viewport hook's `pickAt` over the view, and with the camera it reports. Nothing
 * here decodes the model, so the screenshot script (plain Node) can use it too. Positions are CSS pixels from the canvas's
 * top left, as `sendPointer` takes them.
 */
export type P3 = readonly [number, number, number]
export interface Spot {
  x: number
  y: number
}
export interface FaceSpot extends Spot {
  face: number
  name: string
}

type Camera = Awaited<ReturnType<typeof readViewport>>['camera']

/** Where a model point lands in the view (the same maths as ViewCamera.project). */
export function project(c: Camera, p: P3): Spot & { z: number } {
  const [ux, uy, uz] = c.up
  const [bx, by, bz] = c.back
  const right = [uy * bz - uz * by, uz * bx - ux * bz, ux * by - uy * bx]
  const d = [p[0] - c.target[0], p[1] - c.target[1], p[2] - c.target[2]]
  const dot = (a: number[], b: readonly number[]) => a[0]! * b[0]! + a[1]! * b[1]! + a[2]! * b[2]!
  return { x: c.width / 2 + dot(d, right) / c.scale, y: c.height / 2 - dot(d, c.up) / c.scale, z: dot(d, c.back) }
}

/** What a pointer of this kind picks (with no filter) at each position, in the page. */
export function pickAll(page: Page, spots: Spot[], pointer: PickPointer, filter?: PickKind[]): Promise<(PickHit | null)[]> {
  return page.evaluate(({ spots, pointer, filter }) => spots.map((s) => window.__freeshapeViewport!.pickAt(s.x, s.y, pointer, filter)), { spots, pointer, filter })
}

/**
 * Faces that this kind of pointer picks as a face with no filter (so no edge, vertex, plane or axis wins there), found by
 * scanning the view on a grid: one spot per face in the order of the faces, the one nearest the middle of the positions that
 * pick it. A face the view hides, or only the planes cover, has none.
 */
export async function faceSpots(page: Page, _request: APIRequestContext, pointer: PickPointer): Promise<FaceSpot[]> {
  const cam = (await settled(page)).camera
  const grid: Spot[] = []
  for (let y = 8; y < cam.height; y += 10) for (let x = 8; x < cam.width; x += 10) grid.push({ x, y })
  const hits = await pickAll(page, grid, pointer)
  const byFace = new Map<number, { spot: Spot; name: string }[]>()
  hits.forEach((h, i) => {
    if (h?.kind !== 'face' || !h.name) return
    const list = byFace.get(h.index) ?? []
    list.push({ spot: grid[i]!, name: h.name })
    byFace.set(h.index, list)
  })
  return [...byFace.entries()]
    .sort(([a], [b]) => a - b)
    .map(([face, list]) => {
      // the position nearest the middle of the positions that pick this face
      const cx = list.reduce((t, c) => t + c.spot.x, 0) / list.length
      const cy = list.reduce((t, c) => t + c.spot.y, 0) / list.length
      const mid = list.reduce((best, c) => (Math.hypot(c.spot.x - cx, c.spot.y - cy) < Math.hypot(best.spot.x - cx, best.spot.y - cy) ? c : best))
      return { ...mid.spot, face, name: mid.name }
    })
}

/** A position in the view where nothing is picked by this kind of pointer (no model, no plane, no axis). */
export async function emptySpot(page: Page, pointer: PickPointer): Promise<Spot> {
  const cam = (await settled(page)).camera
  const grid: Spot[] = []
  for (let y = 8; y < cam.height; y += 16) for (let x = 8; x < cam.width; x += 16) grid.push({ x, y })
  const hits = await pickAll(page, grid, pointer)
  const i = hits.findIndex((h) => h === null)
  if (i < 0) throw new Error('every position in the view picks something')
  return grid[i]!
}

const grid = (cam: Camera, step: number, margin = 8): Spot[] => {
  const out: Spot[] = []
  for (let y = margin; y < cam.height - margin; y += step) for (let x = margin; x < cam.width - margin; x += step) out.push({ x, y })
  return out
}

/**
 * A spot where this kind of pointer picks an edge (and nothing nearer wins): on the edge that the most positions of a
 * coarse scan reach, so a long one in view, at the screen position of the nearest point of the edge.
 */
export async function edgeSpot(page: Page, _request: APIRequestContext, pointer: PickPointer): Promise<Spot & { edge: number; name: string }> {
  const cam = (await settled(page)).camera
  const scan = grid(cam, 14)
  const near = await pickAll(page, scan, pointer, ['edge'])
  const onLine = near.flatMap((h) => (h?.kind === 'edge' ? [{ h, at: project(cam, h.point as P3) }] : []))
  const confirmed = await pickAll(page, onLine.map((c) => c.at), pointer)
  const votes = new Map<number, number>()
  const good = onLine.filter((c, i) => confirmed[i]?.kind === 'edge' && confirmed[i]?.index === c.h.index && c.at.x > 20 && c.at.y > 20 && c.at.x < cam.width - 20 && c.at.y < cam.height - 20)
  for (const c of good) votes.set(c.h.index, (votes.get(c.h.index) ?? 0) + 1)
  const best = [...votes.entries()].sort((a, b) => b[1] - a[1])[0]
  if (!best) throw new Error('no edge is picked in the view')
  const mine = good.filter((c) => c.h.index === best[0])
  const mid = mine[Math.floor(mine.length / 2)]!
  return { x: mid.at.x, y: mid.at.y, edge: best[0], name: mid.h.name ?? '' }
}

/** A spot where this kind of pointer picks a vertex, at that vertex's own position on screen, well inside the view. */
export async function vertexSpot(page: Page, _request: APIRequestContext, pointer: PickPointer): Promise<Spot & { vertex: number; name: string }> {
  const cam = (await settled(page)).camera
  const near = await pickAll(page, grid(cam, 14), pointer, ['vertex'])
  const own = near.flatMap((h) => (h?.kind === 'vertex' ? [{ h, at: project(cam, h.point as P3) }] : []))
  const confirmed = await pickAll(page, own.map((c) => c.at), pointer)
  const i = own.findIndex((c, k) => confirmed[k]?.kind === 'vertex' && confirmed[k]?.index === c.h.index && c.at.x > 30 && c.at.y > 30 && c.at.x < cam.width - 30 && c.at.y < cam.height - 30)
  if (i < 0) throw new Error('no vertex is picked in the view')
  return { x: own[i]!.at.x, y: own[i]!.at.y, vertex: own[i]!.h.index, name: own[i]!.h.name ?? '' }
}

/** The first spot where this kind of pointer picks a plane (scanning the view left to right, top to bottom). */
export async function planeSpot(page: Page, pointer: PickPointer): Promise<Spot & { key: string }> {
  const cam = (await settled(page)).camera
  const grid: Spot[] = []
  for (let y = 8; y < cam.height; y += 12) for (let x = 8; x < cam.width; x += 12) grid.push({ x, y })
  const hits = await pickAll(page, grid, pointer)
  const i = hits.findIndex((h) => h?.kind === 'plane')
  if (i < 0) throw new Error('no plane is picked anywhere in the view')
  return { ...grid[i]!, key: (hits[i] as { key: string }).key }
}
