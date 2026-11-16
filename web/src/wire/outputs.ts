import { decodeWire, intKeyMap, isDataclass, isNdArray } from './decode'
import type { DataclassNode, NdArray, WireInput } from './decode'
import { WireError } from './errors'
import type {
  AssemblyOutputs,
  AxisOut,
  ElementKind,
  ElementNames,
  FeatureEntry,
  FolderEntry,
  InstanceEntry,
  MateConnector,
  MateEntry,
  Part,
  PartMesh,
  PlaneOut,
  Sketch,
  SketchRegion,
  StudioOutputs,
  TabOutputs,
  Vec3,
} from './types'

type Fields = Readonly<Record<string, unknown>>

function fail(what: string): never {
  throw new WireError('bad-structure', what)
}

function dc(x: unknown, cls: string, where: string): Fields {
  if (!isDataclass(x) || x.cls !== cls) fail(`${where}: expected ${cls}`)
  return (x as DataclassNode).fields
}

function str(x: unknown, where: string): string {
  return typeof x === 'string' ? x : fail(`${where}: expected a string`)
}

function num(x: unknown, where: string): number {
  return typeof x === 'number' ? x : fail(`${where}: expected a number`)
}

function bool(x: unknown, where: string): boolean {
  return typeof x === 'boolean' ? x : fail(`${where}: expected a boolean`)
}

function list(x: unknown, where: string): unknown[] {
  return Array.isArray(x) ? x : fail(`${where}: expected a list`)
}

function record(x: unknown, where: string): Record<string, unknown> {
  return typeof x === 'object' && x !== null && !Array.isArray(x) && !(x instanceof Map)
    ? (x as Record<string, unknown>)
    : fail(`${where}: expected an object`)
}

function vec3(x: unknown, where: string): Vec3 {
  const a = list(x, where)
  if (a.length !== 3) fail(`${where}: expected 3 numbers`)
  return [num(a[0], where), num(a[1], where), num(a[2], where)]
}

function span(x: unknown, where: string): readonly [number, number] | null {
  if (x === null || x === undefined) return null
  const a = list(x, where)
  if (a.length !== 2) fail(`${where}: expected 2 numbers or null`)
  return [num(a[0], where), num(a[1], where)]
}

function numbers(x: unknown, where: string): number[] {
  return list(x, where).map((v) => num(v, where))
}

function strings(x: unknown, where: string): string[] {
  return list(x, where).map((v) => str(v, where))
}

function array(x: unknown, where: string, dtype: string, cols: number): NdArray {
  if (!isNdArray(x) || x.dtype !== dtype) fail(`${where}: expected a ${dtype} array`)
  const a = x as NdArray
  const ok = cols === 0 ? a.shape.length === 1 : a.shape.length === 2 && a.shape[1] === cols
  return ok ? a : fail(`${where}: expected shape ${cols === 0 ? '[n]' : `[n, ${cols}]`}, got [${a.shape.join(', ')}]`)
}

/** Engine 1-based element names -> 0-based. */
function names(x: unknown, where: string): ElementNames {
  const r = record(x, where)
  const kind = (k: ElementKind): Map<number, string> => {
    const out = new Map<number, string>()
    for (const [i, name] of intKeyMap(r[k] ?? {})) {
      if (i < 1) fail(`${where}.${k}: engine index ${i} is not 1-based`)
      out.set(i - 1, str(name, `${where}.${k}`))
    }
    return out
  }
  return { face: kind('face'), edge: kind('edge'), vertex: kind('vertex') }
}

