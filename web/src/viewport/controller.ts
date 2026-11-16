import { Scene, Vector3, WebGLRenderer } from 'three'
import type { Object3D } from 'three'
import { ManipulatorLayer } from '../dialog/manipulator'
import { PointerModule } from '../input/pointer'
import type { NavHost } from '../input/pointer'
import { wireSelection } from '../selection/aimCommit'
import type { SelectionItem, SelectionStore } from '../selection/store'
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
import { buildScene, EMPTY_STATS } from './scene'
import type { BuiltScene, SceneStats } from './scene'
import { setSeeThrough } from './seeThrough'

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
  private readonly unsubscribe: () => void

  /** Throws when the browser cannot create a WebGL context. The viewport draws and picks for `selection`. */
  constructor(
    readonly canvas: HTMLCanvasElement,
    private readonly selection: SelectionStore,
  ) {
    this.renderer = new WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' })
    this.renderer.setClearColor(viewportBackground(), 1)
    this.pointer = new PointerModule(canvas, this)
    this.manipulators = new ManipulatorLayer(canvas.parentElement ?? document.body, this.view, () => this.requestRender())
    this.pointer.setHandleHandler(this.manipulators)
    wireSelection(this.pointer, selection, (x, y, pointer, filter) => this.pickAt(x, y, pointer, filter))
    // the store tells us once per real change: an Aim move that lands on the same item never gets here
    this.unsubscribe = selection.subscribe(() => this.drawLook())
    this.report()
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
        },
        store,
      )
    } else {
      wireSelection(this.pointer, this.selection, (x, y, pointer, filter) => this.pickAt(x, y, pointer, filter))
      // the sketch's Drag handles gave up the hook: the drag arrows of a preview take it back
      this.pointer.setHandleHandler(this.manipulators)
    }
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
  setOutputs(shown: StudioOutputs, preview: PreviewLook | null = null): void {
    if (this.disposed) return
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
    this.built = buildScene(outputs, { keepPlaneHalf: this.planeHalf, ghost })
    if (preview && ghost) this.tinted = { feature: preview.feature, parts: this.built.stats.ghostParts }
    this.scene.add(this.built.root)
    if (this.sketchMode) this.seeThrough(this.built.root, true)
    this.picker = new PickIndex(outputs, this.built)
    this.highlighted = this.built.setLook(this.selection.getState().items, this.selection.getAimed())
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
    }
  }

  dispose(): void {
    this.disposed = true
    this.sketchMode?.dispose()
    this.sketchMode = null
    this.unsubscribe()
    this.pointer.dispose()
    this.manipulators.dispose()
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
