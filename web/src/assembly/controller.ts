import { Scene, Vector3, WebGLRenderer } from 'three'
import { PointerModule } from '../input/pointer'
import type { NavHost } from '../input/pointer'
import { wireSelection } from '../selection/aimCommit'
import type { SelectionStore } from '../selection/store'
import type { AssemblyOutputs } from '../wire'
import { ViewCamera, STANDARD_VIEWS } from '../viewport/camera'
import type { CameraState, StandardView } from '../viewport/camera'
import { viewportBackground } from '../viewport/colors'
import { rayPoint, viewRay } from '../viewport/pick'
import type { PickFilter, PickPointer } from '../viewport/pick'
import type { ElementPick } from '../viewport/pick'
import { AssemblyPickIndex } from './pick'
import { buildAssemblyScene, EMPTY_ASSEMBLY_STATS } from './scene'
import type { AssemblyScene, AssemblyStats } from './scene'
import { reconcileAssembly } from './select'
import { GizmoController } from './gizmo'
import type { DragSender, GizmoShape } from './gizmo'

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
  /** Each instance's placement (4 x 4, row-major) in the outputs on screen. */
  placements: Record<string, number[][]>
}

declare global {
  interface Window {
    __freeshapeAssembly?: AssemblyReport
  }
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
    this.gizmo = new GizmoController(this.view, selection, onGizmo)
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
    this.gizmo.setSender(sender)
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
    this.gizmo.setOutputs(outputs)
    this.built = buildAssemblyScene(outputs)
    this.scene.add(this.built.root)
    this.picker = new AssemblyPickIndex(outputs, this.built)
    this.applyLook()
    this.revision = outputs.revision
    this.latest = outputs
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
      placements: Object.fromEntries((this.latest?.instances ?? []).map((i) => [i.id, i.T.map((r) => [...r])])),
    }
  }

  dispose(): void {
    this.disposed = true
    this.unsubscribe()
    this.pointer.setHandleHandler(null)
    this.pointer.dispose()
    if (this.pending) cancelAnimationFrame(this.pending)
    this.drop()
    this.renderer.dispose()
    delete window.__freeshapeAssembly
  }
}
