import type { ConnectionStore, TabInfo } from '../net/connection'
import { useState } from 'react'
import { useConnection } from '../net/react'
import { Icon, paths } from './icons'
import { anchorOf, Popover, PopoverItem } from './Popover'
import type { Anchor } from './Popover'
import { LinkStatusButton } from './LinkStatus'
import { MenuButton } from './Menu'
import { chordFor } from '../input/bindings'
import { MENU_BAR } from './menus'
import type { Commands, MenuState } from './menus'
import { TabBar } from './TabBar'
import { useMode, useSetMode } from './mode'
import type { Mode } from './mode'
import { fullscreenSupported, toggleFullscreen, useFullscreen } from './fullscreen'

/** Full screen on and off; its icon and name follow the browser's state (Esc and the browser's own gesture included). */
function FullscreenButton() {
  const on = useFullscreen()
  const label = on ? 'Exit full screen' : 'Full screen'
  return (
    <button type="button" className="icon-button fullscreen-button" aria-label={label} title={label} data-fullscreen={on ? '' : undefined} onClick={toggleFullscreen}>
      <Icon d={on ? paths.exitFullscreen : paths.fullscreen} />
    </button>
  )
}

/**
 * Tablet mode's Export button. With `onDownload` it opens a menu of Export STEP or STL (the dialog of I11.9, on while a tab can be
 * exported) and Download Document (the whole Document as one .fsdoc, card I11.10); without it the button opens the dialog.
 */
function ExportButton({ canExport, onExport, onDownload }: { canExport: boolean; onExport: () => void; onDownload?: () => void }) {
  const [anchor, setAnchor] = useState<Anchor | null>(null)
  if (onDownload === undefined) {
    return (
      <button type="button" className="icon-button" aria-label="Export" title="Export STEP or STL" disabled={!canExport} onClick={onExport}>
        <Icon d={paths.export} />
      </button>
    )
  }
  return (
    <>
      <button type="button" className="icon-button" aria-label="Export" title="Export STEP or STL, or download the Document" aria-haspopup="menu" aria-expanded={anchor !== null} onClick={(e) => setAnchor(anchorOf(e.currentTarget))}>
        <Icon d={paths.export} />
      </button>
      {anchor !== null && (
        <Popover anchor={anchor} label="Export menu" onClose={() => setAnchor(null)}>
          <PopoverItem
            label="Export STEP / STL…"
            icon={<Icon d={paths.export} />}
            disabled={!canExport}
            onClick={() => {
              setAnchor(null)
              onExport()
            }}
          />
          <PopoverItem
            label="Download Document"
            icon={<Icon d={paths.download} />}
            onClick={() => {
              setAnchor(null)
              onDownload()
            }}
          />
        </Popover>
      )}
    </>
  )
}

interface Props {
  store: ConnectionStore
  currentTab: string | null
  readOnly: boolean
  saving: boolean
  /** The current Part Studio's can_undo and can_redo (false for a tab with no feature list). */
  canUndo: boolean
  canRedo: boolean
  onUndo: () => void
  onRedo: () => void
  debugOpen: boolean
  onSelectTab: (id: string) => void
  onAddTab: (kind: 'studio' | 'assembly') => void
  onRenameTab: (tab: TabInfo) => void
  onDeleteTab: (tab: TabInfo) => void
  onBack: () => void
  onSave: () => void
  /** Tablet mode: the Export button's action, and whether the tab on screen can be exported. */
  onExport: () => void
  canExport: boolean
  /** Tablet mode: Download Document, the Export menu's second item (card I11.10). Absent: the button opens the dialog as it did. */
  onDownload?: () => void
  onToggleDebug: () => void
  /** Desktop mode: what the menus run, and what their enabled rules read. */
  menu: { commands: Commands; state: MenuState }
  /** Desktop mode: the Search tools palette is open; the button opens and closes it, and the ? button opens the shortcut list. */
  palette: { open: boolean; toggle: () => void; shortcuts: () => void }
  /** Desktop mode: the window is under 1180 px wide, so Home and Search tools show their icons only (as on the desktop). */
  narrow: boolean
}

/**
 * The top bar: back to the home screen, the Document name, the tab switcher with add tab, undo and redo (on while the current
 * Part Studio can undo or redo), save with a dot while something is unsaved, and the connection state.
 */
