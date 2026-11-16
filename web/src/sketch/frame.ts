import { Vector3 } from 'three'
import type { ViewCamera } from '../viewport/camera'
import type { Frame, Uv } from './wire'

const v3 = (t: readonly number[]): Vector3 => new Vector3(t[0], t[1], t[2])

/** The world point of a sketch point. */
export function uvToWorld(f: Frame, uv: Uv): Vector3 {
  return v3(f.origin).addScaledVector(v3(f.xdir), uv[0]).addScaledVector(v3(f.ydir), uv[1])
}

/** The sketch coordinates of a world point projected on the plane. */
export function worldToUv(f: Frame, p: Vector3): Uv {
  const d = p.clone().sub(v3(f.origin))
  return [d.dot(v3(f.xdir)), d.dot(v3(f.ydir))]
}

/**
 * The sketch point under a view position (CSS pixels from the top left): the ray of the view through that position
 * meets the sketch plane there. Null when the view looks along the plane (it is edge on) and the ray never meets it.
 * Drawing, picking and dragging use this in the 2D and the 3D view alike.
 */
export function viewToUv(view: ViewCamera, f: Frame, x: number, y: number): Uv | null {
  const { back } = view.axes()
  const origin = view.unproject(x, y)
  const normal = v3(f.normal)
  const along = -back.dot(normal)
  if (Math.abs(along) < 1e-9) return null
  const t = v3(f.origin).sub(origin).dot(normal) / along
  return worldToUv(f, origin.addScaledVector(back, -t))
}

/** Where a sketch point lands in the view (CSS pixels from the top left). */
export function uvToView(view: ViewCamera, f: Frame, uv: Uv): { x: number; y: number } {
  const w = uvToWorld(f, uv)
  return view.project([w.x, w.y, w.z])
}
