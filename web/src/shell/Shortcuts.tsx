import { BINDINGS } from '../input/bindings'
import type { Binding, Workspace } from '../input/bindings'
import { Modal } from './Modal'
import { DESKTOP_ONLY } from './menus'
import './shortcuts.css'

/** One line of the list: what a key does, and every chord that does it in the workspace. */
export interface ShortcutRow {
  label: string
  chords: string[]
  /** Shown beside a desktop-only menu item but not bound: the client cannot do it yet. */
  shownOnly: boolean
}

export interface ShortcutGroup {
  workspace: Workspace
  title: string
  rows: ShortcutRow[]
}

/** The workspaces, in the order the list shows them, with their headings. */
const GROUPS: readonly (readonly [Workspace, string])[] = [
  ['studio', 'Part Studio'],
  ['sketch', 'Sketch'],
  ['assembly', 'Assembly'],
  ['dialog', 'Feature dialog'],
]

/**
 * The shortcut list, from the binding data only (input/bindings.ts): a group for each workspace a key works in, the bindings
 * in the table's order. A binding that works in two workspaces is in both groups (P: Point in a sketch, Show planes in a Part
 * Studio). Chords of one label in a group share a row (Ctrl+Y and Ctrl+Shift+Z are Redo; the four arrows turn the view). A
 * new binding appears here with no edit to this list.
 */
export function shortcutGroups(bindings: readonly Binding[] = BINDINGS): ShortcutGroup[] {
  return GROUPS.flatMap(([workspace, title]): ShortcutGroup[] => {
    const rows: ShortcutRow[] = []
    for (const b of bindings) {
      if (!b.where.includes(workspace)) continue
      const same = rows.find((r) => r.label === b.label && r.shownOnly === (b.shownOnly === true))
      if (same !== undefined) same.chords.push(b.chord)
      else rows.push({ label: b.label, chords: [b.chord], shownOnly: b.shownOnly === true })
    }
    return rows.length === 0 ? [] : [{ workspace, title, rows }]
  })
}

/** A chord as it is read on a key cap: "ArrowLeft" is an arrow, "Escape" is Esc. */
export function chordText(chord: string): string {
  const names: Record<string, string> = { ArrowLeft: '←', ArrowRight: '→', ArrowUp: '↑', ArrowDown: '↓', Escape: 'Esc', Delete: 'Del', Backspace: '⌫' }
  return chord
    .split('+')
    .map((p) => names[p] ?? p)
    .join('+')
}

/**
 * The list of every key (card I9.11), opened by the top bar's ? button and the Document menu's Keyboard shortcuts. The desktop's
 * show_shortcuts is a text box; this is built from the bindings, so a key added there shows here.
 */
export function Shortcuts({ onClose }: { onClose: () => void }) {
  const groups = shortcutGroups()
  return (
    <Modal title="Keyboard shortcuts" onClose={onClose} className="shortcuts-modal">
      <div className="shortcuts" data-testid="shortcuts">
        {groups.map((g) => (
          <section key={g.workspace} className="shortcuts-group" data-workspace={g.workspace} aria-label={g.title}>
            <h3 className="shortcuts-heading">{g.title}</h3>
            <ul className="shortcuts-rows">
              {g.rows.map((r) => (
                <li key={`${r.label}|${r.chords[0]}`} className="shortcuts-row" data-shown-only={r.shownOnly ? '' : undefined} title={r.shownOnly ? DESKTOP_ONLY : undefined}>
                  <span className="shortcuts-label">{r.label}</span>
                  <span className="shortcuts-chords">
                    {r.chords.map((c) => (
                      <kbd key={c} className="shortcuts-key" data-chord={c}>
                        {chordText(c)}
                      </kbd>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <div className="modal-buttons">
        <button type="button" className="modal-button" data-kind="primary" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  )
}
