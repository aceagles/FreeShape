import { BufferGeometry, Group, Mesh } from 'three'
import { LineMaterial } from 'three/addons/lines/LineMaterial.js'
import { describe, expect, it } from 'vitest'
import { ViewCamera } from './camera'
import { setSeeThrough, SEE_THROUGH_ALPHA } from './seeThrough'
import { shadedMaterial } from './shading'
import { Color } from 'three'

describe('lookAtPlane', () => {
  const planes: [string, [number, number, number], [number, number, number]][] = [
    ['Top', [0, 0, 1], [0, 1, 0]],
    ['Front', [0, -1, 0], [0, 0, 1]],
    ['Right', [1, 0, 0], [0, 0, 1]],
    ['a face', [0, -Math.SQRT1_2, Math.SQRT1_2], [0, Math.SQRT1_2, Math.SQRT1_2]],
    ['a bottom face', [0, 0, -1], [1, 0, 0]],
  ]
  for (const [name, normal, up] of planes) {
    it(`looks along the normal of ${name} with its y direction up, turned by the twist`, () => {
      const v = new ViewCamera()
      v.roll = 1.2
      v.lookAtPlane(normal, up)
      expect(v.state().back).toEqual(expect.arrayContaining([expect.any(Number)]))
      const a = v.axes()
      expect(a.back.x * normal[0] + a.back.y * normal[1] + a.back.z * normal[2]).toBeCloseTo(1, 12)
      expect(a.up.x * up[0] + a.up.y * up[1] + a.up.z * up[2]).toBeCloseTo(1, 12)
      v.lookAtPlane(normal, up, 0.5)
      expect(v.roll - v.rollFor(up)).toBeCloseTo(0.5, 12)
    })
  }
})

describe('setSeeThrough', () => {
  it('fades the faces and edges of a solid and puts them back', () => {
    const face = shadedMaterial(new Color(0.7, 0.7, 0.7))
    const edge = new LineMaterial({ linewidth: 1.6 })
    const root = new Group()
    const a = new Mesh(new BufferGeometry(), face)
    const b = new Mesh(new BufferGeometry(), edge)
    root.add(a, b)
    setSeeThrough(root, true)
    expect(face.uniforms['alpha']!.value).toBe(SEE_THROUGH_ALPHA)
    expect(face.transparent).toBe(true)
    expect(face.depthWrite).toBe(false)
    expect(edge.opacity).toBeLessThan(1)
    setSeeThrough(root, true)
    setSeeThrough(root, false)
    expect(face.uniforms['alpha']!.value).toBe(1)
    expect(face.transparent).toBe(false)
    expect(face.depthWrite).toBe(true)
    expect(edge.opacity).toBe(1)
  })

  it('leaves a context body that is already fainter than the see-through alpha as it was', () => {
    const ctx = shadedMaterial(new Color(0.7, 0.7, 0.7), { alpha: 0.1 })
    const root = new Group()
    root.add(new Mesh(new BufferGeometry(), ctx))
    setSeeThrough(root, true)
    expect(ctx.uniforms['alpha']!.value).toBe(0.1)
    setSeeThrough(root, false)
    expect(ctx.uniforms['alpha']!.value).toBe(0.1)
  })
})
