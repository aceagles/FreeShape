import { MathUtils, Quaternion, Scene, Vector3, WebGLRenderer } from 'three'
import type { Object3D } from 'three'
import { ManipulatorLayer } from '../dialog/manipulator'
import { PointerModule } from '../input/pointer'
import type { NavHost } from '../input/pointer'
import { wireSelection } from '../selection/aimCommit'
import type { BoxSource, ContextSink, ViewContextEvent } from '../selection/aimCommit'
import { MouseOverlay } from '../selection/mouseOverlay'
import type { SelectionItem, SelectionStore } from '../selection/store'
import { BOX_KINDS, studioBoxHits } from './boxSelect'
import type { StudioOutputs } from '../wire'
import { SketchMode } from '../sketch/mode'
import type { SketchReport } from '../sketch/mode'
import type { SketchStore } from '../sketch/store'
import { viewportBackground } from './colors'
import { ViewCamera } from './camera'
import type { CameraState, StandardView } from './camera'
import { STANDARD_VIEWS } from './camera'
import { PickIndex, rayPoint, viewRay } from './pick'
import type { PickFilter, PickHit, PickKind, PickPointer } from './pick'
import { tintFeature } from './previewTint'
import { buildScene, DEFAULT_PLANES, EMPTY_STATS } from './scene'
import { DEFAULT_VIEW, shapeStudio } from './visibility'
import type { ViewState } from './visibility'
import type { BuiltScene, SceneStats } from './scene'
import { setSeeThrough } from './seeThrough'
import { TextureCache } from './canvases'
import type { ImageFetcher } from './canvases'

/** What `window.__freeshapeViewport` reports, for tests that look at the page from outside. */
export interface ViewportReport extends SceneStats {
  /** Revision of the outputs on screen; null before the first. */
  revision: number | null
  camera: CameraState
  /** Frames drawn so far. */
  frames: number
  /** Id of the part drawn highlighted, or null (a selected part the eye hides is not drawn, so it is null). */
  highlightedPart: string | null
  /** Why nothing is drawn when the tab on screen is not a Part Studio (its kind: assembly, drawing, variables); null otherwise. */
  emptyKind: string | null
  /** Test hook: the number of the dialog preview drawn in place of the committed model, or null. */
  preview: number | null
  /** Test hook: the feature whose result is drawn tinted (a dialog's edited feature), or null; `tintedParts` is how many parts (the ghost's parts while a dialog is open). */
  tintedFeature: string | null
  tintedParts: number
  /** Test hook: the selection store, as a reader sees it (card I4.2). */
  selection: SelectionReport
  /** Test hook: the open sketch (I7.7) as a reader sees it, or null when no sketch is open in this view. */
  sketch: SketchReport | null
  /** Test hook: what a pointer of this kind would pick at a view position (CSS pixels from the top left), null for nothing. */
  pickAt(x: number, y: number, pointer: PickPointer, filter?: readonly PickKind[]): PickHit | null
  /** Test hook (I10.3): where each drawn mate connector's triad is on screen (CSS pixels from the top left), by its id. */
  connectorScreens(): ConnectorScreen[]
  /** Test hook (I12.5): each drawn canvas's centre on screen (CSS pixels from the top left), whether its picture is on it, and why its image failed, if it did. */
  canvasScreens(): CanvasScreen[]
  /** Test hook (I9.9): what a selection box dragged from (x0, y0) to (x1, y1) would take, with the selection's filter. */
  boxHits(x0: number, y0: number, x1: number, y1: number): SelectionItemReport[]
}

/** Where a mate connector's triad is on screen. */
export interface ConnectorScreen {
  id: string
  instance?: string
  x: number
  y: number
}

/** Where a canvas's centre is on screen. */
export interface CanvasScreen {
  id: string
  x: number
  y: number
  /** A point inside the rectangle, a quarter of its width and height from the centre along the picture's x and y: clear of the origin triad that sits on a canvas centred on the origin. */
  inside: { x: number; y: number }
  pictured: boolean
  error: string | null
}

