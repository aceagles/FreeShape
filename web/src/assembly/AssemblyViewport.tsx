import { useCallback, useEffect, useRef, useState } from 'react'
import { useShell } from '../shell/ShellContext'
import type { SelectionStore } from '../selection/store'
import type { AssemblyOutputs } from '../wire'
import { ViewCube } from '../viewport/ViewCube'
import { registerView } from '../viewport/viewCommands'
import '../viewport/viewport.css'
import { AssemblyController } from './controller'
import { GizmoOverlay, GizmoPad } from './GizmoOverlay'
import type { GizmoShape } from './gizmo'
import type { DragSender } from './gizmo'

/**
 * The 3D view of an Assembly tab: draws `outputs` (one object per instance under its placement) and draws again whenever
 * they change; `selection` is the store the view picks for and draws. The pointer module navigates and the view cube sets
 * the standard views, as in a Part Studio.
 */
export function AssemblyViewport({ outputs, selection, drag }: { outputs: AssemblyOutputs | null; selection: SelectionStore; drag?: DragSender }) {
  const shell = useShell()
  const [gizmo, setGizmo] = useState<GizmoShape | null>(null)
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
      c = new AssemblyController(cv, selection, setGizmo)
    } catch (e) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFailed(e instanceof Error ? e.message : 'WebGL is not available')
      return
    }
    controller.current = c
    const unregister = registerView(c)
    const size = () => c.resize(el.clientWidth, el.clientHeight)
    size()
    const ro = new ResizeObserver(size)
    ro.observe(el)
    return () => {
      unregister()
      ro.disconnect()
      c.dispose()
      controller.current = null
    }
  }, [selection])

  useEffect(() => {
    controller.current?.setDragSender(drag ?? null)
  }, [drag, failed])

  const { openDialog, closeDialog } = shell
  const typeValue = useCallback(() => {
    const c = controller.current
    const r = c?.gizmo.readout()
    const s = c?.gizmo.current()
    if (!c || !r || !s) return
    openDialog(
      `Move ${s.name}`,
      <GizmoPad kind={r.kind} value={r.value} onApply={(total) => void c.gizmo.typed(total)} onClose={closeDialog} />,
    )
  }, [openDialog, closeDialog])

  useEffect(() => {
    if (outputs) controller.current?.setOutputs(outputs)
    else controller.current?.clear()
  }, [outputs, failed])

  return (
    <div ref={host} className="viewport" data-testid="viewport">
      <canvas ref={canvas} className="viewport-canvas" />
      <GizmoOverlay shape={gizmo} onLabel={typeValue} />
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
