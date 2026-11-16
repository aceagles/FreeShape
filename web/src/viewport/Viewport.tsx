import { useEffect, useRef, useState } from 'react'
import { SelectionStore } from '../selection/store'
import type { SketchStore } from '../sketch/store'
import type { StudioOutputs } from '../wire'
import { ViewportController } from './controller'
import type { PreviewLook } from './controller'
import { ViewCube } from './ViewCube'
import { notDrawnText } from './notDrawn'
import './viewport.css'

/**
 * The 3D view of a Part Studio. Fills its parent; draws `outputs` and redraws when they change (a new revision
 * arrives as new outputs), with `selection` drawn on it. The controller's pointer module (src/input) navigates; the view cube is the control for standard views.
 */
export function Viewport({
  outputs,
  selection,
  tabKind = 'studio',
  preview = null,
  sketch = null,
}: {
  outputs: StudioOutputs | null
  selection?: SelectionStore
  tabKind?: string | null
  preview?: PreviewLook | null
  /** The open sketch (I7.7), drawn over the model while it is open in this view. */
  sketch?: SketchStore | null
}) {
  // a viewport mounted on its own keeps its own selection; the shell passes the one it shares with the chip and the Parts list
  const [own] = useState(() => new SelectionStore())
  const store = selection ?? own
  // a tab that is not a Part Studio draws nothing of its own and none of another tab's
  const message = tabKind !== null && tabKind !== 'studio' ? notDrawnText(tabKind) : null
  const host = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const controller = useRef<ViewportController | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const previewNumber = preview?.number ?? null
  const previewFeature = preview?.feature ?? null
  const previewGhost = preview?.ghost ?? null

  useEffect(() => {
    const el = host.current
    const cv = canvas.current
    if (!el || !cv) return
    let c: ViewportController
    try {
      c = new ViewportController(cv, store)
    } catch (e) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFailed(e instanceof Error ? e.message : 'WebGL is not available')
      return
    }
    controller.current = c
    const size = () => c.resize(el.clientWidth, el.clientHeight)
    size()
    const ro = new ResizeObserver(size)
    ro.observe(el)
    return () => {
      ro.disconnect()
      c.dispose()
      controller.current = null
    }
  }, [store])

  useEffect(() => {
    if (outputs) controller.current?.setOutputs(outputs, previewNumber === null ? null : { number: previewNumber, feature: previewFeature!, ghost: previewGhost })
    else controller.current?.clear(message === null ? null : tabKind)
  }, [outputs, previewNumber, previewFeature, previewGhost, message, tabKind, failed])

  // sketch mode follows the store that has the sketch open (after the controller exists: the effect above runs first)
  useEffect(() => {
    controller.current?.setSketch(sketch)
    return () => controller.current?.setSketch(null)
  }, [sketch, store, failed])

  return (
    <div ref={host} className="viewport" data-testid="viewport">
      <canvas ref={canvas} className="viewport-canvas" />
      {message === null && (
        <div className="viewport-cube-slot">
          <ViewCube onView={(n) => controller.current?.setStandardView(n)} onNormalTo={() => controller.current?.normalTo()} />
        </div>
      )}
      {message !== null && (
        <p className="viewport-empty" data-testid="viewport-empty">
          {message}
        </p>
      )}
      {failed && <p className="viewport-error">The 3D view could not start: {failed}</p>}
    </div>
  )
}