function mesh(x: unknown, where: string): PartMesh {
  const f = dc(x, 'MeshOut', where)
  const positions = array(f['verts'], `${where}.verts`, '<f4', 3).data as Float32Array
  const normals = array(f['normals'], `${where}.normals`, '<f4', 3).data as Float32Array
  const indices = array(f['tris'], `${where}.tris`, '<u4', 3).data as Uint32Array
  const triFace = array(f['tri_face'], `${where}.tri_face`, '<i4', 0).data as Int32Array
  const vertices = array(f['vertices'], `${where}.vertices`, '<f4', 3).data as Float32Array
  const triangles = indices.length / 3
  if (triFace.length !== triangles) fail(`${where}: ${triFace.length} face ids for ${triangles} triangles`)
  if (normals.length !== positions.length) fail(`${where}: normals and positions differ in length`)
  const triangleFace = new Int32Array(triFace.length)
  for (let t = 0; t < triFace.length; t++) triangleFace[t] = (triFace[t] as number) - 1
  const faceRanges = list(f['face_ranges'], `${where}.face_ranges`).map((r, i) => {
    const p = numbers(r, `${where}.face_ranges[${i}]`)
    if (p.length !== 2 || (p[0] as number) > (p[1] as number) || (p[1] as number) > triangles) {
      fail(`${where}.face_ranges[${i}]: not a [start, end] inside the ${triangles} triangles`)
    }
    return [p[0] as number, p[1] as number] as const
  })
  const edges = list(f['edge_polys'], `${where}.edge_polys`).map(
    (e, i) => array(e, `${where}.edge_polys[${i}]`, '<f4', 3).data as Float32Array,
  )
  return { positions, normals, indices, triangleFace, faceRanges, edges, vertices }
}

function part(x: unknown, where: string): Part {
  const f = dc(x, 'PartOut', where)
  const brep = f['brep']
  if (!(brep instanceof Uint8Array)) fail(`${where}.brep: expected bytes`)
  let faceColors: Map<number, Vec3> | null = null
  if (f['face_colors'] !== null && f['face_colors'] !== undefined) {
    faceColors = new Map()
    for (const [i, c] of intKeyMap(f['face_colors'])) faceColors.set(i - 1, vec3(c, `${where}.face_colors`))
  }
  return {
    id: str(f['id'], `${where}.id`),
    name: str(f['name'], `${where}.name`),
    color: numbers(f['color'], `${where}.color`),
    hidden: bool(f['hidden'], `${where}.hidden`),
    brep: brep as Uint8Array,
    hash: str(f['hash'], `${where}.hash`),
    names: names(f['names'], `${where}.names`),
    mesh: f['mesh'] === null ? null : mesh(f['mesh'], `${where}.mesh`),
    faceColors,
  }
}

function sketch(x: unknown, where: string): Sketch {
  const f = dc(x, 'SketchOut', where)
  const plane = record(f['plane'], `${where}.plane`)
  const regions = list(f['regions'], `${where}.regions`).map((r, i): SketchRegion => {
    const g = record(r, `${where}.regions[${i}]`)
    return {
      c: vec3(g['c'], `${where}.regions[${i}].c`),
      area: num(g['area'], `${where}.regions[${i}].area`),
      loops: num(g['loops'], `${where}.regions[${i}].loops`),
      edges: num(g['edges'], `${where}.regions[${i}].edges`),
    }
  })
  return {
    id: str(f['id'], `${where}.id`),
    name: str(f['name'], `${where}.name`),
    plane: {
      origin: vec3(plane['origin'], `${where}.plane.origin`),
      xdir: vec3(plane['xdir'], `${where}.plane.xdir`),
      normal: vec3(plane['normal'], `${where}.plane.normal`),
    },
    sketch: record(f['sketch'], `${where}.sketch`),
    regions,
    names: names(f['names'], `${where}.names`),
    regionNames: list(f['region_names'], `${where}.region_names`).map((n) =>
      n === null ? null : str(n, `${where}.region_names`),
    ),
    hash: str(f['hash'], `${where}.hash`),
  }
}

