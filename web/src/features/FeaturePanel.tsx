import { Icon, paths } from '../shell/icons'
import { size } from '../theme/tokens'
import type { FeatureEntry, StudioOutputs } from '../wire'
import { featureIcon } from './featureIcons'
import { FeatureList, rowTitle } from './FeatureList'
import { PartsList } from './PartsList'
import type { FeatureEdits } from './useFeatureEdits'
import './features.css'

interface Props {
  collapsed: boolean
  /** Dialog open: the dialog covers the panel, so collapsing it is off. */
  locked: boolean
  /** The current tab's kind ('studio', 'assembly', ...), or null when there is no tab. */
  tabKind: string | null
  /** The current Part Studio's outputs, or null while they load (or for a tab that is not a Part Studio). */
  outputs: StudioOutputs | null
  selected: string | null
  /** The part selected in the Parts list (a part id), or null. */
  selectedPart: string | null
  edits: FeatureEdits
  onSelect: (id: string) => void
  onSelectPart: (id: string) => void
  onCollapsedChange: (collapsed: boolean) => void
}

/** One icon of the rail. A tap selects it; the actions are in the expanded panel. */
function RailButton({ feature, selected, rolledBack, onSelect }: { feature: FeatureEntry; selected: boolean; rolledBack: boolean; onSelect: () => void }) {
  const state = feature.error !== null ? 'failed' : feature.suppressed ? 'suppressed' : rolledBack ? 'rolled-back' : undefined
  return (
    <button
      type="button"
      className="icon-button rail-feature"
      aria-label={rowTitle(feature)}
      aria-current={selected ? 'true' : undefined}
      data-feature={feature.id}
      data-state={state}
      onClick={onSelect}
    >
      <Icon d={featureIcon(feature.type)} px={size.iconSmall} />
    </button>
  )
}

/** What the body says when there is no feature list to draw. */
function emptyText(tabKind: string | null, outputs: StudioOutputs | null): string | null {
  if (tabKind === null) return ''
  if (tabKind !== 'studio') return 'This tab has no feature list.'
  if (outputs === null) return 'Loading the features'
  if (!outputs.hasTree) return 'No feature list. This server is older than the client.'
  return null
}

/**
 * The feature panel and its 56 px rail. The body is the open Part Studio's feature list and, under it in the same
 * scrolling body, its Parts list.
 */
export function FeaturePanel({ collapsed, locked, tabKind, outputs, selected, selectedPart, edits, onSelect, onSelectPart, onCollapsedChange }: Props) {
  const empty = emptyText(tabKind, outputs)
  const list = empty === null ? outputs : null
  if (collapsed) {
    return (
      <div className="panel-rail" data-testid="panel-rail">
        <button type="button" className="icon-button" aria-label="Expand feature panel" onClick={() => onCollapsedChange(false)}>
          <Icon d={paths.expand} />
        </button>
        {list && list.features.length > 0 && <div className="rail-divider" />}
        {list?.features.map((f, i) => (
          <RailButton key={f.id} feature={f} selected={f.id === selected} rolledBack={i >= list.rollback} onSelect={() => onSelect(f.id)} />
        ))}
      </div>
    )
  }
  return (
    <section className="panel" data-testid="feature-panel" aria-label="Feature panel" data-selected-feature={selected ?? undefined} data-selected-part={selectedPart ?? undefined}>
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
      <div className="panel-body" data-testid="panel-body">
        {list ? <FeatureList outputs={list} selected={selected} edits={edits} onSelect={onSelect} /> : empty && <p className="panel-empty">{empty}</p>}
        <div className="panel-parts-slot" data-testid="parts-slot">
          {list && <PartsList outputs={list} selected={selectedPart} edits={edits} onSelect={onSelectPart} />}
        </div>
      </div>
    </section>
  )
}
