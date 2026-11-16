import { SKETCH_TOOL_COMMANDS } from '../input/bindings'
import type { KeyCommand } from '../input/keys'
import { isConstrainTool } from './constraints'
import type { ConstraintStore } from './constraints'
import type { SketchStore } from './store'

/** The hint for a key whose tool the client does not have yet. */
export const NOT_YET = 'Desktop only for now'

/** The tools the sketcher takes a key for that the client lacks (the desktop's Offset). */
const MISSING: ReadonlySet<string> = new Set(['offset'])

/**
 * A tool key, as the desktop's `Sketcher.key_press` runs it (`set_tool`). A drawing tool, Select, Dimension and Use take the key
 * at once and drop the selection; a tool already on starts over rather than going back to Select as its button does. A Constrain
 * tool is tool-first, as its button: a selection that fits is applied, else it waits for picks; the key for the tool already
 * collecting picks does nothing. Offset shows a hint.
 */
function toolKey(store: SketchStore, constraints: ConstraintStore, id: string): boolean {
  if (!store.getState().open) return false
  if (MISSING.has(id)) {
    store.showNotice(NOT_YET)
    return true
  }
  if (isConstrainTool(id)) {
    if (constraints.getState().picking !== id) {
      store.chooseTool('select')
      constraints.choose(id)
    }
    return true
  }
  // a half-done dimension (an open box, a pick) is given up first; the tool key then starts the tool
  store.escapeTools()
  store.keyTool(id)
  return true
}

/**
 * The commands of the sketch's own keys in Desktop mode (input/bindings.ts names them). Added to the keymap's commands only
 * while a sketch is open on the tab on screen. Space, Delete and Esc are the workspace keys in shell/desktopKeys.ts.
 */
export function sketchKeyCommands(store: SketchStore, constraints: ConstraintStore): Record<string, KeyCommand> {
  const out: Record<string, KeyCommand> = {
    'sketch.construction': () => (store.toggleConstruction(), true),
    'sketch.selectAll': () => (store.selectAll(), true),
  }
  for (const [command, tool] of SKETCH_TOOL_COMMANDS) out[command] = () => toolKey(store, constraints, tool)
  return out
}

/**
 * Esc in a sketch, in the desktop's order (`Sketcher.escape`): an open dimension box closes and nothing else happens; else a
 * shape in progress, a chain, a dimension's picks or a Constrain tool's picks are dropped and the selection is cleared; else
 * the tool goes back to Select; else the selection clears. True when it did one of these; false when there is nothing left to step back.
 */
export function escapeSketch(store: SketchStore, constraints: ConstraintStore): boolean {
  if (store.escapeTools()) return true
  if (constraints.escape()) return true
  const drawing = store.getState().drawing > 0
  const stepped = store.stepBack()
  if (stepped && drawing) store.clearSelection()
  return stepped
}