function connector(x: unknown, where: string): MateConnector {
  const f = dc(x, 'MateConnectorOut', where)
  const fr = record(f['frame'], `${where}.frame`)
  return {
    id: str(f['id'], `${where}.id`),
    name: str(f['name'], `${where}.name`),
    part: str(f['part'], `${where}.part`),
    frame: { o: vec3(fr['o'], `${where}.frame.o`), z: vec3(fr['z'], `${where}.frame.z`), x: vec3(fr['x'], `${where}.frame.x`) },
    hash: str(f['hash'], `${where}.hash`),
    sketch: str(f['sketch'], `${where}.sketch`),
  }
}

function feature(x: unknown, where: string): FeatureEntry {
  const f = dc(x, 'FeatureOut', where)
  const error = f['error']
  const folder = f['folder']
  return {
    id: str(f['id'], `${where}.id`),
    type: str(f['type'], `${where}.type`),
    label: str(f['label'], `${where}.label`),
    name: str(f['name'], `${where}.name`),
    suppressed: bool(f['suppressed'], `${where}.suppressed`),
    error: error === null || error === undefined ? null : str(error, `${where}.error`),
    folder: folder === null || folder === undefined ? null : str(folder, `${where}.folder`),
    hidden: f['hidden'] === undefined ? false : bool(f['hidden'], `${where}.hidden`),
  }
}

function folder(x: unknown, where: string): FolderEntry {
  const f = dc(x, 'FolderOut', where)
  return {
    id: str(f['id'], `${where}.id`),
    name: str(f['name'], `${where}.name`),
    open: bool(f['open'], `${where}.open`),
    suppressed: bool(f['suppressed'], `${where}.suppressed`),
    failed: f['failed'] === undefined ? 0 : num(f['failed'], `${where}.failed`),
  }
}

function mapOf<T>(x: unknown, where: string, each: (v: Record<string, unknown>, w: string) => T): Record<string, T> {
  const out: Record<string, T> = {}
  for (const [k, v] of Object.entries(record(x, where))) out[k] = each(record(v, `${where}.${k}`), `${where}.${k}`)
  return out
}

function studio(f: Fields, common: Common): StudioOutputs {
  const w = 'StudioOutputs'
  return {
    kind: 'studio',
    ...common,
    parts: list(f['parts'], `${w}.parts`).map((p, i) => part(p, `${w}.parts[${i}]`)),
    context: list(f['context'], `${w}.context`).map((p, i) => part(p, `${w}.context[${i}]`)),
    sketches: list(f['sketches'], `${w}.sketches`).map((s, i) => sketch(s, `${w}.sketches[${i}]`)),
    connectors: list(f['connectors'], `${w}.connectors`).map((c, i) => connector(c, `${w}.connectors[${i}]`)),
    variables: record(f['variables'], `${w}.variables`) as Record<string, number>,
    planes: mapOf<PlaneOut>(f['planes'], `${w}.planes`, (v, p) => ({ o: vec3(v['o'], p), x: vec3(v['x'], p), n: vec3(v['n'], p) })),
    axes: mapOf<AxisOut>(f['axes'], `${w}.axes`, (v, p) => ({ o: vec3(v['o'], p), d: vec3(v['d'], p), span: span(v['span'], p) })),
    errorRefs: recordOfLists(f['error_refs']),
    // an older payload has none of the tree fields: decode them as an empty tree and say so in `hasTree`
    hasTree: f['features'] !== undefined,
    features: f['features'] === undefined ? [] : list(f['features'], `${w}.features`).map((e, i) => feature(e, `${w}.features[${i}]`)),
    folders: f['folders'] === undefined ? [] : list(f['folders'], `${w}.folders`).map((e, i) => folder(e, `${w}.folders[${i}]`)),
    rollback: f['rollback'] === undefined ? 0 : num(f['rollback'], `${w}.rollback`),
    canUndo: f['can_undo'] === undefined ? false : bool(f['can_undo'], `${w}.can_undo`),
    canRedo: f['can_redo'] === undefined ? false : bool(f['can_redo'], `${w}.can_redo`),
  }
}

