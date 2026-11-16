/**
 * The keyboard of the page: one listener, one keymap. A key is a chord string ("Escape", "Delete", "Ctrl+Z"; modifiers
 * in the order Ctrl, Alt, Shift, Meta, then the `KeyboardEvent.key`, a letter in upper case). The keymap names a command;
 * the owner of that command supplies it to `attachKeys`. Desktop mode (slice S9) brings the desktop's bindings by adding
 * entries to `keymap` and commands to the owners, not by adding listeners.
 */
export type KeyCommand = () => boolean | void

/** The default bindings: chord to command name. */
export const keymap: Record<string, string> = {
  Escape: 'cancel',
  Delete: 'delete',
  Backspace: 'delete',
}

/** The chord a key event makes: "Escape", "Ctrl+Z", "Shift+Delete". */
export function chordOf(e: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey'>): string {
  const key = e.key.length === 1 ? e.key.toUpperCase() : e.key
  const isShiftKey = key === 'Shift' || key === 'Control' || key === 'Alt' || key === 'Meta'
  return [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && !isShiftKey && 'Shift', e.metaKey && 'Meta', key].filter(Boolean).join('+')
}

/** Something typed into (a field, an editable element) takes its own keys. */
export function isTyping(t: EventTarget | null): boolean {
  return t instanceof HTMLElement && (t.isContentEditable || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')
}

let held = 0

/**
 * Keep the keymap quiet until the returned function is called: for something that reads the keys itself while it is on, such
 * as the number pad of a field being edited. Calls nest.
 */
export function suspendKeys(): () => void {
  held++
  let done = false
  return () => {
    if (!done) held--
    done = true
  }
}

/** Whether the keymap is quiet now. */
export const keysSuspended = (): boolean => held > 0

/**
 * Listen to the keyboard and run the command the keymap names for each chord. A command that returns false did not take the
 * key, so the browser's own action stays. Nothing runs while a field has the key, the number pad is editing (`suspendKeys`),
 * or an earlier listener already took the event. Returns the way to stop listening.
 */
export function attachKeys(commands: Record<string, KeyCommand>, map: Record<string, string> = keymap): () => void {
  const onKey = (e: KeyboardEvent): void => {
    if (e.defaultPrevented || e.isComposing || keysSuspended() || isTyping(e.target)) return
    const name = map[chordOf(e)]
    const run = name === undefined ? undefined : commands[name]
    if (run && run() !== false) e.preventDefault()
  }
  window.addEventListener('keydown', onKey)
  return () => window.removeEventListener('keydown', onKey)
}
