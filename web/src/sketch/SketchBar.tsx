import { Icon } from '../shell/icons'
import { StripButton } from '../shell/StripButton'
import type { SketchNav } from './store'
import './sketch.css'

const NORMAL_TO = 'M3 18l6-8h12l-6 8z M12 13V3 M9 6l3-3 3 3'

export interface SketchBarProps {
  nav: SketchNav
  onNav: (nav: SketchNav) => void
  onNormalTo: () => void
  onCancel: () => void
  onDone: () => void
  /** A commit or a cancel is on its way, or the sketch is not open yet. */
  busy?: boolean
}

/**
 * The right end of the sketch strip (layout mock, sketch state): the 2D / 3D toggle, Normal to, Cancel and Done. 2D keeps
 * the view square to the sketch plane (one finger pans; two fingers pan, zoom and twist); 3D orbits as in the Part Studio.
 */
export function SketchBar({ nav, onNav, onNormalTo, onCancel, onDone, busy }: SketchBarProps) {
  return (
    <div className="sketch-bar" data-testid="sketch-bar">
      <div className="nav-toggle" role="group" aria-label="Navigation">
        <button type="button" aria-pressed={nav === '2d'} data-testid="nav-2d" onClick={() => onNav('2d')}>
          2D
        </button>
        <button type="button" aria-pressed={nav === '3d'} data-testid="nav-3d" onClick={() => onNav('3d')}>
          3D
        </button>
      </div>
      <StripButton label="Normal to" icon={<Icon d={NORMAL_TO} px={24} />} data-testid="normal-to" narrow onClick={onNormalTo} />
      <button type="button" className="sketch-cancel" data-testid="sketch-cancel" disabled={busy} onClick={onCancel}>
        Cancel
      </button>
      <button type="button" className="sketch-done" data-testid="sketch-done" disabled={busy} onClick={onDone}>
        Done
      </button>
    </div>
  )
}
