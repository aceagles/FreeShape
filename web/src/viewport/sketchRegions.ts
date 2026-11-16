import { BufferAttribute, BufferGeometry, DoubleSide, Group, LineBasicMaterial, LineSegments, Mesh, MeshBasicMaterial, ShapeUtils, Vector2 } from 'three'
import type { Material } from 'three'
import { sketchColor } from '../sketch/svg'
import { visibleRegions } from '../sketch/regions'
import type { RegionShape, SketchRegions } from '../sketch/regions'
import type { StudioOutputs } from '../wire'
import { rgb } from './colors'

/** A region as the view draws it: outlines (the outer loop, then the holes) and its triangles, in studio coordinates. */
export interface DrawnRegion {
  /** Pairs of points, for line segments. */
  outline: Float32Array
  positions: Float32Array
  indices: Uint32Array
}

export const regionKey = (sketch: string, index: number): string => `${sketch}|${index}`

function drawn(r: SketchRegions, shape: RegionShape): DrawnRegion {
  const at = (u: number, v: number): [number, number, number] => [
    r.origin[0] + u * r.x[0] + v * r.y[0],
    r.origin[1] + u * r.x[1] + v * r.y[1],
    r.origin[2] + u * r.x[2] + v * r.y[2],
  ]
  const pos: number[] = []
  const line: number[] = []
  const rings = shape.loops.map((l) => l.map(([u, v]) => new Vector2(u, v)))
  for (const l of shape.loops) {
    for (let i = 0; i < l.length; i++) {
      const a = l[i]!
      const b = l[(i + 1) % l.length]!
      line.push(...at(a[0], a[1]), ...at(b[0], b[1]))
    }
    for (const [u, v] of l) pos.push(...at(u, v))
  }
  const tri = ShapeUtils.triangulateShape(rings[0]!, rings.slice(1)).flat()
  return { outline: Float32Array.from(line), positions: Float32Array.from(pos), indices: Uint32Array.from(tri) }
}

/**
 * Every region of the sketches the eye shows (card I7.13): a faint fill and a thin outline drawn on the sketch's plane, so
 * a region is seen before the pen is put on it. Returns what the look overlay draws a selected or Aimed region from.
 */
export function buildSketchRegions(outputs: StudioOutputs, materials: Material[], geometries: BufferGeometry[]): { group: Group; regions: Map<string, DrawnRegion> } {
  const group = new Group()
  group.name = 'sketch-regions'
  const regions = new Map<string, DrawnRegion>()
  const fill = new MeshBasicMaterial({ color: rgb(sketchColor.region).clone().convertSRGBToLinear(), transparent: true, opacity: sketchColor.regionAlpha, depthWrite: false, side: DoubleSide })
  const ink = new LineBasicMaterial({ color: rgb(sketchColor.region).clone().convertSRGBToLinear() })
  materials.push(fill, ink)
  for (const r of visibleRegions(outputs)) {
    for (const shape of r.shapes) {
      const d = drawn(r, shape)
      regions.set(regionKey(r.sketch, shape.index), d)
      const g = new BufferGeometry()
      g.setAttribute('position', new BufferAttribute(d.positions, 3))
      g.setIndex(new BufferAttribute(d.indices, 1))
      const m = new Mesh(g, fill)
      m.renderOrder = 3
      const lg = new BufferGeometry()
      lg.setAttribute('position', new BufferAttribute(d.outline, 3))
      const l = new LineSegments(lg, ink)
      l.renderOrder = 3
      geometries.push(g, lg)
      group.add(m, l)
    }
  }
  return { group, regions }
}