/** An item of the selection as the test hook reports it. */
/**
 * A dialog's preview: its number, and the feature whose result is tinted. Without `ghost` the drawn outputs are the
 * preview itself, tinted (after Accept, until the revision it made arrives). With `ghost` (card F3, while the dialog is
 * open) the drawn outputs are the model the dialog picks against, and `ghost` is the preview, drawn over it translucent
 * and never picked: what the pen hits is what the feature can use.
 */
export interface PreviewLook {
  number: number
  feature: string
  ghost?: StudioOutputs | null
}

export interface SelectionItemReport {
  kind: PickKind
  part: string | null
  index: number
  name: string | null
  key: string | null
}

/** The selection as the test hook reports it. */
export interface SelectionReport {
  count: number
  items: SelectionItemReport[]
  /** Multi mode is on (the toggle, or a dialog field that takes several items). */
  multi: boolean
  /** The item under the pen or mouse, or null. */
  aimed: SelectionItemReport | null
  /** Times the selection or the Aim changed what the view draws: an Aim move that changes nothing does not count. */
  looks: number
}

/** The arrow keys turn the view this many degrees (freeshape/ui/viewport.py ARROW_STEP_DEG). */
export const ARROW_STEP_DEG = 15

const tri3 = (v: Vector3): [number, number, number] => [v.x, v.y, v.z]

/**
 * An arrow key (the desktop's `rotate_step`): turn the view `deg` about its own up axis (`h`, the Right arrow is -1) or its
 * right axis (`v`, the Up arrow is +1), about `pivot`, which keeps its place in the model. From the Right view six Right
 * arrows reach the Front view.
 */
export function turnView(view: ViewCamera, pivot: Vector3, h: number, v: number, deg = ARROW_STEP_DEG): void {
  const { right, up, back } = view.axes()
  const q = new Quaternion().setFromAxisAngle(h !== 0 ? up : right, MathUtils.degToRad(deg) * (h !== 0 ? h : v))
  view.target.sub(pivot).applyQuaternion(q).add(pivot)
  view.lookAtPlane(tri3(back.applyQuaternion(q)), tri3(up.applyQuaternion(q)))
}

/** Ctrl+Shift+arrow: the model moves the way the arrow points (`sx` right, `sy` down), a tenth of the view's width or height. */
export function panView(view: ViewCamera, sx: number, sy: number): void {
  view.pan((sx * view.width) / 10, (sy * view.height) / 10)
}

/**
 * N in the 3D view (the desktop's `look_along`): look straight at a flat thing with this normal from the nearer side, or from
 * the other side when the view already is square to it; with no normal, look from the opposite side. The zoom stays; up snaps
 * to the nearest direction along or across `axes` (else X, Y, Z) as it lies on screen; `anchor` comes to the middle of the view.
 */
export function lookAlong(view: ViewCamera, normal: Vector3 | null, anchor: Vector3 | null = null, axes: readonly Vector3[] = []): void {
  const { up, back } = view.axes()
  let nb: Vector3
  if (normal === null) nb = back.clone().negate()
  else {
    nb = normal.clone().normalize()
    if (nb.dot(back) < 0) nb.negate()
    if (nb.dot(back) > 1 - 1e-6) nb.negate()
  }
  let u = up.clone().addScaledVector(nb, -up.dot(nb))
  if (u.length() < 1e-6) u = back.clone().addScaledVector(nb, -back.dot(nb))
  u.normalize()
  let best: { score: number; dir: Vector3 } | null = null
  const world = [new Vector3(1, 0, 0), new Vector3(0, 1, 0), new Vector3(0, 0, 1)]
  for (const axis of axes.length > 0 ? axes : world) {
    const a = axis.clone().addScaledVector(nb, -axis.dot(nb))
    if (a.length() < 1e-6) continue
    a.normalize()
    const across = new Vector3().crossVectors(nb, a)
    for (const cand of [a, a.clone().negate(), across, across.clone().negate()]) {
      const score = cand.dot(u)
      if (best === null || score > best.score + 1e-9) best = { score, dir: cand }
    }
  }
  if (best !== null) u = best.dir
  if (anchor !== null) view.target.copy(anchor).addScaledVector(nb, view.target.clone().sub(anchor).dot(nb))
  view.lookAtPlane(tri3(nb), tri3(u))
}

