import { useConnection } from '../net/react'
import type { ConnectionStore } from '../net/connection'
import { Icon, paths } from './icons'

interface Props {
  store: ConnectionStore
  collapsed: boolean
  /** Dialog open: the dialog covers the panel, so collapsing it is off. */
  locked: boolean
  currentTab: string | null
  onCollapsedChange: (collapsed: boolean) => void
}

/**
 * The feature panel frame and its 56 px rail. The body lists the tab names until the feature list (S3) fills it.
 */
export function FeaturePanel({ store, collapsed, locked, currentTab, onCollapsedChange }: Props) {
  const { tabs } = useConnection(store)
  if (collapsed) {
    return (
      <div className="panel-rail" data-testid="panel-rail">
        <button type="button" className="icon-button" aria-label="Expand feature panel" onClick={() => onCollapsedChange(false)}>
          <Icon d={paths.expand} />
        </button>
      </div>
    )
  }
  return (
    <section className="panel" data-testid="feature-panel" aria-label="Feature panel">
      <div className="panel-header">
        <div className="panel-title">Features</div>
        <button
          type="button"
          className="icon-button"
          aria-label="Collapse feature panel"
          disabled={locked}
          onClick={() => onCollapsedChange(true)}
        >
          <Icon d={paths.collapse} />
        </button>
      </div>
      <ul className="panel-body">
        {tabs.map((t) => (
          <li key={t.id} className="panel-row" aria-current={t.id === currentTab ? 'true' : undefined}>
            <Icon d={paths.tab} px={22} stroke="var(--c-inkSoft)" />
            <span>{t.name}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
