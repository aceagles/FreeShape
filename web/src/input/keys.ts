import { BINDINGS, desktopKeymap, TABLET_CHORDS } from './bindings'
import type { Mode } from '../shell/mode'

/**
 * The keyboard of the page: one listener, one keymap. A key is a chord string ("Escape", "Delete", "Ctrl+Z"; modifiers
 * in the order Ctrl, Alt, Shift, Meta, then the `KeyboardEvent.key`, a letter in upper case). The keymap names a command;
 * the owner of that command supplies it to `attachKeys`. Desktop mode (slice S9, card I9.6) binds the desktop's keys, read
 * from the table in input/bindings.ts (`keymapFor`), through the same listener.
 */
export type KeyCommand = () => boolean | void

/** The default bindings: chord to command name. */
export const keymap: Record<string, string> = {
  Escape: 'cancel',
  Delete: 'delete',
  Backspace: 'delete',
}

/** The keymap of a mode: Desktop binds every key of input/bindings.ts; Tablet the chords `TABLET_CHORDS` names. */
export function keymapFor(mode: Mode): Readonly<Record<string, string>> {
  if (mode === 'desktop' || TABLET_CHORDS === 'all') return desktopKeymap
  return Object.fromEntries(BINDINGS.filter((b) => !b.shownOnly && TABLET_CHORDS.includes(b.chord)).map((b) => [b.chord, b.command]))
}

/**
 * The chord a key event makes: "Escape", "Ctrl+Z", "Shift+Delete", "Space". A digit is named by its key position, so Shift+1
 * is "Shift+1" whatever the layout types ("!" on most); so is a letter with Alt, which a Mac turns into another character.
 */
export function chordOf(e: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey'> & { code?: string }): string {
  const digit = /^Digit(\d)$/.exec(e.code ?? '')
  const letter = /^Key([A-Z])$/.exec(e.code ?? '')
  const typed = e.key === ' ' ? 'Space' : e.key.length === 1 ? e.key.toUpperCase() : e.key
  // Alt+` types a dead key on a Mac: named by its key position, as an Alt letter is
  const key = e.altKey && e.code === 'Backquote' ? '`' : digit ? digit[1]! : letter && e.altKey && !/^[A-Z]$/.test(typed) ? letter[1]! : typed
  const isShiftKey = key === 'Shift' || key === 'Control' || key === 'Alt' || key === 'Meta'
  return [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && !isShiftKey && 'Shift', e.metaKey && 'Meta', key].filter(Boolean).join('+')
}

/** Something typed into (a field, an editable element) takes its own keys; so does an open menu (it moves and runs its items). */
export function isTyping(t: EventTarget | null): boolean {
  return t instanceof HTMLElement && (t.isContentEditable || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.closest('[role="menu"]') !== null)
}

let held = 0

/** The key lookup of the attached listener, so a field that took a key can hand it on (`runKeyChord`). */
let dispatch: ((e: KeyLike) => boolean) | null = null
type KeyLike = Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey'> & { code?: string }

/**
 * Run the command the keymap names for a key event, without the listener's checks for a field, the pad or a default already
 * prevented. For a field that read a key first and decided it was a command's (the dimension box on a tool key, card I9.7).
 * True when a command took it.
 */
export function runKeyChord(e: KeyLike): boolean {
  return dispatch?.(e) ?? false
}

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
export function attachKeys(
  commands: Record<string, KeyCommand> | (() => Record<string, KeyCommand>),
  map: Readonly<Record<string, string>> | (() => Readonly<Record<string, string>>) = keymap,
): () => void {
  // a function is read at each key, so one listener follows a keymap and commands that change (the mode, what is open)
  // a chord bound in two workspaces names both commands, space separated: the first that does not decline takes the key
  const run = (e: KeyLike): boolean => {
    const names = (typeof map === 'function' ? map() : map)[chordOf(e)]
    if (names === undefined) return false
    const all = typeof commands === 'function' ? commands() : commands
    return names.split(' ').some((name) => {
      const command = all[name]
      return command !== undefined && command() !== false
    })
  }
  const onKey = (e: KeyboardEvent): void => {
    if (e.defaultPrevented || e.isComposing || keysSuspended() || isTyping(e.target)) return
    if (run(e)) e.preventDefault()
  }
  window.addEventListener('keydown', onKey)
  dispatch = run
  return () => {
    window.removeEventListener('keydown', onKey)
    if (dispatch === run) dispatch = null
  }
}
