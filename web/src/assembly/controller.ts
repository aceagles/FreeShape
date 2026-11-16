import { Matrix4, Scene, Vector3, WebGLRenderer } from 'three'
import { PointerModule } from '../input/pointer'
import type { NavHost } from '../input/pointer'
import { wireSelection } from '../selection/aimCommit'
import type { SelectionStore } from '../selection/store'
import type { AssemblyOutputs } from '../wire'
import { ViewCamera, STANDARD_VIEWS } from '../viewport/camera'
import type { CameraState, StandardView } from '../viewport/camera'
import { viewportBackground } from '../viewport/colors'
import { rayPoint, viewRay } from '../viewport/pick'
import { panView, turnView } from '../viewport/controller'
import type { PickFilter, PickPointer } from '../viewport/pick'
import type { ElementPick } from '../viewport/pick'
import { AssemblyPickIndex } from './pick'
import { buildAssemblyScene, EMPTY_ASSEMBLY_STATS } from './scene'
import type { AssemblyScene, AssemblyStats } from './scene'
import { reconcileAssembly } from './select'
import { GizmoController, livePose } from './gizmo'
import type { DragSender, GizmoShape, LiveDrag } from './gizmo'

/** What `window.__freeshapeAssembly` reports, for tests that look at the page from outside. */
export interface AssemblyReport extends AssemblyStats {
  /** Revision of the outputs on screen; null before the first. */
  revision: number | null
  camera: CameraState
  /** Frames drawn so far. */
  frames: number
  /** Test hook: what a pointer of this kind would pick at a view position (CSS pixels from the top left), null for nothing. */
  pickAt(x: number, y: number, pointer: PickPointer, filter?: PickFilter): ElementPick | null
  /** Screen position (CSS pixels from the view's top left) of the middle of a drawn instance's bounding box, or null. */
  screenOf(instance: string): { x: number; y: number } | null
  /** The move gizmo as drawn (positions in CSS pixels from the view's top left), or null when nothing movable is selected. */
  gizmo: GizmoShape | null
  /** Each instance's placement (4 x 4, row-major) as drawn: the outputs on screen, or where a drag has put it (F17). */
  placements: Record<string, number[][]>
}

declare global {
  interface Window {
    __freeshapeAssembly?: AssemblyReport
  }
}

/** A three.js matrix as 4 x 4 row-major rows. */
function rowsOf(m: Matrix4): number[][] {
  const e = m.elements
  return [0, 1, 2, 3].map((i) => [e[i]!, e[4 + i]!, e[8 + i]!, e[12 + i]!])
}

/**
 * Owns the canvas, renderer, camera and scene of an Assembly view. The same plain three.js and pointer module as the Part
 * Studio's ViewportController, over `buildAssemblyScene`: one geometry per part, one object per drawn instance.
 */
export class AssemblyController implements NavHost {
  readonly view = new ViewCamera()
  private readonly renderer: WebGLRenderer
  private readonly scene = new Scene()
  private built: AssemblyScene | null = null
  private picker: AssemblyPickIndex | null = null
  private revision: number | null = null
  private latest: AssemblyOutputs | null = null
  private frames = 0
  private pending = 0
  private fitted = false
  private disposed = false
  /**
   * A drag drawn ahead of the outputs (F17). `solved`: the placements move_to replies brought, newer than the outputs on
   * screen, kept until outputs arrive after the drag is done. `live`: the dragged instance, drawn at `motion` applied to
   * `base` (the newest solved placement of it) undoing `answered` (the motion that solve answered); before the first
   * reply that is the motion applied to where it began, so it follows the pointer at once.
   */
  private solved = new Map<string, number[][]>()
  private live: { instance: string; base: number[][]; answered: number[][]; motion: number[][] } | null = null
  private dragging = false
  private sender: DragSender | null = null
  readonly pointer: PointerModule
  readonly gizmo: GizmoController
  private readonly unsubscribe: () => void

