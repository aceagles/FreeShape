import { OrthographicCamera, Vector3 } from 'three'

export type Triple = readonly [number, number, number]
export interface Bounds {
  readonly lo: Triple
  readonly hi: Triple
}

/** Pixels left empty on each side of the model when it is fitted (the desktop leaves 120 across). */
export const FIT_MARGIN = 60
/** The tightest a fit ever zooms: half the box is at least this many mm. */
export const MIN_HALF = 2.5

/** Direction from the target towards the eye in the isometric view the desktop opens with. Z is up. */
const ISO_BACK = new Vector3(1, -1, 1).normalize()
const UP = new Vector3(0, 0, 1)

export interface CameraState {
  target: Triple
  /** mm per CSS pixel */
  scale: number
  /** unit vector from the target towards the eye */
  back: Triple
  up: Triple
  /** viewport size in CSS pixels */
  width: number
  height: number
}

const tri = (v: Vector3): Triple => [v.x, v.y, v.z]

/**
 * Orthographic orbit camera with Z up, expressed as the desktop does: a target, a view direction and a scale in mm
 * per pixel. `camera` is the three.js camera kept in step by `apply()`.
 */
export class ViewCamera {
  readonly camera = new OrthographicCamera(-1, 1, 1, -1, 1, 10)
  target = new Vector3()
  back = ISO_BACK.clone()
  scale = 0.25
  width = 800
  height = 600
  /** Radius of everything drawn; sets the depth range so nothing is clipped. */
  depthRadius = 100

  constructor() {
    this.camera.up.copy(UP)
    this.apply()
  }

  resize(width: number, height: number): void {
    this.width = Math.max(width, 1)
    this.height = Math.max(height, 1)
    this.apply()
  }

  /** Unit view axes in world coordinates: right, up, back. */
  axes(): { right: Vector3; up: Vector3; back: Vector3 } {
    const back = this.back.clone().normalize()
    const right = new Vector3().crossVectors(UP, back)
    if (right.lengthSq() < 1e-12) right.set(1, 0, 0)
    right.normalize()
    const up = new Vector3().crossVectors(back, right).normalize()
    return { right, up, back }
  }

  /** Frame the box in the current view direction, leaving FIT_MARGIN pixels on each side. */
  fit(bounds: Bounds): void {
    const { lo, hi } = bounds
    const centre = new Vector3((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
    const { right, up } = this.axes()
    let rMin = Infinity
    let rMax = -Infinity
    let uMin = Infinity
    let uMax = -Infinity
    for (const x of [lo[0], hi[0]])
      for (const y of [lo[1], hi[1]])
        for (const z of [lo[2], hi[2]]) {
          const d = new Vector3(x, y, z).sub(centre)
          const r = d.dot(right)
          const u = d.dot(up)
          rMin = Math.min(rMin, r)
          rMax = Math.max(rMax, r)
          uMin = Math.min(uMin, u)
          uMax = Math.max(uMax, u)
        }
    this.target.copy(centre).addScaledVector(right, (rMin + rMax) / 2).addScaledVector(up, (uMin + uMax) / 2)
    const wExt = Math.max(rMax - rMin, 1e-3)
    const hExt = Math.max(uMax - uMin, 1e-3)
    this.scale = Math.max(wExt / Math.max(this.width - 2 * FIT_MARGIN, 50), hExt / Math.max(this.height - 2 * FIT_MARGIN, 50))
    const dx = hi[0] - lo[0]
    const dy = hi[1] - lo[1]
    const dz = hi[2] - lo[2]
    this.depthRadius = Math.max(Math.hypot(dx, dy, dz) / 2 + centre.distanceTo(this.target), 1)
    this.apply()
  }

  /** Where a world point lands in the view, in CSS pixels from the top left. */
  project(p: Triple): { x: number; y: number } {
    const { right, up } = this.axes()
    const d = new Vector3(p[0], p[1], p[2]).sub(this.target)
    return { x: this.width / 2 + d.dot(right) / this.scale, y: this.height / 2 - d.dot(up) / this.scale }
  }

  /** Push target, direction and scale into the three.js camera. */
  apply(): void {
    const c = this.camera
    const hw = (this.width * this.scale) / 2
    const hh = (this.height * this.scale) / 2
    c.left = -hw
    c.right = hw
    c.top = hh
    c.bottom = -hh
    const r = Math.max(this.depthRadius * 4, 1)
    // The eye sits 3r from the target; the visible slab runs from r/2 to 5.5r in front of it, so a model that is
    // panned a few model-sizes off centre still lies inside it.
    c.near = r * 0.5
    c.far = r * 5.5
    c.position.copy(this.target).addScaledVector(this.back, r * 3)
    c.up.copy(UP)
    c.lookAt(this.target)
    c.updateProjectionMatrix()
    c.updateMatrixWorld(true)
  }

  state(): CameraState {
    return {
      target: tri(this.target),
      scale: this.scale,
      back: tri(this.back.clone().normalize()),
      up: tri(this.axes().up),
      width: this.width,
      height: this.height,
    }
  }
}
