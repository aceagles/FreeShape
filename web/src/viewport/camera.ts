import { OrthographicCamera, Vector3 } from 'three'

export type Triple = readonly [number, number, number]
export interface Bounds {
  readonly lo: Triple
  readonly hi: Triple
}

/** Pixels left empty on each side of the model when it is fitted (the desktop leaves 120 across). */
export const FIT_MARGIN = 60
/** Top margin of a fit: clears the view cube (12 px inset, 72 px square) by 12 px, so a fitted model never meets it. */
export const FIT_MARGIN_TOP = 96
/** The tightest a fit ever zooms: half the box is at least this many mm. */
export const MIN_HALF = 2.5

/** Direction from the target towards the eye in the isometric view the desktop opens with. Z is up. */
const ISO_BACK = new Vector3(1, -1, 1).normalize()
const UP = new Vector3(0, 0, 1)
/** Smallest and largest zoom, mm per CSS pixel. */
export const MIN_SCALE = 1e-3
export const MAX_SCALE = 1e3
/** Orbit speed, radians per CSS pixel dragged. */
export const ORBIT_RATE = 0.008
/** How close to straight up or down an orbit may reach, radians. */
const POLE_MARGIN = 0.01
/** The standard views sit this far off the pole so the turntable keeps a defined up. */
const POLE_NUDGE = 1e-4

export type StandardView = 'front' | 'back' | 'left' | 'right' | 'top' | 'bottom' | 'iso'

/** Direction from the target towards the eye for each standard view (Z up; top and bottom keep X to the right). */
export const STANDARD_VIEWS: Readonly<Record<StandardView, Triple>> = {
  front: [0, -1, 0],
  back: [0, 1, 0],
  left: [-1, 0, 0],
  right: [1, 0, 0],
  top: [0, -POLE_NUDGE, 1],
  bottom: [0, -POLE_NUDGE, -1],
  iso: [1, -1, 1],
}

