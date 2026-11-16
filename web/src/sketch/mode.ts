import type { PenHandler, PenPoint, PointerModule } from '../input/pointer'
import type { Bounds, ViewCamera } from '../viewport/camera'
import type { PickPointer } from '../viewport/pick'
import { applyLive, PointerDrag } from './drag'
import { drawSketch } from './draw'
import type { SketchDrawing } from './draw'
import { handleSpots, SketchHandles } from './handle'
import type { HandleSpot } from './handle'
import { isMovable } from './select'
import { uvToView, viewToUv } from './frame'
import { layoutSketch } from './layout'
import type { ScreenLayout } from './layout'
import { pickSketch } from './pick'
import type { SketchHit } from './pick'
import type { SnapContext } from './tools/snap'
import type { SketchStore } from './store'
import { sketchMarkup } from './svg'
import type { DrawStats } from './draw'
import type { Uv } from './wire'
import { QuickMenu } from './quickmenu'
import { sketchToolState } from './strip'
import { DrawTools } from './tools/drawing'
import type { DrawReport } from './tools/drawing'

/** What sketch mode needs of the viewport that hosts it. */
export interface SketchHost {
  readonly view: ViewCamera
  readonly pointer: PointerModule
  /** The element the sketch layer is added to (the canvas's parent). */
  readonly overlayParent: HTMLElement
  requestRender(): void
  /** The model's box, for an empty sketch that has nothing to fit to; null when nothing is drawn. */
  modelBounds(): Bounds | null
}

/** What `window.__freeshapeViewport.sketch` reports: the state of sketch mode, for tests that look at the page from outside. */
export interface SketchReport {
  nav: '2d' | '3d'
  name: string
  stats: DrawStats
  selected: readonly SketchHit[]
  /** Where an item is on screen now (CSS pixels from the view's top left), or null. */
  screenOf(kind: 'point' | 'entity' | 'dimension' | 'constraint', id: string): { x: number; y: number } | null
  /** What a pointer at a view position picks in the sketch. */
  pickAt(x: number, y: number, pointer: PickPointer): SketchHit | null
  /** The sketch point under a view position (the ray of the view meets the sketch plane), or null. */
  uvAt(x: number, y: number): Uv | null
  /** The angle on screen, in radians, of the sketch's x axis (0 = pointing right). */
  xAxisAngle: number
  /** The angle between the view direction and the plane's normal, radians: 0 when the view is square to the plane. */
  tilt: number
  /** Where an item's Drag handle is on screen (CSS pixels from the view's top left), or null when it shows none. */
  handleOf(kind: 'point' | 'entity' | 'dimension', id: string): { x: number; y: number } | null
  /** Times the sketch layer was written. */
  paints: number
  /** The drawing tools: the tool on, the shape in progress and where the pen's Aim is (I7.8). */
  draw: DrawReport
}

const SIZE_PAD = 1.25
/** Smallest half-size (mm) of the box a sketch is fitted to. */
const MIN_FIT_HALF = 10

/**
 * Sketch mode of a viewport (I7.7). It draws the open sketch as an SVG layer over the canvas, keeps the view square to the
 * sketch plane in 2D (the pointer module pans and twists instead of orbiting), takes the pointer's Aim and Commit for the
 * sketch's items, and answers picks. It owns nothing the viewport draws: the viewport makes the solids see-through.
 */
export class SketchMode {
  private readonly layer: HTMLDivElement
  private drawing: SketchDrawing | null = null
  private layout: ScreenLayout | null = null
  private lastFace = -1
  private lastOpen = -1
  private lastNav: '2d' | '3d' = '2d'
  /** The sketch's turn on screen set by 2D twists, kept for when the view comes back square (radians). */
  private twist = 0
  private paints = 0
  private before: readonly SketchHit[] | null = null
  /** The pen is down (its first Aim has been seen). */
  private penDown = false
  private readonly off: () => void
  private lastMarkup = ''
  private drawnFor: unknown = null
  private readonly draw: DrawTools
  private readonly menu: QuickMenu
  private drawnLive: unknown = null
  private readonly handles: SketchHandles
  private readonly penDrag: PointerDrag

