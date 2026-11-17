import { useRef } from 'react'
import type { PointerEvent } from 'react'
import { useLongPress } from './longPress'
import { useMode } from './mode'

/** A right press that moves no further than this (px) before it is released is a right click; further, it was a drag. */
export const RIGHT_CLICK_SLOP = 10

/**
 * Handlers for a right click on a row, in Desktop mode (card I9.10): the mouse's right button pressed and released
 * without moving calls `onOpen` with where it was, in window coordinates. It reads Pointer Events, as the pointer module
 * does for the view, because the browser's own `contextmenu` event is kept off by input/surface.ts. Tablet mode, pen and
 * finger: nothing.
 */
export function useRightClick(onOpen: (x: number, y: number) => void) {
  const desktop = useMode() === 'desktop'
  const down = useRef<{ id: number; x: number; y: number } | null>(null)
  return {
    onPointerDown(e: PointerEvent<HTMLElement>) {
      down.current = desktop && e.pointerType === 'mouse' && e.button === 2 ? { id: e.pointerId, x: e.clientX, y: e.clientY } : null
    },
    onPointerUp(e: PointerEvent<HTMLElement>) {
      const d = down.current
      if (!d || e.pointerId !== d.id || e.button !== 2) return
      down.current = null
      if (Math.hypot(e.clientX - d.x, e.clientY - d.y) <= RIGHT_CLICK_SLOP) onOpen(e.clientX, e.clientY)
    },
    onPointerCancel() {
      down.current = null
    },
  }
}

/**
 * A row's menu gestures: a long press opens `onLong` in Tablet mode (the popovers stay for it), a right click opens
 * `onContext` in Desktop mode, and a long press in Desktop mode does nothing extra. Spread it on the row's button.
 */
export function useMenuPress(canMenu: boolean, onLong: (el: HTMLElement) => void, onContext: (x: number, y: number) => void) {
  const desktop = useMode() === 'desktop'
  const press = useLongPress((el) => !desktop && canMenu && onLong(el))
  const right = useRightClick((x, y) => canMenu && onContext(x, y))
  return {
    ...press,
    onPointerDown(e: PointerEvent<HTMLElement>) {
      press.onPointerDown(e)
      right.onPointerDown(e)
    },
    onPointerUp(e: PointerEvent<HTMLElement>) {
      press.onPointerUp()
      right.onPointerUp(e)
    },
    onPointerCancel() {
      press.onPointerCancel()
      right.onPointerCancel()
    },
  }
}
