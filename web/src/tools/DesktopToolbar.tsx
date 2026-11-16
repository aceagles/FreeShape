import { desktopTip } from './defs'
import type { DesktopEntry, DesktopLayout } from './defs'
import { ToolIcon } from './icons'
import type { ToolState } from './useToolTypes'
import { DESKTOP_ONLY_NOTE } from './ToolStrip'
import './toolbar.css'

/** The pixel size of a button and of its icon: the desktop draws 26 px icons. */
export const TOOLBAR_BUTTON = 26

/** What the shell does for a command button. A command with no entry is not something the client can do yet. */
export interface ToolbarCommand {
  run: () => void
  /** Default: on. */
  enabled?: boolean
  /** A toggle that is on. */
  pressed?: boolean
  /** The button's name where the shell has a better one than the layout's (a type's label from the server). */
  label?: string
  /** The tooltip's text before the key, where the shell has one (a type's help from the server). */
  tip?: string
  /** Why the button is off, as its tooltip. */
  note?: string
}

export interface DesktopToolbarProps {
  layout: DesktopLayout
  /** What a feature tool can do now (see useToolTypes). */
  stateOf: (id: string) => ToolState
  /** The command buttons the client has, by tool id. */
  commands: Readonly<Record<string, ToolbarCommand>>
  /** The feature buttons are off (a dialog is open, no Part Studio is on screen); the commands keep their own rules. */
  busy?: boolean
  /** A feature tool starts: the same call as a tablet strip button's. */
  onStart: (id: string) => void
  /** Which row this is, for `data-strip` (default the feature row): 'sketch', 'assembly'. */
  strip?: string
}

/**
 * The Desktop-mode feature toolbar: one row of flat icon-only buttons, a rule where the layout has a separator, and the
 * layout's right-end buttons at the right. One component draws any layout (defs.ts `DesktopLayout`); no tool has JSX of
 * its own. A feature button takes its state from the types route: ok is on, dimmed is on with the reason in its tooltip
 * (it starts nothing until the selection fits), desktop and off are disabled with "Desktop only for now". A command
 * button is disabled with the same note unless the shell supplies it.
 */
export function DesktopToolbar({ layout, stateOf, commands, busy, onStart, strip = 'feature' }: DesktopToolbarProps) {
  const button = (e: Exclude<DesktopEntry, { kind: 'separator' }>) => {
    const id = e.tool.id
    const tip = desktopTip(e)
    if (e.kind === 'command') {
      const cmd = commands[id]
      const has = cmd !== undefined
      const on = has && cmd.enabled !== false
      const tipped = has ? desktopTip({ ...e, tip: cmd.tip ?? e.tip }) : tip
      return (
        <button
          key={id}
          type="button"
          className={e.text !== undefined ? `tb-button tb-text${e.accent ? ' tb-accent' : ''}` : 'tb-button'}
          data-tool={id}
          data-state={has ? (on ? 'ok' : 'off') : 'desktop'}
          aria-label={cmd?.label ?? e.tool.label}
          aria-pressed={has && cmd.pressed !== undefined ? cmd.pressed : undefined}
          title={has ? (on ? tipped : (cmd.note ?? tipped)) : DESKTOP_ONLY_NOTE}
          disabled={!on}
          onClick={() => cmd?.run()}
        >
          {e.text !== undefined ? e.text : <ToolIcon name={e.tool.icon} px={TOOLBAR_BUTTON} />}
        </button>
      )
    }
    const state: ToolState = busy ? 'off' : stateOf(id)
    const title = state === 'desktop' || (state === 'off' && !busy) ? DESKTOP_ONLY_NOTE : state === 'dimmed' ? `${tip} — pick something it can use first` : tip
    return (
      <button
        key={id}
        type="button"
        className="tb-button"
        data-tool={id}
        data-state={state}
        aria-label={e.tool.label}
        title={title}
        disabled={state === 'off' || state === 'desktop'}
        aria-disabled={state === 'dimmed' ? true : undefined}
        onClick={() => state === 'ok' && onStart(id)}
      >
        <ToolIcon name={e.tool.icon} px={TOOLBAR_BUTTON} />
      </button>
    )
  }
  const draw = (entries: readonly DesktopEntry[], at: string) =>
    entries.map((e, i) => (e.kind === 'separator' ? <span key={`${at}${i}`} className="tb-rule" role="separator" aria-orientation="vertical" /> : button(e)))
  return (
    <div className="desktop-toolbar" data-region="tool-strip" data-strip={strip} role="toolbar" aria-label="Tools">
      {/* the left part scrolls when the window is narrower than the row (the desktop clips it); the right end stays */}
      <div
        className="tb-scroll"
        data-testid="tb-scroll"
        onWheel={(e) => {
          if (e.deltaX === 0) e.currentTarget.scrollLeft += e.deltaY
        }}
      >
        {draw(layout.items, 'l')}
      </div>
      <div className="tb-end">{draw(layout.trailing, 'r')}</div>
    </div>
  )
}
