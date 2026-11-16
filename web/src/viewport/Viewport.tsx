import { useEffect, useRef, useState } from 'react'
import type { StudioOutputs } from '../wire'
import { ViewportController } from './controller'
import { ViewCube } from './ViewCube'
import './viewport.css'

/**
 * The 3D view of a Part Studio. Fills its parent; draws `outputs` and redraws when they change (a new revision
 * arrives as new outputs). The controller's pointer module (src/input) navigates; the view cube is the control for standard views.
 */
export function Viewport({ outputs, selectedPart = null }: { outputs: StudioOutputs | null; selectedPart?: string | null }) {
  const host = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const controller = useRef<ViewportController | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    const el = host.current
    const cv = canvas.current
    if (!el || !cv) return
    let c: ViewportController
    try {
      c = new ViewportController(cv)
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
  }, [])

  // the selection first, so the scene a new set of outputs builds already has it
  useEffect(() => {
    controller.current?.setSelectedPart(selectedPart)
  }, [selectedPart, failed])

  useEffect(() => {
    if (outputs) controller.current?.setOutputs(outputs)
  }, [outputs, failed])

  return (
    <div ref={host} className="viewport" data-testid="viewport">
      <canvas ref={canvas} className="viewport-canvas" />
      <div className="viewport-cube-slot">
        <ViewCube onView={(n) => controller.current?.setStandardView(n)} onNormalTo={() => controller.current?.normalTo()} />
      </div>
      {failed && <p className="viewport-error">The 3D view could not start: {failed}</p>}
    </div>
  )
}