export interface CameraState {
  target: Triple
  /** mm per CSS pixel */
  scale: number
  /** unit vector from the target towards the eye */
  back: Triple
  up: Triple
  /** Roll of the view about its own back axis, radians; 0 keeps Z up. */
  roll: number
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
  /** Roll about the back axis (radians), applied on top of the Z-up turntable orientation. */
  roll = 0
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
    if (this.roll === 0) return { right, up, back }
    const c = Math.cos(this.roll)
    const s = Math.sin(this.roll)
    return {
      right: right.clone().multiplyScalar(c).addScaledVector(up, s),
      up: up.clone().multiplyScalar(c).addScaledVector(right, -s),
      back,
    }
  }

  /** World point on the plane through the target that lands at this view position (CSS pixels from top left). */
  unproject(x: number, y: number): Vector3 {
    const { right, up } = this.axes()
    return this.target
      .clone()
      .addScaledVector(right, (x - this.width / 2) * this.scale)
      .addScaledVector(up, (this.height / 2 - y) * this.scale)
  }

  /** Move the target so that `p` lands at (x, y); `depth` is p's distance in front of the target (along back). */
  private anchor(p: Vector3, x: number, y: number, depth: number): void {
    const { right, up, back } = this.axes()
    this.target
      .copy(p)
      .addScaledVector(right, -(x - this.width / 2) * this.scale)
      .addScaledVector(up, -(this.height / 2 - y) * this.scale)
      .addScaledVector(back, -depth)
  }

  /** Turn the view by a drag of (dx, dy) pixels about the world point `pivot`, which stays where it is on screen. */
  orbit(dx: number, dy: number, pivot: Vector3): void {
    const at = this.project(tri(pivot))
    const { back } = this.axes()
    const depth = pivot.clone().sub(this.target).dot(back)
    // The drag is in screen axes; the turntable works in the unrolled ones.
    const c = Math.cos(this.roll)
    const s = Math.sin(this.roll)
    const ux = dx * c + dy * s
    const uy = -dx * s + dy * c
    const b = this.back.clone().normalize()
    let azimuth = Math.atan2(b.y, b.x) - ux * ORBIT_RATE
    const limit = Math.PI / 2 - POLE_MARGIN
    const elevation = Math.max(-limit, Math.min(limit, Math.asin(Math.max(-1, Math.min(1, b.z))) + uy * ORBIT_RATE))
    if (Math.abs(b.z) > 1 - 1e-9) azimuth = -Math.PI / 2 - ux * ORBIT_RATE
    this.back.set(Math.cos(elevation) * Math.cos(azimuth), Math.cos(elevation) * Math.sin(azimuth), Math.sin(elevation))
    this.anchor(pivot, at.x, at.y, depth)
    this.apply()
  }

  /** Slide the model with a drag of (dx, dy) pixels. */
  pan(dx: number, dy: number): void {
    const { right, up } = this.axes()
    this.target.addScaledVector(right, -dx * this.scale).addScaledVector(up, dy * this.scale)
    this.apply()
  }

  /**
   * One step of a two-finger gesture: the model point under (fromX, fromY) is brought under (toX, toY) after
   * zooming by `zoom` (above 1 magnifies) and rolling the view by `roll` radians about the screen normal.
   */
  gesture(fromX: number, fromY: number, toX: number, toY: number, zoom: number, roll: number): void {
    const p = this.unproject(fromX, fromY)
    this.scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, this.scale / zoom))
    this.roll += roll
    this.anchor(p, toX, toY, 0)
    this.apply()
  }

  /** Zoom by `factor` (above 1 magnifies) about the view position (x, y). */
  zoomAt(factor: number, x: number, y: number): void {
    this.gesture(x, y, x, y, factor, 0)
  }

  /** Look along `back` (towards the eye) with Z up; roll is cleared. The target stays. */
  setBack(back: Triple): void {
    this.back.set(back[0], back[1], back[2]).normalize()
    this.roll = 0
    this.apply()
  }

  /** Look straight at a face with this outward normal: the nearest axis view when it is not square to one. */
  normalTo(normal: Triple = tri(this.back)): void {
    const k = [0, 1, 2].reduce((best, i) => (Math.abs(normal[i]!) > Math.abs(normal[best]!) ? i : best), 0)
    const names: readonly (readonly [StandardView, StandardView])[] = [
      ['left', 'right'],
      ['front', 'back'],
      ['bottom', 'top'],
    ]
    this.setBack(STANDARD_VIEWS[names[k]![normal[k]! < 0 ? 0 : 1]])
  }

  /** The roll that puts the direction `up` upright on screen for the present view direction (the turntable's up when it is not). */
  rollFor(up: Triple): number {
    const saved = this.roll
    this.roll = 0
    const { right, up: base } = this.axes()
    this.roll = saved
    const u = new Vector3(up[0], up[1], up[2])
    return Math.atan2(-u.dot(right), u.dot(base))
  }

  /**
   * Look straight at a plane with this normal (towards the eye), its `up` direction upright on screen and then turned by
   * `twist` radians (the sketch's 2D view turns the sketch on screen). The target stays; no fit.
   */
  lookAtPlane(normal: Triple, up: Triple, twist = 0): void {
    this.back.set(normal[0], normal[1], normal[2]).normalize()
    this.roll = this.rollFor(up) + twist
    this.apply()
  }

  /** Frame the box in the current view direction, leaving FIT_MARGIN pixels on each side (FIT_MARGIN_TOP at the top, clear of the view cube). */
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
    this.scale = Math.max(
      wExt / Math.max(this.width - 2 * FIT_MARGIN, 50),
      hExt / Math.max(this.height - FIT_MARGIN - FIT_MARGIN_TOP, 50),
    )
    // The free area sits (FIT_MARGIN_TOP - FIT_MARGIN) / 2 px below the middle of the view: put the model there.
    this.target.addScaledVector(up, ((FIT_MARGIN_TOP - FIT_MARGIN) / 2) * this.scale)
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
    c.up.copy(this.axes().up)
    c.lookAt(this.target)
    c.updateProjectionMatrix()
    c.updateMatrixWorld(true)
  }

  /** Put the view back as `state()` gave it (target, zoom, direction and roll; the size stays as it is now). */
  restore(s: CameraState): void {
    this.target.set(...s.target)
    this.scale = s.scale
    this.back.set(...s.back).normalize()
    this.roll = s.roll
  }

  state(): CameraState {
    return {
      target: tri(this.target),
      scale: this.scale,
      back: tri(this.back.clone().normalize()),
      up: tri(this.axes().up),
      roll: this.roll,
      width: this.width,
      height: this.height,
    }
  }
}
