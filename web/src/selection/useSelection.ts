import { useSyncExternalStore } from 'react'
import type { SelectionState, SelectionStore } from './store'

/**
 * The selection's items and mode, for a component. It re-renders when those change and not when only the Aim does
 * (the Aim moves with every pen sample; the view reads it from the store itself).
 */
export function useSelection(store: SelectionStore): SelectionState {
  return useSyncExternalStore(store.subscribe, store.getState)
}