const reportItem = (i: SelectionItem): SelectionItemReport => ({ kind: i.kind, part: i.part, index: i.index, name: i.name, key: i.key })

declare global {
  interface Window {
    __freeshapeViewport?: ViewportReport
  }
}

/**
 * Owns the canvas, the renderer, the camera and the current scene of one viewport. Plain three.js: React only
 * mounts it (see Viewport.tsx). The camera is fitted to the first model it gets; later outputs keep the view.
 */
export class ViewportController implements NavHost {
  readonly view = new ViewCamera()
  private renderer: WebGLRenderer
  private readonly scene = new Scene()
  private built: BuiltScene | null = null
  private picker: PickIndex | null = null
  private revision: number | null = null
  private frames = 0
  private pending = 0
  private fitted = false
  private disposed = false
  private highlighted: string | null = null
  private emptyKind: string | null = null
  private previewNumber: number | null = null
  private tinted: { feature: string; parts: number } | null = null
  /** Half-size of the default planes of the last scene that had a model: an emptied studio keeps it. */
  private planeHalf: number | null = null
  readonly pointer: PointerModule
  private readonly manipulators: ManipulatorLayer
  private looks = 0
  private sketchMode: SketchMode | null = null
  /** The view as it was when the sketch opened: put back when it closes (the sketch squares and fits the view to itself). */
  private beforeSketch: CameraState | null = null
  private sketchStore: SketchStore | null = null
  /** The outputs drawn now (what N reads a face's or plane's normal from). */
  private outputs: StudioOutputs | null = null
  private readonly unsubscribe: () => void
  /** Desktop mode (I9.9): the box and the count badge drawn over the view, and the box's source for the selection. */
  private readonly overlay: MouseOverlay
  private readonly box: BoxSource
  /** The tab's visibility settings (P, Shift+P, Shift+I, Shift+T): what setOutputs draws by. */
  private vis: ViewState = DEFAULT_VIEW
  /** Whether the mate connectors were drawn the last time the scene was built (K, or a dialog's field that wants one). */
  private connectorsDrawn = true
  /** The pictures of the canvases (I12.5), cached per blob; null until the shell says where to fetch them. */
  private images: TextureCache | null = null
  private imageSource: ImageFetcher | null = null
  /** Desktop mode's right click (card I9.10): the shell opens a menu for what is under the pointer; coordinates are the window's. */
  private contextHandler: ((e: ViewContextEvent) => void) | null = null
  /** What setOutputs was last given, so a change of `vis` draws it again. */
  private lastShown: { shown: StudioOutputs; preview: PreviewLook | null } | null = null

  /** Throws when the browser cannot create a WebGL context. The viewport draws and picks for `selection`. */
  constructor(
    readonly canvas: HTMLCanvasElement,
    private readonly selection: SelectionStore,
  ) {
    this.renderer = new WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' })
    this.renderer.setClearColor(viewportBackground(), 1)
    this.pointer = new PointerModule(canvas, this)
    this.manipulators = new ManipulatorLayer(canvas.parentElement ?? document.body, this.view, () => this.requestRender())
    this.overlay = new MouseOverlay(canvas.parentElement ?? document.body)
    this.box = { hits: (b, filter) => this.boxHits(b, filter), draw: (b) => this.overlay.showBox(b) }
    this.pointer.setCursorHandler((p) => this.overlay.cursor(p))
    this.pointer.setHandleHandler(this.manipulators)
    wireSelection(this.pointer, selection, (x, y, pointer, filter) => this.pickAt(x, y, pointer, filter), this.box, this.contextSink)
    // the store tells us once per real change: an Aim move that lands on the same item never gets here
    this.unsubscribe = selection.subscribe(() => {
      this.syncConnectors()
      this.drawLook()
    })
    this.report()
  }

  /** Where the view's own position (x, y) is in the window. */
  private windowAt(x: number, y: number): { x: number; y: number } {
    const r = this.canvas.getBoundingClientRect()
    return { x: r.left + x, y: r.top + y }
  }

  private readonly contextSink: ContextSink = (hit, p) => this.contextHandler?.({ ...this.windowAt(p.x, p.y), hit, sketch: false })

