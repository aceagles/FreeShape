import { useEffect, useRef, useState } from 'react'
import type { StudioOutputs } from '../wire'
import { ViewportController } from './controller'
import './viewport.css'

/**
 * The 3D view of a Part Studio. Fills its parent; draws `outputs` and redraws when they change (a new revision
 * arrives as new outputs). Navigation is not here: the pointer module of a later card drives the camera.
 */
export function Viewport({ outputs }: { outputs: StudioOutputs | null }) {
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

  useEffect(() => {
    if (outputs) controller.current?.setOutputs(outputs)
  }, [outputs, failed])

  return (
    <div ref={host} className="viewport" data-testid="viewport">
      <canvas ref={canvas} className="viewport-canvas" />
      {failed && <p className="viewport-error">The 3D view could not start: {failed}</p>}
    </div>
  )
}
