import type { KeyCommand } from '../input/keys'
import type { ConstraintStore } from './constraints'
import type { SketchStore } from './store'

/**
 * The keymap's commands that act on the sketch (input/keys.ts names them). Esc steps back, as the desktop sketcher does; a
 * constraint tool's pick mode goes first (the picks are dropped and the mode left), and only then the store's own order.
 * Delete removes the selected constraints.
 */
export function sketchKeys(store: SketchStore, constraints?: ConstraintStore): Record<string, KeyCommand> {
  return { cancel: () => constraints?.escape() || store.stepBack(), delete: () => constraints?.removeSelected() ?? false }
}
