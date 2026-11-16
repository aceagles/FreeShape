import type { ConnectionStore, TabInfo } from '../net/connection'
import { useConnection } from '../net/react'
import { Icon, paths } from './icons'
import { LinkStatusButton } from './LinkStatus'
import { TabBar } from './TabBar'
import { useMode, useSetMode } from './mode'
import type { Mode } from './mode'

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
  onToggleDebug: () => void
}

/**
 * The top bar: back to the home screen, the Document name, the tab switcher with add tab, undo and redo (on while the current
 * Part Studio can undo or redo), save with a dot while something is unsaved, and the connection state.
 */
export function TopBar({ store, currentTab, readOnly, saving, canUndo, canRedo, onUndo, onRedo, debugOpen, onSelectTab, onAddTab, onRenameTab, onDeleteTab, onBack, onSave, onToggleDebug }: Props) {
  const { document, tabs } = useConnection(store)
  const mode = useMode()
  const setMode = useSetMode()
  const dirty = tabs.some((t) => t.dirty)
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
      <div className="mode-toggle" role="group" aria-label="Mode" data-testid="mode-toggle">
        {(['desktop', 'tablet'] as Mode[]).map((m) => (
          <button key={m} type="button" className="mode-option" aria-pressed={mode === m} data-mode={m} onClick={() => setMode(m)}>
            {m === 'desktop' ? 'Desktop' : 'Tablet'}
          </button>
        ))}
      </div>
      <LinkStatusButton store={store} open={debugOpen} onToggle={onToggleDebug} />
    </header>
  )
}
