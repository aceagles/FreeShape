// @vitest-environment node
import { readFileSync } from 'node:fs'
import { DoubleSide, Group, Mesh, MeshBasicMaterial, Texture } from 'three'
import { describe, expect, it } from 'vitest'
import { decodeOutputs, isStudio } from '../wire'
import type { Canvas, StudioOutputs } from '../wire'
import { STANDARD_VIEWS, ViewCamera } from './camera'
import { buildCanvases, canvasesToDraw, CANVAS_RENDER_ORDER, TextureCache } from './canvases'
import { DEFAULT_KINDS, PickIndex } from './pick'
import { buildScene } from './scene'

// Canvases on screen (card I12.5): the recorded outputs of a bracket with one canvas (web/fixtures/make_canvas_fixture.py), the textured
// quad each one is, its cache of pictures, and what a pick on one returns.
const dir = new URL('../../fixtures/', import.meta.url)
function recorded(): StudioOutputs {
  const b = readFileSync(new URL('bracket.canvas.outputs.fsw', dir))
  const out = decodeOutputs(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer)
  if (!isStudio(out)) throw new Error('not a studio')
  return out
}

const canvas = (over: Partial<Canvas> = {}): Canvas => ({
  id: 'F5',
  name: 'Canvas 1',
  blob: 'a'.repeat(64),
  origin: [0, 0, 0],
  xdir: [1, 0, 0],
  ydir: [0, 1, 0],
  normal: [0, 0, 1],
  width: 40,
  height: 20,
  opacity: 50,
  flipH: false,
  flipV: false,
  selectable: true,
  hidden: false,
  filename: 'photo.png',
  pixels: [64, 32],
  ...over,
})

/** A picture that is here at once (the cache's fetch and decode are the test's own). */
function cache(fetched: string[] = []): TextureCache {
  return new TextureCache(
    (blob) => {
      fetched.push(blob)
      return Promise.resolve(new Blob(['x']))
    },
    () => {},
    () => Promise.resolve(new Texture()),
  )
}
const settle = () => new Promise((r) => setTimeout(r, 0))
const quad = (root: Group, id = 'F5') => root.getObjectByName(`canvas:${id}`) as Mesh
const corners = (m: Mesh): number[][] => {
  const p = m.geometry.getAttribute('position')
  return [0, 1, 2, 3].map((i) => [p.getX(i), p.getY(i), p.getZ(i)])
}
const uvs = (m: Mesh): number[][] => {
  const t = m.geometry.getAttribute('uv')
  return [0, 1, 2, 3].map((i) => [t.getX(i), t.getY(i)])
}
const dist = (a: number[], b: number[]) => Math.hypot(a[0]! - b[0]!, a[1]! - b[1]!, a[2]! - b[2]!)

describe('the recorded outputs', () => {
  it('decode the canvas the engine sent, with its frame (rotation and the flip already in the two directions)', () => {
    const out = recorded()
    expect(out.canvases).toHaveLength(1)
    const c = out.canvases[0]!
    expect([c.id, c.name, c.filename, c.pixels]).toEqual([expect.any(String), 'Canvas 1', 'photo.png', [64, 32]])
    expect(c.blob).toMatch(/^[0-9a-f]{64}$/)
    expect([c.width, c.height, c.opacity, c.flipH, c.flipV, c.selectable, c.hidden]).toEqual([40, 20, 30, true, false, true, false])
    expect(c.origin).toEqual([5, -3, 0])
    // Top's x and y turned 90 degrees: x -> +y, y -> -x; then flip_h reverses the picture's x
    expect(c.xdir).toEqual([0, -1, 0])
    expect(c.ydir).toEqual([-1, 0, 0])
    expect(c.normal).toEqual([0, 0, 1])
  })

  it('an older payload with no canvases field decodes as having none', () => {
    const b = readFileSync(new URL('bracket.outputs.fsw', dir))
    const out = decodeOutputs(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer)
    expect(isStudio(out) && out.canvases).toEqual([])
  })
})

