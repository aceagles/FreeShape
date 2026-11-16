import { useSyncExternalStore } from 'react'
import type { ConstraintStore } from './constraints'
import type { SketchStore } from './store'
import './sketch.css'

/** Under the sketch chip: why the last constraint was refused (the sketch is as it was). */
export function ConstraintNote({ store }: { store: ConstraintStore }) {
  const s = useSyncExternalStore(store.subscribe, store.getState)
  if (!s.message) return null
  return (
    <div className="sketch-error constraint-note" role="alert" data-testid="constraint-note">
      {s.message}
    </div>
  )
}

/** In the chip slot while a Constrain tool collects picks: which tool, and what to pick next. */
export function ConstraintHint({ store, sketch }: { store: ConstraintStore; sketch: SketchStore }) {
  useSyncExternalStore(sketch.subscribe, sketch.getState)
  useSyncExternalStore(store.subscribe, store.getState)
  const hint = store.hint()
  if (hint === null) return null
  return (
    <div className="sketch-hint" role="status" data-testid="constraint-hint">
      {hint}
    </div>
  )
}
