import { useRef } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'

const STEP = 16

/**
 * The bar between the left column and the viewport (Desktop mode). Dragging it, or the arrow keys with it focused, asks for
 * a new column width through `onResize`; the shell keeps it inside its limits and per device. It sits on the column's right
 * edge and takes no room of its own.
 */
export function Splitter({ width, onResize }: { width: number; onResize: (width: number) => void }) {
  const drag = useRef<{ x: number; width: number } | null>(null)
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    drag.current = { x: e.clientX, width }
    e.currentTarget.setPointerCapture?.(e.pointerId)
    e.preventDefault()
  }
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (drag.current) onResize(drag.current.width + e.clientX - drag.current.x)
  }
  const onUp = (e: PointerEvent<HTMLDivElement>) => {
    drag.current = null
    if (e.currentTarget.hasPointerCapture?.(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
  }
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    onResize(width + (e.key === 'ArrowRight' ? STEP : -STEP))
  }
  return (
    <div
      className="splitter"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the left column"
      aria-valuenow={width}
      tabIndex={0}
      data-testid="splitter"
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
      onKeyDown={onKey}
    />
  )
}