describe('the scene', () => {
  it('draws one textured mesh in the canvas frame and size, once its picture has arrived', async () => {
    const out = recorded()
    const pictures = cache()
    const built = buildScene(out, { images: pictures })
    expect(built.stats.canvases).toBe(1)
    const m = quad(built.root, out.canvases[0]!.id)
    // the picture is fetched, so nothing shows until it is here
    expect(built.canvasPictures()).toEqual([])
    expect(m.visible).toBe(false)
    await settle()
    built.refreshCanvases()
    expect(built.canvasPictures()).toEqual([out.canvases[0]!.id])
    expect(m.visible).toBe(true)
    expect((m.material as MeshBasicMaterial).map).toBeInstanceOf(Texture)
    // 40 mm along the picture's x (the plane's -y), 20 mm along its y (the plane's -x), centred on (5, -3, 0)
    const c = corners(m)
    expect(dist(c[0]!, c[1]!)).toBeCloseTo(40, 9)
    expect(dist(c[1]!, c[2]!)).toBeCloseTo(20, 9)
    expect([0, 1, 2].map((k) => (c[0]![k]! + c[2]![k]!) / 2)).toEqual([5, -3, 0].map((v) => expect.closeTo(v, 9)))
    expect(c.every((p) => Math.abs(p[2]!) < 1e-9)).toBe(true)
    // the picture's left-to-right is the quad's first side: u runs 0 -> 1 along xdir (0, -1, 0)
    expect(c[1]![1]! - c[0]![1]!).toBeCloseTo(-40, 9)
    built.dispose()
    pictures.dispose()
  })

  it("Opacity 30 sets the material's opacity to 0.3, and the quad is drawn after the parts without writing depth", () => {
    const built = buildScene(recorded(), { images: cache() })
    const m = quad(built.root, recorded().canvases[0]!.id)
    const mat = m.material as MeshBasicMaterial
    expect(mat.opacity).toBeCloseTo(0.3, 9)
    expect([mat.transparent, mat.depthWrite, mat.depthTest, mat.side]).toEqual([true, false, true, DoubleSide])
    // after the Context bodies (2), before the planes and the sketch regions (3)
    expect(m.renderOrder).toBe(CANVAS_RENDER_ORDER)
    expect(m.renderOrder).toBeGreaterThan(2)
    expect(m.renderOrder).toBeLessThan(3)
    built.dispose()
  })

  it('flip_h mirrors the texture coordinates: the same side of the plane gets the other end of the picture', () => {
    // as the engine sends them: no flip draws the picture's x along the plane's x; flip_h reverses the direction it is drawn along
    const plain = buildCanvases([canvas()], new Set(), null).group.children[0] as Mesh
    const flipped = buildCanvases([canvas({ flipH: true, xdir: [-1, 0, 0] })], new Set(), null).group.children[0] as Mesh
    const uAtLowestX = (m: Mesh): number => {
      const c = corners(m)
      const low = c.reduce((a, b) => (b[0]! < a[0]! ? b : a))
      return uvs(m)[c.indexOf(low)]![0]!
    }
    expect(uAtLowestX(plain)).toBe(0)
    expect(uAtLowestX(flipped)).toBe(1)
    // flip_v likewise on the other axis
    const vAtLowestY = (m: Mesh): number => {
      const c = corners(m)
      const low = c.reduce((a, b) => (b[1]! < a[1]! ? b : a))
      return uvs(m)[c.indexOf(low)]![1]!
    }
    const up = buildCanvases([canvas({ flipV: true, ydir: [0, -1, 0] })], new Set(), null).group.children[0] as Mesh
    expect(vAtLowestY(plain)).toBe(0)
    expect(vAtLowestY(up)).toBe(1)
  })

  it('fetches a picture once per blob: two canvases of one image, and a scene built again, share the texture', async () => {
    const fetched: string[] = []
    const pictures = cache(fetched)
    const two = [canvas({ id: 'F5' }), canvas({ id: 'F6', origin: [50, 0, 0] }), canvas({ id: 'F7', blob: 'b'.repeat(64) })]
    const first = buildCanvases(two, new Set(), pictures)
    await settle()
    first.refresh()
    expect(first.pictured()).toEqual(['F5', 'F6', 'F7'])
    const again = buildCanvases(two, new Set(), pictures)
    expect(again.pictured()).toEqual(['F5', 'F6', 'F7'])
    expect(fetched).toEqual(['a'.repeat(64), 'b'.repeat(64)])
    expect(((first.group.children[0] as Mesh).material as MeshBasicMaterial).map).toBe(((again.group.children[0] as Mesh).material as MeshBasicMaterial).map)
  })

  it('a picture that cannot be fetched is not drawn, and the cache says why', async () => {
    const pictures = new TextureCache(() => Promise.reject(new Error('The image is not in this Part Studio any more.')), () => {})
    const layer = buildCanvases([canvas()], new Set(), pictures)
    await settle()
    layer.refresh()
    expect(layer.pictured()).toEqual([])
    expect(pictures.failure('a'.repeat(64))).toBe('The image is not in this Part Studio any more.')
  })

  it('a hidden canvas is not drawn, and a dialog preview replaces the canvas of its id', () => {
    expect(canvasesToDraw([canvas({ hidden: true })], null)).toEqual([])
    const base = canvas({ opacity: 50 })
    const preview = canvas({ opacity: 20 })
    expect(canvasesToDraw([base, canvas({ id: 'F9' })], [preview]).map((c) => [c.id, c.opacity])).toEqual([
      ['F9', 50],
      ['F5', 20],
    ])
    // a preview's canvas that the base already has, unchanged, is not drawn twice
    const out = recorded()
    const ghost = { ...out, canvases: [...out.canvases, canvas({ id: 'NEW', origin: [90, 0, 0] })] }
    const built = buildScene(out, { ghost, images: cache() })
    expect(built.stats.canvases).toBe(2)
    expect(built.canvases.map((c) => c.id)).toEqual([out.canvases[0]!.id])
    built.dispose()
  })
})

