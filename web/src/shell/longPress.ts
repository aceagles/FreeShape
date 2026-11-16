import { useRef } from 'react'
import type { MouseEvent, PointerEvent } from 'react'

/** How long a finger, pen or mouse button is held to make a long press. */
export const LONG_PRESS_MS = 500
/** Moving further than this (px) before the time is up is a drag or a scroll, not a long press. */
const SLOP = 10

/**
 * Handlers that call `onLong` with the pressed element after LONG_PRESS_MS of holding still. The click that follows
 * the release of a long press is swallowed, so the same press does not also activate the element. A shorter press
 * is left alone: it ends in an ordinary click. The context menu event is suppressed (iPadOS sends one on a hold).
 */
export function useLongPress(onLong: (el: HTMLElement) => void) {
  const held = useRef<{ timer: ReturnType<typeof setTimeout> | null; fired: boolean; x: number; y: number }>({
    timer: null,
    fired: false,
    x: 0,
    y: 0,
  })
  const cancel = () => {
    const h = held.current
    if (h.timer !== null) clearTimeout(h.timer)
    h.timer = null
  }
  return {
    onPointerDown(e: PointerEvent<HTMLElement>) {
      if (e.pointerType === 'mouse' && e.button !== 0) return
      const h = held.current
      cancel()
      h.fired = false
      h.x = e.clientX
      h.y = e.clientY
      const el = e.currentTarget
      h.timer = setTimeout(() => {
        h.timer = null
        h.fired = true
        onLong(el)
      }, LONG_PRESS_MS)
    },
    onPointerMove(e: PointerEvent<HTMLElement>) {
      const h = held.current
      if (h.timer !== null && Math.hypot(e.clientX - h.x, e.clientY - h.y) > SLOP) cancel()
    },
    onPointerUp: cancel,
    onPointerCancel: cancel,
    onClickCapture(e: MouseEvent<HTMLElement>) {
      const h = held.current
      if (!h.fired) return
      h.fired = false
      e.preventDefault()
      e.stopPropagation()
    },
    onContextMenu(e: MouseEvent<HTMLElement>) {
      e.preventDefault()
    },
  }
}
