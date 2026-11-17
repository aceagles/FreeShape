import { BufferAttribute, BufferGeometry, DoubleSide, Group, Mesh, MeshBasicMaterial, SRGBColorSpace, Texture, Vector3 } from 'three'
import type { Material } from 'three'
import type { Canvas } from '../wire'

/**
 * Canvases on screen (card I12.5): each `CanvasOut` of the studio as a textured rectangle in its frame. The picture is
 * mapped along `xdir` and `ydir` exactly as the engine sends them (Rotation and both flips are already in those two
 * directions: a flip reverses one of them, so the picture is mirrored in the plane), so nothing here applies a flip a
 * second time. The texture coordinates are the plain 0..1 square.
 *
 * Drawing: after the parts, without writing depth. The material is transparent (three draws the transparent list after the
 * opaque one) and tests depth, so a part in front hides it; it writes none, so a plane or a sketch region on the same plane
 * draws over it (the sketch itself is an SVG layer over the whole view). `renderOrder` 2.4 puts it after the Context bodies
 * (2) and before the planes and the sketch regions (3). A slight polygon offset pushes it behind a part face it lies on.
 * The stored opacity (0 to 100) is the material's opacity as 0..1.
 *
 * Textures are cached per blob in a `TextureCache` that outlives the scene (the scene is built again on every revision):
 * the image is fetched once and decoded once, and every scene that draws the blob shares the texture. A canvas whose texture
 * has not arrived is not drawn yet (it can still be picked); the cache tells its owner when one arrives and `refresh` puts it on.
 * Nothing here listens to the pointer: the pick index calls `pickCanvas`.
 */

/** The quad's corners in the picture's own x, y (units of half the width and height), counter-clockwise from the bottom left, and their texture coordinates. */
const CORNERS: readonly (readonly [number, number])[] = [
  [-1, -1],
  [1, -1],
  [1, 1],
  [-1, 1],
]
const UV: readonly (readonly [number, number])[] = [
  [0, 0],
  [1, 0],
  [1, 1],
  [0, 1],
]

export const CANVAS_RENDER_ORDER = 2.4

/** What a canvas is as the scene and the pick index know it. */
export interface CanvasSpec {
  readonly id: string
  readonly name: string
  readonly origin: Vector3
  /** Unit directions of the picture's left-to-right and bottom-to-top, flips applied. */
  readonly x: Vector3
  readonly y: Vector3
  readonly normal: Vector3
  readonly width: number
  readonly height: number
  /** Whether a click picks it. */
  readonly selectable: boolean
  /** The four corners, in the order the quad is built. */
  readonly corners: readonly Vector3[]
}

export function canvasSpec(c: Canvas): CanvasSpec {
  const origin = new Vector3(...c.origin)
  const x = new Vector3(...c.xdir).normalize()
  const y = new Vector3(...c.ydir).normalize()
  const corners = CORNERS.map(([sx, sy]) =>
    origin
      .clone()
      .addScaledVector(x, (sx * c.width) / 2)
      .addScaledVector(y, (sy * c.height) / 2),
  )
  return { id: c.id, name: c.name, origin, x, y, normal: new Vector3(...c.normal).normalize(), width: c.width, height: c.height, selectable: c.selectable, corners }
}

/** The canvases the scene draws: the studio's own that the eye shows, a dialog's preview ones replacing any of the same id. */
export function canvasesToDraw(base: readonly Canvas[], preview: readonly Canvas[] | null): Canvas[] {
  const over = new Set((preview ?? []).map((c) => c.id))
  return [...base.filter((c) => !over.has(c.id)), ...(preview ?? [])].filter((c) => !c.hidden)
}

export type ImageFetcher = (blob: string) => Promise<Blob>
export type ImageDecoder = (image: Blob) => Promise<Texture>

/** The picture as a texture, decoded by the browser's image element. sRGB, so the scene (which writes colours unconverted and lets the renderer encode its output) shows the file's own colours. */
export async function decodeImage(image: Blob): Promise<Texture> {
  const url = URL.createObjectURL(image)
  try {
    const el = new Image()
    el.src = url
    await el.decode()
    const t = new Texture(el)
    t.colorSpace = SRGBColorSpace
    t.needsUpdate = true
    return t
  } finally {
    URL.revokeObjectURL(url)
  }
}

interface Entry {
  texture: Texture | null
  error: string | null
}