describe('picking', () => {
  const out = recorded()
  const bounds = buildScene(out).bounds
  // the bracket seen from above, with canvases of 10 m square at a height of our choosing, the middle of its footprint under the pen
  function rig(canvases: Canvas[], filter?: readonly string[], xy?: [number, number]) {
    const model = { ...out, canvases }
    const built = buildScene(model, { images: cache() })
    const view = new ViewCamera()
    view.resize(1024, 768)
    view.setBack(STANDARD_VIEWS['top'])
    view.fit(built.fitBounds)
    const index = new PickIndex(model, built)
    const at = view.project([xy?.[0] ?? (bounds.lo[0] + bounds.hi[0]) / 2, xy?.[1] ?? (bounds.lo[1] + bounds.hi[1]) / 2, bounds.hi[2]])
    return { pick: (kinds: readonly string[] | undefined = filter) => index.pick(view, at.x, at.y, 'mouse', kinds as never), built }
  }
  const huge = (z: number, over: Partial<Canvas> = {}): Canvas => canvas({ origin: [0, 0, z], width: 10000, height: 10000, ...over })

  it("a click on a Selectable canvas picks its feature, ahead of the plane it lies in", () => {
    const r = rig([huge(bounds.hi[2] + 50)])
    expect(r.pick()).toMatchObject({ kind: 'canvas', key: 'F5', part: null, index: -1 })
    expect(DEFAULT_KINDS).toContain('canvas')
    r.built.dispose()
  })

  it('a part face in front of the canvas hides it: the pick is the face', () => {
    const r = rig([huge(bounds.lo[2] - 50)])
    expect(r.pick()).toMatchObject({ kind: 'face' })
    r.built.dispose()
  })

  it('a canvas that is not Selectable is never picked: what is behind it is, or nothing', () => {
    const r = rig([huge(bounds.hi[2] + 50, { selectable: false })])
    expect(r.pick()?.kind).not.toBe('canvas')
    // with only canvases wanted, nothing is under the pen
    expect(r.pick(['canvas'])).toBeNull()
    r.built.dispose()
  })

  it('a canvas the eye hides is not picked, and one outside the filter is skipped (a dialog field that wants a plane)', () => {
    expect(rig([huge(bounds.hi[2] + 50, { hidden: true })]).pick(['canvas'])).toBeNull()
    // (inside the Top plane's own square, which is smaller than the bracket)
    expect(rig([huge(bounds.hi[2] + 50)], undefined, [0, 0]).pick(['plane'])?.kind).toBe('plane')
  })

  it('the pen outside its rectangle picks what is under it', () => {
    const r = rig([huge(bounds.hi[2] + 50, { width: 1, height: 1, origin: [bounds.hi[0] + 5000, 0, bounds.hi[2] + 50] })])
    expect(r.pick()?.kind).not.toBe('canvas')
  })
})
