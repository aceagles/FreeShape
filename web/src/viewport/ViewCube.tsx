import { useState } from 'react'
import type { StandardView } from './camera'
import './viewcube.css'

const VIEWS: readonly { name: StandardView; label: string }[] = [
  { name: 'iso', label: 'Isometric' },
  { name: 'top', label: 'Top' },
  { name: 'bottom', label: 'Bottom' },
  { name: 'front', label: 'Front' },
  { name: 'back', label: 'Back' },
  { name: 'left', label: 'Left' },
  { name: 'right', label: 'Right' },
]

/**
 * The view cube: a 72 px button (the approved layout) that opens the standard views and Normal to. The app shell
 * places it; it is positioned by its parent. It is a plain button, not part of the view, so it has no pointer
 * handling of its own beyond clicks.
 */
export function ViewCube({ onView, onNormalTo }: { onView: (name: StandardView) => void; onNormalTo: () => void }) {
  const [open, setOpen] = useState(false)
  const pick = (act: () => void) => () => {
    setOpen(false)
    act()
  }
  return (
    <div className="view-cube">
      {open && <div className="view-cube-backdrop" data-testid="view-cube-backdrop" onClick={() => setOpen(false)} />}
      <button
        type="button"
        className="view-cube-button"
        data-region="view-cube"
        aria-label="View cube, showing an isometric view"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <svg width="52" height="52" viewBox="0 0 52 52" aria-hidden="true">
          <polygon points="26,5 46,15 26,25 6,15" fill="#f5f6f8" stroke="#3b4250" strokeWidth="1.4" strokeLinejoin="round" />
          <polygon points="6,15 26,25 26,47 6,37" fill="#e3eefb" stroke="#3b4250" strokeWidth="1.4" strokeLinejoin="round" />
          <polygon points="26,25 46,15 46,37 26,47" fill="#d3e4f8" stroke="#3b4250" strokeWidth="1.4" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div className="view-cube-menu" role="menu" aria-label="Standard views">
          {VIEWS.map((v) => (
            <button key={v.name} type="button" role="menuitem" className={v.name === 'iso' ? 'view-cube-item view-cube-wide' : 'view-cube-item'} onClick={pick(() => onView(v.name))}>
              {v.label}
            </button>
          ))}
          <button type="button" role="menuitem" className="view-cube-item view-cube-wide view-cube-normal" onClick={pick(onNormalTo)}>
            Normal to
          </button>
        </div>
      )}
    </div>
  )
}
