import { Scene, WebGLRenderer } from 'three'
import type { StudioOutputs } from '../wire'
import { viewportBackground } from './colors'
import { ViewCamera } from './camera'
import type { CameraState } from './camera'
import { buildScene } from './scene'
import type { BuiltScene, SceneStats } from './scene'

/** What `window.__freeshapeViewport` reports, for tests that look at the page from outside. */
export interface ViewportReport extends SceneStats {
  /** Revision of the outputs on screen; null before the first. */
  revision: number | null
  camera: CameraState
  /** Frames drawn so far. */
  frames: number
}

declare global {
  interface Window {
    __freeshapeViewport?: ViewportReport
  }
}

const EMPTY: SceneStats = { parts: 0, context: 0, faces: 0, edges: 0, triangles: 0, planes: 0, axes: 0 }

/**
 * Owns the canvas, the renderer, the camera and the current scene of one viewport. Plain three.js: React only
 * mounts it (see Viewport.tsx). The camera is fitted to the first model it gets; later outputs keep the view.
 */
export class ViewportController {
  readonly view = new ViewCamera()
  private renderer: WebGLRenderer
  private readonly scene = new Scene()
  private built: BuiltScene | null = null
  private revision: number | null = null
  private frames = 0
  private pending = 0
  private fitted = false
  private disposed = false

  /** Throws when the browser cannot create a WebGL context. */
  constructor(readonly canvas: HTMLCanvasElement) {
    this.renderer = new WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' })
    this.renderer.setClearColor(viewportBackground(), 1)
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
    this.built = buildScene(outputs)
    this.scene.add(this.built.root)
    this.revision = outputs.revision
    if (!this.fitted) this.fit()
    this.requestRender()
  }

  fit(): void {
    if (!this.built) return
    this.view.fit(this.built.bounds)
    this.fitted = true
    this.requestRender()
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
      ...(this.built?.stats ?? EMPTY),
      revision: this.revision,
      camera: this.view.state(),
      frames: this.frames,
    }
  }

  dispose(): void {
    this.disposed = true
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
