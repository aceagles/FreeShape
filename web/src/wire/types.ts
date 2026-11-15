/**
 * Typed Part Studio outputs, as decoded from GET /tabs/<id>/outputs.
 *
 * INDEX CONVENTION. Every index in these types is 0-based. The engine mixes two bases: Element names are keyed
 * by 1-based indices (face 1 is the first face) and `tri_face` holds 1-based face numbers, while `face_ranges`
 * and `edge_polys` are positional (entry 0 is face / edge 1). The decoder converts at the boundary: here face,
 * edge and vertex number i is engine index i + 1, and the `names` maps are keyed by the 0-based index. When a
 * message goes back to the server, add 1 to a face / edge / vertex index again.
 *
 * ZERO COPY. Arrays marked "view" are typed arrays over the response ArrayBuffer: keep that buffer alive and do
 * not write to them. `triangleFace` is the one derived array (the engine's 1-based `tri_face` minus 1).
 */

export type Vec3 = readonly [number, number, number]

/** The three kinds of topological element an Element name can name. */
export type ElementKind = 'face' | 'edge' | 'vertex'

/** Element names of one part: 0-based element index -> Element name. Sparse: an unnamed element has no entry. */
export interface ElementNames {
  readonly face: ReadonlyMap<number, string>
  readonly edge: ReadonlyMap<number, string>
  readonly vertex: ReadonlyMap<number, string>
}

/** A display mesh, in the studio's coordinates, millimetres. */
export interface PartMesh {
  /** View. 3 floats per vertex: x y z. Length 3 * vertexCount. */
  readonly positions: Float32Array
  /** View. Per-vertex normals, same order and length as `positions`. */
  readonly normals: Float32Array
  /** View. 3 per triangle; each value is a 0-based VERTEX index (corner k is positions[3k .. 3k+2]). */
  readonly indices: Uint32Array
  /** Derived, one entry per triangle: the 0-based face index of that triangle; -1 if the triangle has no face. */
  readonly triangleFace: Int32Array
  /**
   * Entry f is face f (0-based): its triangles are the triangle numbers start .. end - 1 (end exclusive), so its
   * corner indices are indices[3 * start .. 3 * end). Positional, in triangle numbers, not vertex indices.
   */
  readonly faceRanges: ReadonlyArray<readonly [start: number, end: number]>
  /** Entry e is edge e (0-based): a polyline, views of 3 floats per point, in order along the edge. */
  readonly edges: readonly Float32Array[]
  /** View. The shape's topological vertices, 3 floats each; vertex v (0-based) is vertices[3v .. 3v+2]. */
  readonly vertices: Float32Array
}

export interface Part {
  readonly id: string
  readonly name: string
  /** Display colour, r g b in 0..1 (an engine colour with an alpha keeps it as a fourth entry). */
  readonly color: readonly number[]
  readonly hidden: boolean
  /** View. zlib-compressed BREP text, no triangulation. */
  readonly brep: Uint8Array
  /** sha256 hex of the uncompressed BREP text. */
  readonly hash: string
  readonly names: ElementNames
  /** Null when the part has no mesh. */
  readonly mesh: PartMesh | null
  /** Per-face colour override: 0-based face index -> r g b. Null when no face has one. */
  readonly faceColors: ReadonlyMap<number, Vec3> | null
}

export interface SketchRegion {
  /** Centroid, 3 numbers. */
  readonly c: Vec3
  readonly area: number
  readonly loops: number
  readonly edges: number
}

export interface Sketch {
  readonly id: string
  readonly name: string
  /** The sketch plane in studio coordinates: a point, the x direction and the normal. */
  readonly plane: { readonly origin: Vec3; readonly xdir: Vec3; readonly normal: Vec3 }
  /** The solved sketch (Sketch.to_dict): points, entities, constraints. Opaque to the wire layer. */
  readonly sketch: Readonly<Record<string, unknown>>
  readonly regions: readonly SketchRegion[]
  /** Element names of the sketch's region compound; 0-based indices, as for a part. */
  readonly names: ElementNames
  /** Entry i is the Element name of regions[i], or null. */
  readonly regionNames: ReadonlyArray<string | null>
  readonly hash: string
}

export interface MateConnector {
  readonly id: string
  readonly name: string
  /** Id of the owning part; "" for a sketch-owned connector. */
  readonly part: string
  /** Origin, z axis and x axis in the studio's coordinates. */
  readonly frame: { readonly o: Vec3; readonly z: Vec3; readonly x: Vec3 }
  readonly hash: string
  /** Id of the owning sketch feature; "" for a part-owned connector. */
  readonly sketch: string
}

/** A construction plane: origin, x direction, normal. Keyed by its feature id in `planes`. */
export interface PlaneOut {
  readonly o: Vec3
  readonly x: Vec3
  readonly n: Vec3
}

/**
 * A construction axis: origin, unit direction, and the stretch [t0, t1] of the source geometry along it, in mm
 * from the origin (null when the axis has no extent). Keyed by feature id.
 */
export interface AxisOut {
  readonly o: Vec3
  readonly d: Vec3
  readonly span: readonly [number, number] | null
}

export interface StudioOutputs {
  readonly kind: 'studio'
  readonly tab: string
  readonly name: string
  /** Bumps on every edit applied to the tab. */
  readonly revision: number
  /** Feature id (or "" for the tab) -> message. */
  readonly errors: Readonly<Record<string, string>>
  /** Tab ids this tab read. */
  readonly consumes: readonly string[]
  /** The studio's parts, in the studio's order. */
  readonly parts: readonly Part[]
  /** Reference parts for display only; never the studio's own parts. */
  readonly context: readonly Part[]
  readonly sketches: readonly Sketch[]
  readonly connectors: readonly MateConnector[]
  readonly variables: Readonly<Record<string, number>>
  readonly planes: Readonly<Record<string, PlaneOut>>
  readonly axes: Readonly<Record<string, AxisOut>>
  /** Failed feature id -> references to where it failed (as the engine sent them). */
  readonly errorRefs: Readonly<Record<string, readonly unknown[]>>
}

/** Any other tab kind: the common fields only. */
export interface OtherOutputs {
  readonly kind: string
  readonly tab: string
  readonly name: string
  readonly revision: number
  readonly errors: Readonly<Record<string, string>>
  readonly consumes: readonly string[]
}

export type TabOutputs = StudioOutputs | OtherOutputs

export function isStudio(o: TabOutputs): o is StudioOutputs {
  return o.kind === 'studio'
}