function instance(x: unknown, where: string): InstanceEntry {
  const f = dc(x, 'InstanceOut', where)
  const part = f['part']
  const rows = list(f['T'], `${where}.T`).map((r, i) => numbers(r, `${where}.T[${i}]`))
  if (rows.length !== 4 || rows.some((r) => r.length !== 4)) fail(`${where}.T: expected a 4 x 4 matrix`)
  return {
    id: str(f['id'], `${where}.id`),
    name: str(f['name'], `${where}.name`),
    part: part === null || part === undefined ? null : str(part, `${where}.part`),
    T: rows,
    hidden: bool(f['hidden'], `${where}.hidden`),
    fixed: bool(f['fixed'], `${where}.fixed`),
    color: numbers(f['color'], `${where}.color`),
  }
}

function mate(x: unknown, where: string): MateEntry {
  const f = dc(x, 'MateOut', where)
  return {
    id: str(f['id'], `${where}.id`),
    name: str(f['name'], `${where}.name`),
    type: str(f['type'], `${where}.type`),
    suppressed: bool(f['suppressed'], `${where}.suppressed`),
    a: str(dc(f['a'], 'ConnectorOut', `${where}.a`)['instance'], `${where}.a.instance`),
    b: str(dc(f['b'], 'ConnectorOut', `${where}.b`)['instance'], `${where}.b.instance`),
  }
}

function assembly(f: Fields, common: Common): AssemblyOutputs {
  const w = 'AssemblyOutputs'
  const parts: Record<string, Part> = {}
  for (const [k, v] of Object.entries(record(f['parts'], `${w}.parts`))) parts[k] = part(v, `${w}.parts.${k}`)
  return {
    kind: 'assembly',
    ...common,
    parts,
    instances: list(f['instances'], `${w}.instances`).map((e, i) => instance(e, `${w}.instances[${i}]`)),
    mates: list(f['mates'], `${w}.mates`).map((e, i) => mate(e, `${w}.mates[${i}]`)),
    dof: f['dof'] === undefined ? 0 : num(f['dof'], `${w}.dof`),
  }
}

type Common = Pick<StudioOutputs, 'tab' | 'name' | 'revision' | 'errors' | 'consumes'>

function recordOfLists(x: unknown): Record<string, readonly unknown[]> {
  const out: Record<string, readonly unknown[]> = {}
  for (const [k, v] of Object.entries(record(x, 'error_refs'))) out[k] = list(v, `error_refs.${k}`)
  return out
}

/** Decode a GET /tabs/<id>/outputs payload. A Part Studio is fully typed; other tab kinds give the common fields. */
export function decodeOutputs(input: WireInput): TabOutputs {
  const root = decodeWire(input)
  if (!isDataclass(root)) fail('root: expected a TabOutputs dataclass')
  const { cls, fields: f } = root as DataclassNode
  if (!cls.endsWith('Outputs')) fail(`root: ${cls} is not an outputs class`)
  const errors: Record<string, string> = {}
  for (const [k, v] of Object.entries(record(f['errors'], 'errors'))) errors[k] = str(v, `errors.${k}`)
  const common: Common = {
    tab: str(f['tab'], 'tab'),
    name: str(f['name'], 'name'),
    revision: num(f['revision'], 'revision'),
    errors,
    consumes: strings(f['consumes'], 'consumes'),
  }
  if (cls === 'StudioOutputs') return studio(f, common)
  if (cls === 'AssemblyOutputs') return assembly(f, common)
  return { kind: str(f['kind'], 'kind'), ...common }
}

/** The Element name of face / edge / vertex `index` (0-based) of `part`, or undefined if it has none. */
export function elementName(part: Part, kind: ElementKind, index: number): string | undefined {
  return part.names[kind].get(index)
}

/** The 0-based face a triangle (the three.js raycast `faceIndex`) belongs to, or -1. */
export function faceOfTriangle(mesh: PartMesh, triangle: number): number {
  return mesh.triangleFace[triangle] ?? -1
}