  /** The shell's answer to a right click on the view (Desktop mode); null takes it away. */
  setContextHandler(handler: ((e: ViewContextEvent) => void) | null): void {
    this.contextHandler = handler
  }

  /** Desktop or Tablet mode (I9.9): in Desktop mode the mouse toggles on a click, draws a selection box and shows the count badge. */
  setDesktop(on: boolean): void {
    if (this.disposed) return
    this.pointer.setDesktop(on)
    this.overlay.setEnabled(on && this.sketchMode === null)
  }

  /**
   * The items a released selection box takes (I9.9), worked out here from what is drawn (see boxSelect.ts): the drawn
   * parts' faces, edges and vertices and the shown sketches' regions, as `filter` allows (else the desktop's defaults).
   */
  boxHits(b: { x0: number; y0: number; x1: number; y1: number }, filter?: SelectionStore['pickFilter']): SelectionItem[] {
    if (!this.outputs || !this.picker) return []
    return studioBoxHits(this.outputs, new Set(this.picker.partIds), this.view, b, filter ?? BOX_KINDS)
  }

  /**
   * Enter sketch mode for the sketch `store` has open (I7.7), or leave it with null. In sketch mode the view faces the
   * sketch plane, the solids are see-through, the sketch is drawn over them, and the pointer picks sketch items instead
   * of parts. Leaving puts the solids and the part selection's pointer wiring back.
   */
  setSketch(store: SketchStore | null): void {
    if (this.disposed) return
    // the same store again (an effect that re-ran for another reason) changes nothing: no new sketch mode, no camera move
    if (store !== null && store === this.sketchStore) return
    this.sketchStore = store
    if (store && this.sketchMode === null) this.beforeSketch = this.view.state()
    this.sketchMode?.dispose()
    this.sketchMode = null
    if (!store && this.beforeSketch) {
      this.view.restore(this.beforeSketch)
      this.beforeSketch = null
    }
    if (store) {
      this.sketchMode = new SketchMode(
        {
          view: this.view,
          pointer: this.pointer,
          overlayParent: this.canvas.parentElement ?? document.body,
          requestRender: () => this.requestRender(),
          modelBounds: () => (this.built?.hasModel ? this.built.bounds : null),
          drawBox: (b) => this.overlay.showBox(b),
          onContext: (x, y) => this.contextHandler?.({ ...this.windowAt(x, y), hit: null, sketch: true }),
        },
        store,
      )
    } else {
      wireSelection(this.pointer, this.selection, (x, y, pointer, filter) => this.pickAt(x, y, pointer, filter), this.box, this.contextSink)
      // the sketch's Drag handles gave up the hook: the drag arrows of a preview take it back
      this.pointer.setHandleHandler(this.manipulators)
    }
    // the desktop shows no count badge in a sketch
    this.overlay.setEnabled(this.pointer.desktop && this.sketchMode === null)
    if (this.built) this.seeThrough(this.built.root, store !== null)
    this.report()
    this.requestRender()
  }

  /** Solids see-through while a sketch is open, and the committed sketches' regions not drawn (the open sketch shows its own). */
  private seeThrough(root: Object3D, on: boolean): void {
    setSeeThrough(root, on)
    const regions = root.getObjectByName('sketch-regions')
    if (regions) regions.visible = !on
  }

  /** Draw the selection and the Aim: once per change, and the view is drawn again once. */
  private drawLook(): void {
    if (this.disposed) return
    const state = this.selection.getState()
    this.overlay.setCount(state.items.length)
    const first = this.built?.setLook(state.items, this.selection.getAimed()) ?? null
    this.highlighted = first
    this.looks++
    this.report()
    this.requestRender()
  }

  /** Match the canvas to its box (CSS pixels). */
  resize(width: number, height: number): void {
    if (this.disposed || width < 1 || height < 1) return
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.renderer.setSize(width, height, false)
    this.view.resize(width, height)
    if (this.fitted === false && this.built) this.fit()
    this.requestRender()
  }

