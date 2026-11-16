import { useState } from 'react'
import { useMode } from '../shell/mode'

export type EyeState = 'shown' | 'off'

/**
 * When a row's eye (or Assembly lock) is drawn. Tablet: always. Desktop: on the row under the mouse and on a row that is
 * `marked` (hidden, or fixed), like the desktop's _EyeDelegate; the stylesheet reads data-eye and fades the others out.
 * `bind` goes on the row's element; `eyeFor(marked)` answers for a second button of the same row. Desktop only: a mouse is
 * there to hover with.
 */
export function useRowEye(marked: boolean): { eye: EyeState; eyeFor: (marked: boolean) => EyeState; bind: { onMouseEnter: () => void; onMouseLeave: () => void } } {
  const desktop = useMode() === 'desktop'
  const [hover, setHover] = useState(false)
  const eyeFor = (m: boolean): EyeState => (!desktop || hover || m ? 'shown' : 'off')
  return { eye: eyeFor(marked), eyeFor, bind: { onMouseEnter: () => setHover(true), onMouseLeave: () => setHover(false) } }
}