  constructor(
    private readonly host: SketchHost,
    private readonly store: SketchStore,
  ) {
    this.layer = document.createElement('div')
    this.layer.className = 'sketch-layer'
    this.layer.dataset['testid'] = 'sketch-layer'
    host.overlayParent.append(this.layer)
    this.draw = new DrawTools(
      {
        overlayParent: host.overlayParent,
        width: () => host.view.width,
        height: () => host.view.height,
        requestRender: () => host.requestRender(),
        context: (pointer) => this.snapContext(pointer),
      },
      store,
    )
    this.menu = new QuickMenu({ overlayParent: host.overlayParent, width: () => host.view.width, height: () => host.view.height }, store, sketchToolState)
    const dragView = { uvAt: (x: number, y: number) => this.uvAt(x, y), labelUv: (id: string) => this.labelUv(id) }
    this.penDrag = new PointerDrag(store, dragView)
    this.handles = new SketchHandles(store, { ...dragView, spots: () => this.spots() }, () => this.host.requestRender())
    this.wirePointer()
    this.off = store.subscribe(() => this.onStore())
    this.onStore()
  }

  dispose(): void {
    this.off()
    this.draw.dispose()
    this.menu.dispose()
    this.host.pointer.setHoldHandler(null)
    this.host.pointer.setPlanar(false)
    this.host.pointer.setHandleHandler(null)
    this.layer.remove()
  }

  get nav(): '2d' | '3d' {
    return this.store.getState().nav
  }

  private wirePointer(): void {
    const p = this.host.pointer
    const itemAt = (x: number, y: number, kind: PickPointer): SketchHit | null => (this.layout ? pickSketch(this.layout, x, y, kind) : null)
    const picking = (kind: 'pen' | 'mouse'): PenHandler => ({
      aim: (pt: PenPoint) => {
        if (kind === 'pen') {
          if (this.penDrag.armed) {
            // a press that began on a movable item: past 6 px it drags the item, short of that it is an Aim as before
            this.penDrag.move(pt.x, pt.y)
            if (this.penDrag.active) return
          } else if (!this.penDown) {
            const hit = itemAt(pt.x, pt.y, kind)
            const view = this.store.getState().view
            if (view && this.selecting && isMovable(view.sketch, hit)) this.penDrag.arm(hit, pt.x, pt.y)
          }
          this.penDown = true
        }
        this.store.aim(itemAt(pt.x, pt.y, kind))
      },
      commit: (pt: PenPoint) => {
        if (kind === 'pen') {
          this.penDown = false
          if (this.penDrag.release(pt.x, pt.y)) return
        }
        if (kind === 'mouse') this.store.aim(itemAt(pt.x, pt.y, kind))
        const hit = this.store.getState().aimed
        if (!this.store.pointerTool?.commit(hit, this.uvAt(pt.x, pt.y), kind)) this.store.select(hit)
        if (kind === 'pen') this.store.aim(null)
      },
      cancel: () => {
        if (kind === 'pen') {
          this.penDown = false
          this.penDrag.cancel()
        }
        this.store.aim(null)
      },
    })
    p.setPenHandler(this.draw.wrap('pen', picking('pen')))
    p.setMouseHandler(this.draw.wrap('mouse', picking('mouse')))
    p.setHoldHandler(this.menu)
    // a Drag handle takes a press before the pen's Aim and the finger's navigation
    p.setHandleHandler(this.handles)
    p.setTapHandler({
      tap: (x, y) => {
        if (this.draw.active) return // a finger places nothing with a drawing tool
        const hit = itemAt(x, y, 'touch')
        if (this.store.pointerTool?.commit(hit, this.uvAt(x, y), 'touch')) {
          this.before = null
          return
        }
        this.before = this.store.getState().selected
        this.store.select(hit)
      },
      doubleTap: (x, y) => {
        const hit = itemAt(x, y, 'touch')
        return hit !== null && this.store.pointerTool?.doubleTap(hit) === true
      },
      undoTap: () => {
        if (this.draw.active) return
        if (this.before) this.store.setSelection(this.before)
        this.before = null
      },
    })
  }

  /** The sketch point under a view position, or null. */
  private uvAt(x: number, y: number): Uv | null {
    const f = this.store.getState().view?.frame
    return f ? viewToUv(this.host.view, f, x, y) : null
  }

