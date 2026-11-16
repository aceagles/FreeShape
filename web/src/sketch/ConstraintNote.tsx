import { useSyncExternalStore } from 'react'
import type { ConstraintStore } from './constraints'
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