export function TopBar({ store, currentTab, readOnly, saving, canUndo, canRedo, onUndo, onRedo, debugOpen, onSelectTab, onAddTab, onRenameTab, onDeleteTab, onBack, onSave, onExport, canExport, onDownload, onToggleDebug, menu, palette, narrow }: Props) {
  const { document, tabs } = useConnection(store)
  const mode = useMode()
  const setMode = useSetMode()
  const dirty = tabs.some((t) => t.dirty)
  const undoRedoSave = (
    <>
      <button type="button" className="icon-button" aria-label="Undo" disabled={readOnly || !canUndo} onClick={onUndo}>
        <Icon d={paths.undo} />
      </button>
      <button type="button" className="icon-button" aria-label="Redo" disabled={readOnly || !canRedo} onClick={onRedo}>
        <Icon d={paths.redo} />
      </button>
      <button
        type="button"
        className="icon-button save-button"
        aria-label={dirty ? 'Save, unsaved changes' : 'Save'}
        aria-busy={saving}
        data-dirty={dirty ? '' : undefined}
        disabled={readOnly || saving}
        onClick={onSave}
      >
        <Icon d={paths.save} />
        {dirty && <span className="dirty-dot" data-testid="dirty-dot" />}
      </button>
    </>
  )
  // full screen for the whole app (fullscreen.ts); not shown where the browser has no Fullscreen API
  const fullscreen = fullscreenSupported() && <FullscreenButton />
  const modeToggle = (
    <div className="mode-toggle" role="group" aria-label="Mode" data-testid="mode-toggle">
      {(['desktop', 'tablet'] as Mode[]).map((m) => (
        <button key={m} type="button" className="mode-option" aria-pressed={mode === m} data-mode={m} onClick={() => setMode(m)}>
          {m === 'desktop' ? 'Desktop' : 'Tablet'}
        </button>
      ))}
    </div>
  )
  if (mode === 'desktop') {
    // the desktop's bar (MainWindow._build_topbar): Home, the menus, the Document name, then undo, save, the link, the mode, search and help
    return (
      <header className="top-bar" data-region="top-bar" data-narrow={narrow ? '' : undefined}>
        <button type="button" className="home-button" aria-label="Home" title="Home — back to the Documents" onClick={onBack}>
          <Icon d={paths.cube} px={26} sw={1.5} stroke="var(--c-accent)" />
          <span className="home-text">freeshape</span>
        </button>
        {MENU_BAR.map((m) => (
          <MenuButton key={m.id} label={m.label} entries={m.entries} commands={menu.commands} state={menu.state} />
        ))}
        <div className="top-bar-gap" />
        <div className="top-bar-name" data-testid="document-name">
          {document?.name ?? 'FreeShape'}
        </div>
        <div className="top-bar-gap" />
        {undoRedoSave}
        <LinkStatusButton store={store} open={debugOpen} onToggle={onToggleDebug} />
        {modeToggle}
        {fullscreen}
        <span className="top-bar-rule" aria-hidden="true" />
        <button type="button" className="search-button" data-palette-toggle="" aria-label="Search tools" aria-expanded={palette.open} title={`Find a tool by name (${chordFor('palette.open')})`} onClick={palette.toggle}>
          <Icon d={paths.search} px={16} />
          <span className="search-text">Search tools</span>
          <span className="search-kbd">{chordFor('palette.open')}</span>
        </button>
        <button type="button" className="help-button" aria-label="Keyboard shortcuts" title="Keyboard shortcuts" onClick={palette.shortcuts}>
          ?
        </button>
      </header>
    )
  }
  return (
    <header className="top-bar" data-region="top-bar">
      <button type="button" className="icon-button" aria-label="Back to Documents" onClick={onBack}>
        <Icon d={paths.back} />
      </button>
      <div className="top-bar-name" data-testid="document-name">
        {document?.name ?? 'FreeShape'}
      </div>
      <TabBar tabs={tabs} currentTab={currentTab} readOnly={readOnly} onSelect={onSelectTab} onAdd={onAddTab} onRename={onRenameTab} onDelete={onDeleteTab} />
      <div className="top-bar-gap" />
      {undoRedoSave}
      <ExportButton canExport={canExport} onExport={onExport} onDownload={onDownload} />
      {modeToggle}
      {fullscreen}
      <LinkStatusButton store={store} open={debugOpen} onToggle={onToggleDebug} />
    </header>
  )
}