  /** Replace the drawn model with this studio. The first model is fitted; later ones keep the camera. */
  setOutputs(given: StudioOutputs, preview: PreviewLook | null = null): void {
    if (this.disposed) return
    this.lastShown = { shown: given, preview }
    this.connectorsDrawn = this.connectorsOn()
    const shown = shapeStudio(given, this.vis)
    let outputs = shown
    this.previewNumber = preview?.number ?? null
    this.tinted = null
    const ghost = preview?.ghost ?? null
    if (preview && !ghost) {
      const t = tintFeature(shown, preview.feature)
      outputs = t.outputs
      this.tinted = { feature: preview.feature, parts: t.tinted }
    }
    // what is selected survives the rebuild when its part and Element name are still there (see SelectionStore.reconcile)
    this.selection.reconcile(outputs)
    // the drag arrows belong to the preview: while a dialog is open that is the ghost over the base (card F3)
    this.manipulators.set((ghost ?? shown).manipulators)
    if (this.built) {
      this.scene.remove(this.built.root)
      this.built.dispose()
    }
    this.built = buildScene(outputs, { keepPlaneHalf: this.planeHalf, ghost, hiddenPlanes: this.vis.hiddenPlanes, transparent: new Set(this.vis.transparent), connectors: this.connectorsDrawn, images: this.images })
    if (preview && ghost) this.tinted = { feature: preview.feature, parts: this.built.stats.ghostParts }
    this.scene.add(this.built.root)
    if (this.sketchMode) this.seeThrough(this.built.root, true)
    this.picker = new PickIndex(outputs, this.built)
    this.highlighted = this.built.setLook(this.selection.getState().items, this.selection.getAimed())
    if (this.built.hasModel) this.planeHalf = this.built.stats.planeHalf
    this.revision = outputs.revision
    this.outputs = outputs
    this.emptyKind = null
    if (!this.fitted) this.fit()
    this.requestRender()
  }

  /**
   * Where the canvases' pictures come from (card I12.5): `GET .../images/<blob>` for this tab, as the shell's function. A new
   * source (another tab) drops the textures of the old one and draws again; the same one changes nothing.
   */
  setImages(source: ImageFetcher | null): void {
    if (this.disposed || source === this.imageSource) return
    this.imageSource = source
    this.images?.dispose()
    this.images = source === null ? null : new TextureCache(source, () => this.picturesArrived())
    if (this.lastShown) this.setOutputs(this.lastShown.shown, this.lastShown.preview)
  }

  /** A picture was fetched (or failed): put it on its canvas and draw. */
  private picturesArrived(): void {
    if (this.disposed) return
    this.built?.refreshCanvases()
    this.report()
    this.requestRender()
  }

  /** K shows the mate connectors; so does a dialog's selection field that wants one, as the desktop shows what a dialog picks (card I10.3). */
  private connectorsOn(): boolean {
    return this.vis.connectors || (this.selection.pickFilter !== undefined && [...this.selection.pickFilter].includes('connector'))
  }

  /** A field that wants a connector became active or ended: draw the scene again when that changes whether the triads show. */
  private syncConnectors(): void {
    if (this.disposed || !this.lastShown || this.connectorsOn() === this.connectorsDrawn) return
    this.setOutputs(this.lastShown.shown, this.lastShown.preview)
  }

  /** The tab's visibility settings changed (card I9.8): draw the model again by them. */
  setVisibility(vis: ViewState): void {
    if (this.disposed || vis === this.vis) return
    this.vis = vis
    if (this.lastShown) this.setOutputs(this.lastShown.shown, this.lastShown.preview)
  }

  /**
   * Draw nothing: the tab on screen is not a Part Studio (`kind` says which), or its outputs have not arrived. No part,
   * plane or highlight of another tab stays. The next outputs are fitted afresh, and the planes' size is forgotten.
   */
  clear(kind: string | null = null): void {
    if (this.disposed) return
    if (this.built) {
      this.scene.remove(this.built.root)
      this.built.dispose()
      this.built = null
    }
    this.picker = null
    this.lastShown = null
    this.highlighted = null
    this.planeHalf = null
    this.revision = null
    this.outputs = null
    this.fitted = false
    this.emptyKind = kind
    this.previewNumber = null
    this.tinted = null
    this.manipulators.clear()
    this.requestRender()
  }

