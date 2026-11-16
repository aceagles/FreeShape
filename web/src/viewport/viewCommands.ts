import type { StandardView } from './camera'

/** What the menus can ask of the 3D view on screen. */
export interface ViewHandler {
  setStandardView(name: StandardView): void
  fit(): void
}

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