  /** What the drawing tools snap against, for the view as it is now. */
  private snapContext(pointer: 'pen' | 'mouse' | 'touch'): SnapContext | null {
    const s = this.store.getState()
    if (!s.open || !s.view || !this.drawing) return null
    const { view } = this.host
    const f = s.view.frame
    const drawing = this.drawing
    const toView = (uv: Uv) => uvToView(view, f, uv)
    return { sketch: s.view.sketch, layout: layoutSketch(drawing, toView), edges: s.view.edges, toUv: (x, y) => viewToUv(view, f, x, y), toView, pointer }
  }

  /** The centre of a dimension's label, in sketch coordinates. */
  private labelUv(id: string): Uv | null {
    const l = this.layout?.dims.find((d) => d.id === id)?.label
    return l ? this.uvAt(l.x + l.w / 2, l.y + l.h / 2) : null
  }

  /** Select is the tool: no drawing tool and no Dimension is on, so an item can be dragged. */
  private get selecting(): boolean {
    return !this.draw.active && this.store.getState().tool !== 'dimension'
  }

  /** The Drag handles of the selected items now. */
  private spots(): readonly HandleSpot[] {
    const s = this.store.getState()
    if (!s.view || !this.layout || !this.selecting) return []
    return handleSpots(this.layout, s.selected, s.view.sketch, this.host.view.width)
  }

  private onStore(): void {
    const s = this.store.getState()
    if (!s.open || !s.view) return
    if (s.openSeq !== this.lastOpen) {
      // a sketch was opened: face it, fit it
      this.lastOpen = s.openSeq
      this.lastFace = s.faceSeq
      this.lastNav = s.nav
      this.twist = 0
      this.host.view.lookAtPlane(s.view.frame.normal, s.view.frame.ydir, 0)
      this.fitToSketch()
    } else {
      if (s.nav !== this.lastNav) {
        // leaving 2D keeps the turn the user gave the sketch; coming back restores it
        if (s.nav === '3d') this.twist = this.currentTwist()
        this.lastNav = s.nav
      }
      if (s.faceSeq !== this.lastFace) {
        this.lastFace = s.faceSeq
        this.faceView()
      }
    }
    this.host.pointer.setPlanar(s.nav === '2d')
    this.host.requestRender()
  }

  private currentTwist(): number {
    const f = this.store.getState().view?.frame
    if (!f) return 0
    const v = this.host.view
    const n = v.state().back
    const aligned = Math.abs(n[0] * f.normal[0] + n[1] * f.normal[1] + n[2] * f.normal[2]) > 1 - 1e-9
    if (!aligned) return this.twist
    return v.roll - v.rollFor(f.ydir)
  }

  /** Turn the view square to the sketch plane, the sketch's y direction up on screen and turned by the 2D twist. */
  private faceView(): void {
    const f = this.store.getState().view?.frame
    if (!f) return
    this.twist = this.currentTwist()
    this.host.view.lookAtPlane(f.normal, f.ydir, this.twist)
  }

  /** Frame the sketch: its box with some air round it, or the model's box when it is empty. */
  private fitToSketch(): void {
    const s = this.store.getState().view
    if (!s) return
    const { view } = this.host
    const pts = Object.values(s.sketch.points)
    const us = pts.map((p) => p[0])
    const vs = pts.map((p) => p[1])
    const has = pts.length > 1
    let lo: Uv = [-MIN_FIT_HALF, -MIN_FIT_HALF]
    let hi: Uv = [MIN_FIT_HALF, MIN_FIT_HALF]
    if (has) {
      const cu = (Math.min(...us) + Math.max(...us)) / 2
      const cv = (Math.min(...vs) + Math.max(...vs)) / 2
      const hu = Math.max(((Math.max(...us) - Math.min(...us)) / 2) * SIZE_PAD, MIN_FIT_HALF)
      const hv = Math.max(((Math.max(...vs) - Math.min(...vs)) / 2) * SIZE_PAD, MIN_FIT_HALF)
      lo = [cu - hu, cv - hv]
      hi = [cu + hu, cv + hv]
    } else {
      const b = this.host.modelBounds()
      if (b) {
        const half = Math.max(...[0, 1, 2].map((i) => (b.hi[i]! - b.lo[i]!) / 2), MIN_FIT_HALF)
        lo = [-half, -half]
        hi = [half, half]
      }
    }
    const f = s.frame
    const corners: Uv[] = [lo, [hi[0], lo[1]], hi, [lo[0], hi[1]]]
    const world = corners.map((c) => [f.origin[0] + f.xdir[0] * c[0] + f.ydir[0] * c[1], f.origin[1] + f.xdir[1] * c[0] + f.ydir[1] * c[1], f.origin[2] + f.xdir[2] * c[0] + f.ydir[2] * c[1]] as const)
    const box: Bounds = {
      lo: [Math.min(...world.map((w) => w[0])), Math.min(...world.map((w) => w[1])), Math.min(...world.map((w) => w[2]))],
      hi: [Math.max(...world.map((w) => w[0])), Math.max(...world.map((w) => w[1])), Math.max(...world.map((w) => w[2]))],
    }
    view.fit(box)
  }