/**
 * Textures by blob key: an image is fetched and decoded once however many scenes ask for it. `get` returns the texture, or
 * null while it loads (and starts the load); `changed` is called when one arrives or fails. `dispose` frees them all.
 */
export class TextureCache {
  private readonly entries = new Map<string, Entry>()
  private disposed = false

  constructor(
    private readonly fetchImage: ImageFetcher,
    private readonly changed: () => void = () => {},
    private readonly decode: ImageDecoder = decodeImage,
  ) {}

  get(blob: string): Texture | null {
    if (blob === '' || this.disposed) return null
    let e = this.entries.get(blob)
    if (e === undefined) {
      const entry: Entry = { texture: null, error: null }
      e = entry
      this.entries.set(blob, entry)
      this.fetchImage(blob)
        .then(this.decode)
        .then(
          (t) => {
            if (this.disposed) return t.dispose()
            entry.texture = t
            this.changed()
          },
          (err: unknown) => {
            entry.error = err instanceof Error ? err.message : 'The image could not be loaded.'
            if (!this.disposed) this.changed()
          },
        )
    }
    return e.texture
  }

  /** Why the image of `blob` could not be loaded, or null. */
  failure(blob: string): string | null {
    return this.entries.get(blob)?.error ?? null
  }

  dispose(): void {
    this.disposed = true
    for (const e of this.entries.values()) e.texture?.dispose()
    this.entries.clear()
  }
}

/** The canvases of one built scene. */
export interface CanvasLayer {
  readonly group: Group
  /** The canvases a pick can take (a dialog's preview ones are not in it); hidden ones are not drawn and so not here. */
  readonly specs: readonly CanvasSpec[]
  /** Put the textures that have arrived on their materials. */
  refresh(): void
  /** Ids of the canvases whose picture is on (the rest are waiting for it). */
  pictured(): string[]
  dispose(): void
}

/**
 * The textured rectangles for `drawn`. The ones named in `previewIds` (a dialog's preview, drawn over the model) are in the
 * group but not in `specs`: nothing picks them.
 */
export function buildCanvases(drawn: readonly Canvas[], previewIds: ReadonlySet<string>, cache: TextureCache | null): CanvasLayer {
  const group = new Group()
  group.name = 'canvases'
  const specs: CanvasSpec[] = []
  const materials: Material[] = []
  const geometries: BufferGeometry[] = []
  const waiting: { mesh: Mesh; material: MeshBasicMaterial; blob: string }[] = []
  const attach = (w: (typeof waiting)[number]): boolean => {
    const t = cache?.get(w.blob) ?? null
    if (t === null) return false
    w.material.map = t
    w.material.needsUpdate = true
    w.mesh.visible = true
    return true
  }
  for (const c of drawn) {
    const spec = canvasSpec(c)
    const g = new BufferGeometry()
    g.setAttribute('position', new BufferAttribute(new Float32Array(spec.corners.flatMap((p) => [p.x, p.y, p.z])), 3))
    g.setAttribute('uv', new BufferAttribute(new Float32Array(UV.flat()), 2))
    g.setIndex([0, 1, 2, 0, 2, 3])
    const material = new MeshBasicMaterial({ transparent: true, opacity: Math.min(100, Math.max(0, c.opacity)) / 100, depthWrite: false, side: DoubleSide })
    material.polygonOffset = true
    material.polygonOffsetFactor = 1
    material.polygonOffsetUnits = 1
    const mesh = new Mesh(g, material)
    mesh.name = `canvas:${c.id}`
    mesh.renderOrder = CANVAS_RENDER_ORDER
    mesh.userData['canvasId'] = c.id
    // nothing to show until the picture is here
    mesh.visible = false
    group.add(mesh)
    materials.push(material)
    geometries.push(g)
    const w = { mesh, material, blob: c.blob }
    if (!attach(w)) waiting.push(w)
    if (!previewIds.has(c.id)) specs.push(spec)
  }
  return {
    group,
    specs,
    pictured: () => group.children.filter((m) => m.visible).map((m) => m.userData['canvasId'] as string),
    refresh() {
      for (let i = waiting.length - 1; i >= 0; i--) if (attach(waiting[i] as (typeof waiting)[number])) waiting.splice(i, 1)
    },
    dispose() {
      for (const g of geometries) g.dispose()
      for (const m of materials) m.dispose()
    },
  }
}

/** The picture's texture coordinates at the quad's four corners, in the order of `CanvasSpec.corners`; a test hook and the one place the mapping is stated. */
export const CANVAS_UV: readonly (readonly [number, number])[] = UV