  constructor(
    readonly canvas: HTMLCanvasElement,
    private readonly selection: SelectionStore,
    onGizmo: (shape: GizmoShape | null) => void = () => {},
  ) {
    this.renderer = new WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' })
    this.renderer.setClearColor(viewportBackground(), 1)
    this.pointer = new PointerModule(canvas, this)
    // the DOM overlay and the test hook change together: report as soon as the shape does, not on the next frame
    const live: LiveDrag = {
      pose: (id) => this.pose(id),
      begin: (id) => this.liveBegin(id),
      motion: (id, T) => this.liveMotion(id, T),
      done: () => this.liveDone(),
    }
    this.gizmo = new GizmoController(
      this.view,
      selection,
      (shape) => {
        onGizmo(shape)
        this.report()
      },
      live,
    )
    this.pointer.setHandleHandler(this.gizmo)
    wireSelection(this.pointer, selection, (x, y, pointer, filter) => this.pickAt(x, y, pointer, filter))
    this.unsubscribe = selection.subscribe(() => {
      this.gizmo.refresh()
      this.drawLook()
    })
    this.report()
  }

  private drawLook(): void {
    if (this.disposed || !this.built) return
    this.applyLook()
    this.requestRender()
  }

  private applyLook(): void {
    const items = this.selection.getState().items.flatMap((i) => (i.instance === undefined ? [] : [{ kind: i.kind, part: i.instance, index: i.index, key: null }]))
    const aimed = this.selection.getAimed()
    this.built?.setLook(items, aimed?.instance === undefined ? null : { kind: aimed.kind, part: aimed.instance, index: aimed.index, key: null })
  }

  /** What sends a drag to the server; null (or a disabled one) shows no gizmo. */
  setDragSender(sender: DragSender | null): void {
    this.sender?.listen(null)
    this.sender = sender
    sender?.listen((placements, T) => this.solvedStep(placements, T))
    this.gizmo.setSender(sender)
  }

  /** Where instance `id` is drawn when that is not where the outputs on screen put it; null otherwise. */
  private pose(id: string): number[][] | null {
    const l = this.live
    if (l && l.instance === id) return livePose(l.motion, l.answered, l.base)
    return this.solved.get(id) ?? null
  }

  private outputsT(id: string): number[][] | null {
    const T = this.latest?.instances.find((i) => i.id === id)?.T
    return T ? T.map((r) => [...r]) : null
  }

  private liveBegin(id: string): void {
    const base = this.pose(id) ?? this.outputsT(id)
    if (!base) return
    const I = rowsOf(new Matrix4())
    this.dragging = true
    this.live = { instance: id, base, answered: I, motion: I }
  }

  private liveMotion(id: string, T: number[][]): void {
    if (!this.live || this.live.instance !== id) return
    this.live.motion = T
    this.placeOverrides()
  }

  /** A move_to answered: the instances its solve moved go where it put them; the dragged one keeps following the pointer. */
  private solvedStep(placements: Record<string, number[][]>, T: number[][]): void {
    if (this.disposed || !this.dragging) return
    for (const [id, P] of Object.entries(placements)) this.solved.set(id, P)
    const l = this.live
    if (l) {
      const mine = placements[l.instance]
      if (mine) l.base = mine
      l.answered = T
    }
    this.placeOverrides()
  }

  /** The server has answered the whole drag: draw what it solved until the next outputs arrive. */
  private liveDone(): void {
    if (this.disposed) return
    this.live = null
    this.dragging = false
    this.placeOverrides()
    if (this.latest && this.built) this.picker = new AssemblyPickIndex(this.latest, this.built)
  }

  private placeOverrides(): void {
    if (this.disposed || !this.built) return
    for (const [id, P] of this.solved) this.built.place(id, P)
    if (this.live) {
      const P = this.pose(this.live.instance)
      if (P) this.built.place(this.live.instance, P)
    }
    this.gizmo.refresh()
    this.report()
    this.requestRender()
  }

  resize(width: number, height: number): void {
    if (this.disposed || width < 1 || height < 1) return
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.renderer.setSize(width, height, false)
    this.view.resize(width, height)
    if (!this.fitted && this.built) this.fit()
    this.requestRender()
  }

