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

/**
 * One row of the feature list, in the order the features build (file order). Ids are stable across edits and
 * revisions: use them as React keys and in edits (`feature`, `ids`).
 */
export interface FeatureEntry {
  readonly id: string
  /** The feature type, for the icon: sketch, extrude, revolve, fillet, chamfer, shell, plane, axis, mirror,
   *  linear_pattern, circular_pattern, boolean, import, hole, variable, variable_studio_ref, derived,
   *  mate_connector, context, sweep, rib, loft, draft, split, move, thread, sm_base, sm_flange, sm_hem, sm_jog,
   *  sm_tab, sm_convert, delete_face, move_face, offset_surface, thicken, enclose, fill, delete_part, or
   *  `plugin:<id>` for a plugin's feature. An unknown type is shown with a generic icon. */
  readonly type: string
  /** The type's display label ("Extrude"); a Boundary surface (a loft) says "Boundary surface". */
  readonly label: string
  /** The row's text; what a rename changes. */
  readonly name: string
  /** The feature's own suppressed flag (a feature in a suppressed folder is not marked). */
  readonly suppressed: boolean
  /** The message the feature failed with, or null when it built. The same text as `errors[id]`. */
  readonly error: string | null
  /** Id of the folder the feature sits in (a `FolderEntry.id`), or null. */
  readonly folder: string | null
  /** The eye: a sketch, plane or axis hidden in the viewport. */
  readonly hidden: boolean
}

/** A feature-list folder. Draw it above its first member; its members are contiguous in `features`. */
export interface FolderEntry {
  readonly id: string
  readonly name: string
  /** Expanded in the list. */
  readonly open: boolean
  /** Every member is suppressed. */
  readonly suppressed: boolean
  /** How many members carry an error. */
  readonly failed: number
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
  /**
   * The feature tree. False when the payload predates it (an older server or recorded fixture): `features` and
   * `folders` are then empty, `rollback` is 0 and both `can` flags are false, so a panel should show "no tree
   * available" rather than an empty Part Studio. A payload with extra unknown fields still decodes.
   */
  readonly hasTree: boolean
  /** The features in file order (the order they build in), rolled-back ones included. */
  readonly features: readonly FeatureEntry[]
  /** Folders in the order of their first member. */
  readonly folders: readonly FolderEntry[]
  /**
   * The rollback position: features[0 .. rollback) are active, the rest are rolled back. Equal to
   * features.length when nothing is rolled back. Send this number as `set_rollback`'s `index` (0-based).
   */
  readonly rollback: number
  readonly canUndo: boolean
  readonly canRedo: boolean
  /** The drag arrows of the dialog being previewed. Only a preview has any; committed outputs give []. */
  readonly manipulators: readonly Manipulator[]
}

/**
 * A drag arrow (dialog_schema.manipulators). The value is signed: along `dir` from `origin`. A drag to value v sets
 * the `field` param to |v| (twice that when `symmetric`) and the `flip` param to v < 0.
 */
export interface Manipulator {
  /** The param the arrow drives: "depth" (Extrude) or "offset" (Plane). */
  readonly field: string
  /** The param that takes the sign of a drag past the origin, or null. */
  readonly flip: string | null
  readonly origin: Vec3
  /** Unit vector. */
  readonly dir: Vec3
  /** The current signed distance along `dir`; half of the field when `symmetric`. */
  readonly value: number
  readonly symmetric: boolean
  /** "view": snap to a step the viewer picks from its zoom. */
  readonly snap: string
  /** The smallest absolute value a drag may give, or null. */
  readonly min: number | null
  /** The value as the desktop prints it, with its unit. */
  readonly label: string
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

/** One instance of an Assembly: a part under a placement (card I8.1). */
export interface InstanceEntry {
  readonly id: string
  readonly name: string
  /** Key into `AssemblyOutputs.parts` (the part's content hash); null when the instance has no part. */
  readonly part: string | null
  /** Placement, row-major 4 x 4 as the engine sends it (the last row is 0 0 0 1). */
  readonly T: readonly (readonly number[])[]
  readonly hidden: boolean
  readonly fixed: boolean
  /** r g b in 0..1. */
  readonly color: readonly number[]
}

/** One mate of an Assembly, as the read-only list shows it. */
export interface MateEntry {
  readonly id: string
  readonly name: string
  readonly type: string
  readonly suppressed: boolean
  /** Ids of the two instances the mate joins (the connectors' `instance`). */
  readonly a: string
  readonly b: string
}

/** An Assembly's outputs. Indices in `parts` (Element names, faces, edges, vertices) are 0-based like a Part Studio's. */
export interface AssemblyOutputs {
  readonly kind: 'assembly'
  readonly tab: string
  readonly name: string
  readonly revision: number
  /** Instance or mate id (or "" for the tab) -> message. */
  readonly errors: Readonly<Record<string, string>>
  readonly consumes: readonly string[]
  /** Content hash -> part, shared by every instance of one part. */
  readonly parts: Readonly<Record<string, Part>>
  readonly instances: readonly InstanceEntry[]
  readonly mates: readonly MateEntry[]
  /** Degrees of freedom left after solving. */
  readonly dof: number
}

export type TabOutputs = StudioOutputs | AssemblyOutputs | OtherOutputs

export function isStudio(o: TabOutputs): o is StudioOutputs {
  return o.kind === 'studio'
}

export function isAssembly(o: TabOutputs): o is AssemblyOutputs {
  return o.kind === 'assembly' && 'instances' in o
}
