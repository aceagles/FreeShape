import { Children } from 'react'
import type { ReactNode } from 'react'

export interface Anchor {
  left: number
  right: number
  bottom: number
  /** Top of the element, so a menu that does not fit under it can sit over it instead of covering it. */
  top?: number
}

/** The place under an element, taken when the popover opens. */
export function anchorOf(el: HTMLElement): Anchor {
  const r = el.getBoundingClientRect()
  return { left: r.left, right: r.right, bottom: r.bottom, top: r.top }
}

const WIDTH = 220
const ITEM_HEIGHT = 44
/** Padding and border of the menu box, around the items. */
const BOX = 10

/**
 * A menu under `anchor`, kept inside the window (opened above the anchor when it would run off the bottom, or moved up when there is no room above either). A press anywhere else (the transparent scrim) closes it; it does
 * not rely on hover or on the click that ends the press which opened it.
 */
export function Popover({ anchor, label, onClose, children }: { anchor: Anchor; label: string; onClose: () => void; children: ReactNode }) {
  const left = Math.max(8, Math.min(anchor.left, window.innerWidth - WIDTH - 8))
  const height = Children.count(children) * ITEM_HEIGHT + BOX
  const below = anchor.bottom + 4
  // not enough room under the element: open over it (clear of it), when there is room there
  const fitsBelow = below + height + 8 <= window.innerHeight
  const above = anchor.top === undefined ? null : anchor.top - 4 - height
  const top = fitsBelow ? below : above !== null && above >= 8 ? above : Math.max(8, Math.min(below, window.innerHeight - height - 8))
  return (
    <>
      <div className="popover-scrim" data-testid="popover-scrim" onPointerDown={onClose} />
      <div className="popover" role="menu" aria-label={label} style={{ left, top, width: WIDTH }}>
        {children}
      </div>
    </>
  )
}

export function PopoverItem({ label, icon, danger, disabled, onClick }: { label: string; icon: ReactNode; danger?: boolean; disabled?: boolean; onClick: () => void }) {
  return (
    <button type="button" role="menuitem" className="popover-item" data-danger={danger ? '' : undefined} disabled={disabled} onClick={onClick}>
      {icon}
      <span>{label}</span>
    </button>
  )
}