  /** Draw the layer for the view as it is now. Called once per frame, before the canvas is drawn. */
  update(): void {
    const s = this.store.getState()
    if (!s.open || !s.view) {
      this.layer.replaceChildren()
      this.draw.update()
      return
    }
    const { view } = this.host
    const f = s.view.frame
    if (this.drawnFor !== s.view || this.drawnLive !== s.live) {
      // a drag draws its items where the pointer or the solve has them, over the sketch the server holds
      this.drawing = drawSketch(applyLive(s.view.sketch, s.live), s.view.status)
      this.drawnFor = s.view
      this.drawnLive = s.live
    }
    this.layout = layoutSketch(this.drawing!, (uv) => uvToView(view, f, uv))
    const handles = this.spots()
    const markup = sketchMarkup(this.layout, { selected: s.selected, aimed: s.aimed, handles, grabbed: this.handles.grabbed }, view.width, view.height)
    if (markup !== this.lastMarkup) {
      this.lastMarkup = markup
      this.layer.innerHTML = markup
      this.paints++
    }
    this.draw.update()
  }

  /** Turn the view square to the sketch plane (the cube's Normal to). */
  normalTo(): void {
    this.store.normalTo()
  }

  /** Switch to the standard view of a cube tap: the sketch can no longer be held square, so the mode is 3D. */
  leavePlanar(): void {
    this.store.setNav('3d')
  }

  report(): SketchReport | null {
    const s = this.store.getState()
    if (!s.open || !s.view || !this.drawing) return null
    const { view } = this.host
    const f = s.view.frame
    const drawing = this.drawing
    // measured against the view as it is now, not as the last frame drew it
    const layout = (): ScreenLayout => layoutSketch(drawing, (uv) => uvToView(view, f, uv))
    const o = uvToView(view, f, [0, 0])
    const x = uvToView(view, f, [1, 0])
    const back = view.state().back
    const dotN = back[0] * f.normal[0] + back[1] * f.normal[1] + back[2] * f.normal[2]
    return {
      nav: s.nav,
      name: s.view.name,
      stats: drawing.stats,
      selected: s.selected,
      screenOf: (kind, id) => {
        const l = layout()
        if (kind === 'point') return l.points.find((p) => p.id === id) ?? null
        if (kind === 'entity') {
          const st = l.strokes.find((q) => q.id === id)
          if (!st) return null
          if (st.pts.length === 2) return { x: (st.pts[0]!.x + st.pts[1]!.x) / 2, y: (st.pts[0]!.y + st.pts[1]!.y) / 2 }
          return st.pts[Math.floor(st.pts.length / 2)]!
        }
        if (kind === 'dimension') {
          const d = l.dims.find((q) => q.id === id)
          return d ? { x: d.label.x + d.label.w / 2, y: d.label.y + d.label.h / 2 } : null
        }
        return l.glyphs.find((g) => g.id === id) ?? null
      },
      handleOf: (kind, id) => {
        const l = layout()
        const spot = handleSpots(l, s.selected, s.view!.sketch, view.width).find((h) => h.hit.kind === kind && h.hit.id === id)
        return spot ? { x: spot.x, y: spot.y } : null
      },
      pickAt: (px, py, pointer) => pickSketch(layout(), px, py, pointer),
      uvAt: (px, py) => viewToUv(view, f, px, py),
      xAxisAngle: Math.atan2(x.y - o.y, x.x - o.x),
      tilt: Math.acos(Math.min(1, Math.abs(dotN))),
      paints: this.paints,
      draw: this.draw.report(),
    }
  }
}