  fit(): void {
    if (!this.built) return
    this.view.fit(this.built.fitBounds)
    // an empty studio is fitted to the default box, but the first model that is drawn is fitted too
    this.fitted = this.built.hasModel
    this.requestRender()
  }

  /** Look along a standard view and fit the model. */
  setStandardView(name: StandardView): void {
    // a standard view cannot keep a sketch square to its plane: the sketch goes to its 3D mode
    this.sketchMode?.leavePlanar()
    this.view.setBack(STANDARD_VIEWS[name])
    this.fit()
  }

  /** Look straight at the nearest face direction, or at the given outward normal, and fit the model. */
  normalTo(normal?: readonly [number, number, number]): void {
    if (this.sketchMode) {
      // in a sketch the cube's Normal to squares the view to the sketch plane
      this.sketchMode.normalTo()
      return
    }
    this.view.normalTo(normal)
    this.fit()
  }

  /** An arrow key: turn the view one step about the model's centre. Not in a sketch, where the arrows are the sketch's. */
  turnStep(h: number, v: number): boolean {
    if (this.sketchMode || !this.built) return false
    turnView(this.view, this.modelCentre(), h, v)
    this.requestRender()
    return true
  }

  /** Ctrl+Shift+arrow: pan a tenth of the view. Not in a sketch. */
  panStep(sx: number, sy: number): boolean {
    if (this.sketchMode || !this.built) return false
    panView(this.view, sx, sy)
    this.requestRender()
    return true
  }

  /**
   * N (the desktop's `_normal_key`): in a sketch, square the view to its plane. Otherwise look straight at `item` (the face,
   * plane or region under the mouse, or else the selected one) from the nearer side, the other side when pressed again;
   * with nothing, look from the opposite side. A curved face: nothing.
   */
  normalKey(item: SelectionItem | null): boolean {
    if (this.sketchMode) {
      this.sketchMode.normalTo()
      return true
    }
    const flat = item === null ? undefined : this.flatOf(item)
    if (flat === null) return true
    lookAlong(this.view, flat?.normal ?? null, flat?.anchor ?? null, flat?.axes ?? [])
    this.requestRender()
    return true
  }

  /** The normal, a point and the in-plane axes of a flat face, plane or region; null for a curved face; undefined for anything else. */
  private flatOf(item: SelectionItem): { normal: Vector3; anchor: Vector3; axes: Vector3[] } | null | undefined {
    const o = this.outputs
    if (o === null) return undefined
    const v = (t: readonly number[]) => new Vector3(t[0], t[1], t[2])
    if (item.kind === 'plane' && item.key !== null) {
      const p = DEFAULT_PLANES.find((d) => d.name === item.key) ?? o.planes[item.key]
      return p ? { normal: v(p.n), anchor: v(p.o), axes: [v(p.x)] } : undefined
    }
    if (item.kind === 'region') {
      const s = o.sketches.find((k) => k.id === item.part)
      const r = s?.regions[item.index]
      return s && r ? { normal: v(s.plane.normal), anchor: v(r.c), axes: [v(s.plane.xdir)] } : undefined
    }
    if (item.kind !== 'face') return undefined
    const mesh = (o.parts.find((p) => p.id === item.part) ?? o.context.find((p) => p.id === item.part))?.mesh
    const range = mesh?.faceRanges[item.index]
    if (!mesh || !range) return undefined
    const corner = (k: number) => new Vector3(mesh.positions[3 * k], mesh.positions[3 * k + 1], mesh.positions[3 * k + 2])
    let normal: Vector3 | null = null
    const sum = new Vector3()
    let n = 0
    for (let t = range[0]; t < range[1]; t++) {
      const [a, b, c] = [corner(mesh.indices[3 * t]!), corner(mesh.indices[3 * t + 1]!), corner(mesh.indices[3 * t + 2]!)]
      sum.add(a).add(b).add(c)
      n += 3
      const tn = new Vector3().crossVectors(b.clone().sub(a), c.clone().sub(a))
      if (tn.length() < 1e-12) continue
      tn.normalize()
      if (normal === null) normal = tn
      else if (Math.abs(normal.dot(tn)) < 1 - 1e-4) return null
    }
    return normal === null ? null : { normal, anchor: sum.divideScalar(n), axes: [] }
  }

