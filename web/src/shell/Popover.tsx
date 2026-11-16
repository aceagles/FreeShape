import type { ReactNode } from 'react'

export interface Anchor {
  left: number
  right: number
  bottom: number
}

/** The place under an element, taken when the popover opens. */
export function anchorOf(el: HTMLElement): Anchor {
  const r = el.getBoundingClientRect()
  return { left: r.left, right: r.right, bottom: r.bottom }
}

const WIDTH = 220

/**
 * A menu under `anchor`, kept inside the window. A press anywhere else (the transparent scrim) closes it; it does
 * not rely on hover or on the click that ends the press which opened it.
 */
export function Popover({ anchor, label, onClose, children }: { anchor: Anchor; label: string; onClose: () => void; children: ReactNode }) {
  const left = Math.max(8, Math.min(anchor.left, window.innerWidth - WIDTH - 8))
  return (
    <>
      <div className="popover-scrim" data-testid="popover-scrim" onPointerDown={onClose} />
      <div className="popover" role="menu" aria-label={label} style={{ left, top: anchor.bottom + 4, width: WIDTH }}>
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