  /** Replace the drawn model with this Assembly. The first model is fitted; later ones keep the camera. */
  setOutputs(outputs: AssemblyOutputs): void {
    if (this.disposed) return
    reconcileAssembly(this.selection, outputs)
    this.drop()
    // outputs that arrive once a drag is done are newer than anything its replies said
    if (!this.dragging) this.solved.clear()
    this.latest = outputs
    this.gizmo.setOutputs(outputs)
    this.built = buildAssemblyScene(outputs)
    this.scene.add(this.built.root)
    if (this.dragging) this.placeOverrides()
    this.picker = new AssemblyPickIndex(outputs, this.built)
    this.applyLook()
    this.revision = outputs.revision
    if (!this.fitted) this.fit()
    this.requestRender()
  }

  /** Draw nothing (the outputs have not arrived, or the tab changed). The next outputs are fitted afresh. */
  clear(): void {
    if (this.disposed) return
    this.drop()
    this.gizmo.setOutputs(null)
    this.latest = null
    this.revision = null
    this.solved.clear()
    this.live = null
    this.dragging = false
    this.fitted = false
    this.requestRender()
  }

  private drop(): void {
    if (!this.built) return
    this.scene.remove(this.built.root)
    this.built.dispose()
    this.built = null
    this.picker = null
  }

  fit(): void {
    if (!this.built) return
    this.view.fit(this.built.bounds)
    this.fitted = this.built.hasModel
    this.requestRender()
  }

  setStandardView(name: StandardView): void {
    this.view.setBack(STANDARD_VIEWS[name])
    this.fit()
  }

  normalTo(normal?: readonly [number, number, number]): void {
    this.view.normalTo(normal)
    this.fit()
  }

  /** An arrow key: turn the view one step about the model's centre (as in a Part Studio). */
  turnStep(h: number, v: number): boolean {
    if (!this.built) return false
    turnView(this.view, this.modelCentre(), h, v)
    this.requestRender()
    return true
  }

  /** Ctrl+Shift+arrow: pan a tenth of the view. */
  panStep(sx: number, sy: number): boolean {
    if (!this.built) return false
    panView(this.view, sx, sy)
    this.requestRender()
    return true
  }

  modelCentre(): Vector3 {
    const b = this.built?.bounds
    return b ? new Vector3((b.lo[0] + b.hi[0]) / 2, (b.lo[1] + b.hi[1]) / 2, (b.lo[2] + b.hi[2]) / 2) : new Vector3()
  }

  pickPoint(x: number, y: number): Vector3 | null {
    return this.built ? rayPoint(this.built.root, viewRay(this.view, x, y)) : null
  }

  pickAt(x: number, y: number, pointer: PickPointer, filter?: PickFilter): ElementPick | null {
    return this.picker ? this.picker.pick(this.view, x, y, pointer, filter) : null
  }

  private screenOf(instance: string): { x: number; y: number } | null {
    const o = this.built?.objects.get(instance)
    const g = o?.geometry
    if (!o || !g) return null
    g.computeBoundingBox()
    const c = g.boundingBox!.getCenter(new Vector3()).applyMatrix4(o.matrix)
    const { right, up } = this.view.axes()
    const d = c.sub(this.view.target)
    return { x: this.view.width / 2 + d.dot(right) / this.view.scale, y: this.view.height / 2 - d.dot(up) / this.view.scale }
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
    this.gizmo.refresh()
    this.built?.update(this.view.width, this.view.height)
    this.renderer.render(this.scene, this.view.camera)
    this.frames++
    this.report()
  }

  private report(): void {
    window.__freeshapeAssembly = {
      ...(this.built?.stats ?? EMPTY_ASSEMBLY_STATS),
      revision: this.revision,
      camera: this.view.state(),
      frames: this.frames,
      pickAt: (x, y, pointer, filter) => this.pickAt(x, y, pointer, filter),
      screenOf: (id) => this.screenOf(id),
      gizmo: this.gizmo.current(),
      placements: Object.fromEntries((this.latest?.instances ?? []).map((i) => [i.id, (this.pose(i.id) ?? i.T).map((r) => [...r])])),
    }
  }

  dispose(): void {
    this.disposed = true
    this.sender?.listen(null)
    this.unsubscribe()
    this.pointer.setHandleHandler(null)
    this.pointer.dispose()
    if (this.pending) cancelAnimationFrame(this.pending)
    this.drop()
    this.renderer.dispose()
    delete window.__freeshapeAssembly
  }
}
