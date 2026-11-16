import { useEffect, useRef, useState } from 'react'
import type { SelectionStore } from '../selection/store'
import type { AssemblyOutputs } from '../wire'
import { ViewCube } from '../viewport/ViewCube'
import '../viewport/viewport.css'
import { AssemblyController } from './controller'

/**
 * The 3D view of an Assembly tab: draws `outputs` (one object per instance under its placement) and draws again whenever
 * they change; `selection` is the store the view picks for and draws. The pointer module navigates and the view cube sets
 * the standard views, as in a Part Studio.
 */
export function AssemblyViewport({ outputs, selection }: { outputs: AssemblyOutputs | null; selection: SelectionStore }) {
  const host = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const controller = useRef<AssemblyController | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    const el = host.current
    const cv = canvas.current
    if (!el || !cv) return
    let c: AssemblyController
    try {
      c = new AssemblyController(cv, selection)
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
  }, [selection])

  useEffect(() => {
    if (outputs) controller.current?.setOutputs(outputs)
    else controller.current?.clear()
  }, [outputs, failed])

  return (
    <div ref={host} className="viewport" data-testid="viewport">
      <canvas ref={canvas} className="viewport-canvas" />
      <div className="viewport-cube-slot">
        <ViewCube onView={(n) => controller.current?.setStandardView(n)} onNormalTo={() => controller.current?.normalTo()} />
      </div>
      {outputs !== null && outputs.instances.length === 0 && (
        <p className="viewport-empty" data-testid="viewport-empty">
          This Assembly has no instances.
        </p>
      )}
      {failed && <p className="viewport-error">The 3D view could not start: {failed}</p>}
    </div>
  )
}
