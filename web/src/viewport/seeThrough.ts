import { ShaderMaterial } from 'three'
import type { Material, Object3D } from 'three'
import { LineMaterial } from 'three/addons/lines/LineMaterial.js'

/** How much of a solid shows while a sketch is open: the sketch is the subject, the model is the context. */
export const SEE_THROUGH_ALPHA = 0.22
const EDGE_OPACITY = 0.45

interface Saved {
  alpha: number
  transparent: boolean
  depthWrite: boolean
}
const saved = new WeakMap<Material, Saved>()

/**
 * Make the solids of a built scene see-through (or solid again). Parts' faces get a low alpha and stop writing depth,
 * their edge lines fade; planes and the origin triad are left alone. Safe to call twice with the same value.
 */
export function setSeeThrough(root: Object3D, on: boolean): void {
  root.traverse((o) => {
    const raw = (o as { material?: Material | Material[] }).material
    const list = Array.isArray(raw) ? raw : [raw]
    for (const m of list) {
      if (!m) continue
      if (m instanceof ShaderMaterial && m.uniforms['alpha'] !== undefined) {
        const u = m.uniforms['alpha'] as { value: number }
        if (on) {
          if (!saved.has(m)) saved.set(m, { alpha: u.value, transparent: m.transparent, depthWrite: m.depthWrite })
          u.value = Math.min(u.value, SEE_THROUGH_ALPHA)
          m.transparent = true
          m.depthWrite = false
        } else {
          const s = saved.get(m)
          if (s) {
            u.value = s.alpha
            m.transparent = s.transparent
            m.depthWrite = s.depthWrite
            saved.delete(m)
          }
        }
        m.needsUpdate = true
      } else if (m instanceof LineMaterial) {
        if (on) {
          if (!saved.has(m)) saved.set(m, { alpha: m.opacity, transparent: m.transparent, depthWrite: m.depthWrite })
          m.transparent = true
          m.opacity = Math.min(m.opacity, EDGE_OPACITY)
        } else {
          const s = saved.get(m)
          if (s) {
            m.opacity = s.alpha
            m.transparent = s.transparent
            saved.delete(m)
          }
        }
        m.needsUpdate = true
      }
    }
  })
}
