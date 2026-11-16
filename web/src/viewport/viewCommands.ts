import type { SelectionItem } from '../selection/store'
import type { StandardView } from './camera'

/** What the menus and the desktop's keys can ask of the 3D view on screen. A view without a key's method ignores that key. */
export interface ViewHandler {
  setStandardView(name: StandardView): void
  fit(): void
  /** An arrow key: turn one 15 degree step (h: Right arrow -1, v: Up arrow +1). */
  turnStep?(h: number, v: number): boolean
  /** Ctrl+Shift+arrow: pan a tenth of the view (sx right, sy down). */
  panStep?(sx: number, sy: number): boolean
  /** N: look straight at the item (or the opposite side with none). */
  normalKey?(item: SelectionItem | null): boolean
}

/** The view on screen, for the desktop's keys; null when none is. */
export const viewOnScreen = (): ViewHandler | null => current

let current: ViewHandler | null = null

/** The view on screen takes the menus' view commands. Returns the way to let go (only the latest holder is cleared). */
export function registerView(handler: ViewHandler): () => void {
  current = handler
  return () => {
    if (current === handler) current = null
  }
}

/** Look along a standard view, or fit the model. False when no view is on screen. */
export function runViewCommand(command: StandardView | 'fit'): boolean {
  if (current === null) return false
  if (command === 'fit') current.fit()
  else current.setStandardView(command)
  return true
}
