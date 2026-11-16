import type { KeyCommand } from '../input/keys'
import type { SketchStore } from './store'

/** The keymap's commands that act on the sketch (input/keys.ts names them): Esc steps back, as the desktop sketcher does. */
export function sketchKeys(store: SketchStore): Record<string, KeyCommand> {
  return { cancel: () => store.stepBack() }
}
