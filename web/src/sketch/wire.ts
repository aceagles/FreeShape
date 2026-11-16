/** A point in the sketch plane: [u, v] in mm along the plane's x and y directions. */
export type Uv = readonly [number, number]
export type Vec3 = readonly [number, number, number]

/** Solve status of a point or entity, as the server's `status_of` gives it. */
export type Status = 'free' | 'defined' | 'conflicting'

/** An entity of the freeshape.sketch format. `construction` entities are drawn dashed and make no region. */
export interface SketchEntity {
  type: string
  construction?: boolean
  /** line: ends; arc: start and end (counter-clockwise from p1 to p2 about c) */
  p1?: string
  p2?: string
  /** circle, arc, ellipse: centre point */
  c?: string
  r?: number
  /** point, text: its point */
  p?: string
  /** ellipse: end of the major axis */
  a?: string
  /** spline: the points it passes through */
  pts?: string[]
  closed?: boolean
  helper?: boolean
}

export interface SketchConstraint {
  id: string
  type: string
  refs: string[]
  value?: number
  /** A dimension: where its label sits (sketch coordinates), and where the measured geometry was when it was put there. */
  pos?: Uv
  pos_anchor?: Uv
}

export interface SketchData {
  points: Record<string, Uv>
  entities: Record<string, SketchEntity>
  constraints: SketchConstraint[]
}

export interface SketchStatus {
  ok: boolean
  dof: number
  entities: Record<string, Status>
  points: Record<string, Status>
  constraints: Record<string, 'ok' | 'conflicting'>
}

/** The sketch plane's frame in world coordinates (the engine's sketch_frame). */
export interface Frame {
  origin: Vec3
  xdir: Vec3
  ydir: Vec3
  normal: Vec3
}

export interface FrameEdge {
  pick: unknown
  points: Uv[]
  height: number
}

/** What `sketch.opened` and `sketch.state` carry, and what `sketch.changed` carries without the frame. */
export interface SketchView {
  document: string
  tab: string
  feature: string | null
  name: string
  plane: unknown
  sketch: SketchData
  status: SketchStatus
  canUndo: boolean
  canRedo: boolean
  frame: Frame
  edges: FrameEdge[]
  revision: number | null
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const isUv = (v: unknown): v is Uv => Array.isArray(v) && v.length === 2 && isNum(v[0]) && isNum(v[1])
const isVec3 = (v: unknown): v is Vec3 => Array.isArray(v) && v.length === 3 && v.every(isNum)

export function parseSketchData(raw: unknown): SketchData | null {
  if (!isObj(raw) || !isObj(raw['points']) || !isObj(raw['entities']) || !Array.isArray(raw['constraints'])) return null
  const points: Record<string, Uv> = {}
  for (const [id, p] of Object.entries(raw['points'])) if (isUv(p)) points[id] = p
  const entities: Record<string, SketchEntity> = {}
  for (const [id, e] of Object.entries(raw['entities'])) if (isObj(e) && typeof e['type'] === 'string') entities[id] = e as unknown as SketchEntity
  const constraints: SketchConstraint[] = []
  for (const c of raw['constraints'])
    if (isObj(c) && typeof c['id'] === 'string' && typeof c['type'] === 'string' && Array.isArray(c['refs']))
      constraints.push({ id: c['id'], type: c['type'], refs: c['refs'].filter((r): r is string => typeof r === 'string'), ...(isNum(c['value']) ? { value: c['value'] } : {}),
        ...(isUv(c['pos']) ? { pos: c['pos'] } : {}),
        ...(isUv(c['pos_anchor']) ? { pos_anchor: c['pos_anchor'] } : {}),
      })
  return { points, entities, constraints }
}

export function parseSketchStatus(raw: unknown): SketchStatus | null {
  if (!isObj(raw) || !isObj(raw['entities']) || !isObj(raw['points']) || !isObj(raw['constraints'])) return null
  return {
    ok: raw['ok'] !== false,
    dof: isNum(raw['dof']) ? raw['dof'] : 0,
    entities: raw['entities'] as Record<string, Status>,
    points: raw['points'] as Record<string, Status>,
    constraints: raw['constraints'] as Record<string, 'ok' | 'conflicting'>,
  }
}

export function parseFrame(raw: unknown): Frame | null {
  if (!isObj(raw) || !isVec3(raw['origin']) || !isVec3(raw['xdir']) || !isVec3(raw['ydir']) || !isVec3(raw['normal'])) return null
  return { origin: raw['origin'], xdir: raw['xdir'], ydir: raw['ydir'], normal: raw['normal'] }
}

/**
 * The fields of a `sketch.opened` or `sketch.state` message as a SketchView, or null when one is missing or of the wrong
 * shape. A `sketch.changed` has no frame: pass the one the open gave as `frame`, and the edges as `edges`.
 */
export function parseSketchView(m: unknown, keep?: { frame: Frame; edges: FrameEdge[] }): SketchView | null {
  if (!isObj(m) || typeof m['document'] !== 'string' || typeof m['tab'] !== 'string') return null
  const sketch = parseSketchData(m['sketch'])
  const status = parseSketchStatus(m['status'])
  const frame = m['frame'] === undefined ? (keep?.frame ?? null) : parseFrame(m['frame'])
  if (!sketch || !status || !frame) return null
  const edges: FrameEdge[] = Array.isArray(m['edges'])
    ? m['edges'].filter(isObj).map((e) => ({ pick: e['pick'], points: Array.isArray(e['points']) ? e['points'].filter(isUv) : [], height: isNum(e['height']) ? e['height'] : 0 }))
    : (keep?.edges ?? [])
  return {
    document: m['document'],
    tab: m['tab'],
    feature: typeof m['feature'] === 'string' ? m['feature'] : null,
    name: typeof m['name'] === 'string' ? m['name'] : 'Sketch',
    plane: m['plane'] ?? null,
    sketch,
    status,
    canUndo: m['can_undo'] === true,
    canRedo: m['can_redo'] === true,
    frame,
    edges,
    revision: isNum(m['revision']) ? m['revision'] : null,
  }
}
