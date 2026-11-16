import { Scene, Vector3, WebGLRenderer } from 'three'
import { PointerModule } from '../input/pointer'
import type { NavHost } from '../input/pointer'
import type { StudioOutputs } from '../wire'
import { viewportBackground } from './colors'
import { ViewCamera } from './camera'
import type { CameraState, StandardView } from './camera'
import { STANDARD_VIEWS } from './camera'
import { PickIndex, rayPoint, viewRay } from './pick'
import type { PickFilter, PickHit, PickKind, PickPointer } from './pick'
import { buildScene, EMPTY_STATS } from './scene'
import type { BuiltScene, SceneStats } from './scene'

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
  /** Test hook: what a pointer of this kind would pick at a view position (CSS pixels from the top left), null for nothing. */
  pickAt(x: number, y: number, pointer: PickPointer, filter?: readonly PickKind[]): PickHit | null
}

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
  private selectedPart: string | null = null
  private highlighted: string | null = null
  private emptyKind: string | null = null
  /** Half-size of the default planes of the last scene that had a model: an emptied studio keeps it. */
  private planeHalf: number | null = null
  readonly pointer: PointerModule

  /** Throws when the browser cannot create a WebGL context. */
  constructor(readonly canvas: HTMLCanvasElement) {
    this.renderer = new WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' })
    this.renderer.setClearColor(viewportBackground(), 1)
    this.pointer = new PointerModule(canvas, this)
    this.report()
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
  setOutputs(outputs: StudioOutputs): void {
    if (this.disposed) return
    if (this.built) {
      this.scene.remove(this.built.root)
      this.built.dispose()
    }
    this.built = buildScene(outputs, { keepPlaneHalf: this.planeHalf, selectedPart: this.selectedPart })
    this.scene.add(this.built.root)
    this.picker = new PickIndex(outputs, this.built)
    this.highlighted = this.built.stats.visiblePartIds.includes(this.selectedPart ?? '') ? this.selectedPart : null
    if (this.built.hasModel) this.planeHalf = this.built.stats.planeHalf
    this.revision = outputs.revision
    this.emptyKind = null
    if (!this.fitted) this.fit()
    this.requestRender()
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
    this.highlighted = null
    this.planeHalf = null
    this.revision = null
    this.fitted = false
    this.emptyKind = kind
    this.requestRender()
  }

  /** Draw this part highlighted (the Parts list's selection), or none. */
  setSelectedPart(id: string | null): void {
    if (this.disposed || id === this.selectedPart) return
    this.selectedPart = id
    this.highlighted = this.built?.select(id) ? id : null
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
    this.view.setBack(STANDARD_VIEWS[name])
    this.fit()
  }

  /** Look straight at the nearest face direction, or at the given outward normal, and fit the model. */
  normalTo(normal?: readonly [number, number, number]): void {
    this.view.normalTo(normal)
    this.fit()
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
    this.built?.update(this.view.width, this.view.height, this.view.scale)
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
      pickAt: (x, y, pointer, filter) => this.pickAt(x, y, pointer, filter),
    }
  }

  dispose(): void {
    this.disposed = true
    this.pointer.dispose()
    if (this.pending) cancelAnimationFrame(this.pending)
    if (this.built) {
      this.scene.remove(this.built.root)
      this.built.dispose()
      this.built = null
    }
    this.renderer.dispose()
    delete window.__freeshapeViewport
  }
}