  modelCentre(): Vector3 {
    const b = this.built?.bounds
    return b ? new Vector3((b.lo[0] + b.hi[0]) / 2, (b.lo[1] + b.hi[1]) / 2, (b.lo[2] + b.hi[2]) / 2) : new Vector3()
  }

  /** The model point under a view position (CSS pixels from the top left): the nearest hit of a ray along the view. */
  pickPoint(x: number, y: number): Vector3 | null {
    return this.built ? rayPoint(this.built.root, viewRay(this.view, x, y)) : null
  }

  /**
   * What is under a view position for this pointer kind (see pick.ts), or null. Nothing is drawn by it and nothing is
   * kept: the pointer wiring (card I4.2) calls it, and so does the test hook `pickAt`.
   */
  pickAt(x: number, y: number, pointer: PickPointer, filter?: PickFilter): PickHit | null {
    return this.picker ? this.picker.pick(this.view, x, y, pointer, filter) : null
  }

  private canvasScreens(): CanvasScreen[] {
    const pictured = new Set(this.built?.canvasPictures() ?? [])
    const blobOf = (id: string): string => this.outputs?.canvases.find((o) => o.id === id)?.blob ?? ''
    return (this.built?.canvases ?? []).map((c) => ({ id: c.id, ...this.view.project([c.origin.x, c.origin.y, c.origin.z]), inside: this.view.project(c.origin.clone().addScaledVector(c.x, c.width / 4).addScaledVector(c.y, c.height / 4).toArray()), pictured: pictured.has(c.id), error: this.images?.failure(blobOf(c.id)) ?? null }))
  }

  private connectorScreens(): ConnectorScreen[] {
    return (this.built?.triads ?? []).filter((t) => t.kind === 'connector').map((t) => ({ id: t.id, ...this.view.project(t.o) }))
  }

  requestRender(): void {
    if (this.pending || this.disposed) return
    this.pending = requestAnimationFrame(() => {
      this.pending = 0
      this.render()
    })
  }

  render(): void {
    if (this.disposed) return
    this.view.apply()
    this.sketchMode?.update()
    this.built?.update(this.view.width, this.view.height, this.view.scale)
    this.manipulators.update()
    this.renderer.render(this.scene, this.view.camera)
    this.frames++
    this.report()
  }

  private report(): void {
    window.__freeshapeViewport = {
      ...(this.built?.stats ?? EMPTY_STATS),
      revision: this.revision,
      camera: this.view.state(),
      frames: this.frames,
      highlightedPart: this.highlighted,
      emptyKind: this.emptyKind,
      preview: this.previewNumber,
      tintedFeature: this.tinted?.feature ?? null,
      tintedParts: this.tinted?.parts ?? 0,
      selection: {
        count: this.selection.getState().items.length,
        items: this.selection.getState().items.map(reportItem),
        multi: this.selection.getState().multi,
        aimed: this.selection.getAimed() ? reportItem(this.selection.getAimed()!) : null,
        looks: this.looks,
      },
      sketch: this.sketchMode?.report() ?? null,
      pickAt: (x, y, pointer, filter) => this.pickAt(x, y, pointer, filter),
      connectorScreens: () => this.connectorScreens(),
      canvasScreens: () => this.canvasScreens(),
      boxHits: (x0, y0, x1, y1) => this.boxHits({ x0, y0, x1, y1 }, this.selection.pickFilter).map(reportItem),
    }
  }

  dispose(): void {
    this.disposed = true
    this.sketchMode?.dispose()
    this.sketchMode = null
    this.unsubscribe()
    this.pointer.dispose()
    this.manipulators.dispose()
    this.overlay.dispose()
    if (this.pending) cancelAnimationFrame(this.pending)
    if (this.built) {
      this.scene.remove(this.built.root)
      this.built.dispose()
      this.built = null
    }
    this.images?.dispose()
    this.renderer.dispose()
    delete window.__freeshapeViewport
  }
}
