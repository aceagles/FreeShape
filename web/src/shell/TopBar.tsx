import type { ConnectionStore } from '../net/connection'
import { useConnection } from '../net/react'
import { Icon, paths } from './icons'
import { statusLabel, useLinkStatus } from './status'

interface Props {
  store: ConnectionStore
  currentTab: string | null
  onSelectTab: (id: string) => void
  debugOpen: boolean
  onToggleDebug: () => void
}

/** The top bar: Document name, tab switcher, back and add tab, undo, redo and save (disabled until their cards), the connection state. */
export function TopBar({ store, currentTab, onSelectTab, debugOpen, onToggleDebug }: Props) {
  const { document, tabs } = useConnection(store)
  const status = useLinkStatus(store)
  return (
    <header className="top-bar" data-region="top-bar">
      <button type="button" className="icon-button" aria-label="Back to Documents" disabled>
        <Icon d={paths.back} />
      </button>
      <div className="top-bar-name" data-testid="document-name">
        {document?.name ?? 'FreeShape'}
      </div>
      <nav className="top-bar-tabs" aria-label="Tabs">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            className="tab-button"
            aria-current={t.id === currentTab ? 'page' : undefined}
            onClick={() => onSelectTab(t.id)}
          >
            {t.name}
          </button>
        ))}
        <button type="button" className="icon-button" aria-label="Add tab" disabled>
          <Icon d={paths.plus} />
        </button>
      </nav>
      <div className="top-bar-gap" />
      <button type="button" className="icon-button" aria-label="Undo" disabled>
        <Icon d={paths.undo} />
      </button>
      <button type="button" className="icon-button" aria-label="Redo" disabled>
        <Icon d={paths.redo} />
      </button>
      <button type="button" className="icon-button" aria-label="Save" disabled>
        <Icon d={paths.save} />
      </button>
      <button
        type="button"
        className="link-status"
        data-status={status}
        data-testid="link-status"
        aria-label={`${statusLabel[status]}, connection details`}
        aria-expanded={debugOpen}
        onClick={onToggleDebug}
      >
        <span className="link-dot" />
        <span>{statusLabel[status]}</span>
      </button>
    </header>
  )
}
