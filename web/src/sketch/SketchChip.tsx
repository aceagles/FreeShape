import { useSyncExternalStore } from 'react'
import { useMode } from '../shell/mode'
import { selectionText, statusText } from './store'
import type { SketchStore } from './store'
import '../selection/selection.css'
import './sketch.css'

/**
 * Top left of the view while a sketch is open (layout mock, sketch state): the selection chip with what is selected in the
 * sketch, Multi and Clear, and beside it the sketch's name and how far the solve is ("3 items free", "Fully defined").
 */
export function SketchChip({ store }: { store: SketchStore }) {
  const s = useSyncExternalStore(store.subscribe, store.getState)
  // Desktop mode has no Multi: a click adds to the selection there (I9.9)
  const tablet = useMode() === 'tablet'
  if (!s.open || !s.view) return null
  const empty = s.selected.length === 0
  const status = statusText(s.view)
  return (
    <>
      <div className="selection-chip" data-testid="selection-chip" data-empty={empty || undefined}>
        <span className="selection-chip-count">
          <span className="selection-chip-swatch" aria-hidden="true" />
          <span data-testid="selection-count">{selectionText(s.selected)}</span>
        </span>
        {tablet && (
          <button type="button" className="selection-chip-multi" aria-pressed={s.multi} data-testid="selection-multi" onClick={() => store.setMulti(!s.multi)}>
            Multi
          </button>
        )}
        <button type="button" className="selection-chip-clear" data-testid="selection-clear" disabled={empty} onClick={() => store.clearSelection()}>
          Clear
        </button>
      </div>
      <div className="sketch-status" data-testid="sketch-status" data-tone={status.tone}>
        <span className="sketch-name">{s.view.name}</span>
        <span className="sketch-solve">{status.text}</span>
      </div>
      {s.notice !== null && (
        <div className="sketch-hint" role="status" data-testid="sketch-notice">
          {s.notice}
        </div>
      )}
      {s.error && (
        <div className="sketch-error" role="alert" data-testid="sketch-error">
          {s.error}
        </div>
      )}
    </>
  )
}
