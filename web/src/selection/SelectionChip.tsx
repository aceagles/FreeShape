import { useMode } from '../shell/mode'
import { useSelection } from './useSelection'
import type { SelectionStore } from './store'
import { countText } from './store'
import './selection.css'

/**
 * The selection chip, top left of the view (web/design/layout.html, `selection-chip`): a square in the selection colour
 * with what is selected ("1 face", "2 faces, 1 edge", "Nothing selected"), the Multi toggle and Clear (hidden while a dialog's selection field is active: the field's own remove buttons take items out). It is always
 * there, because Multi has to be reachable before the first pick. With nothing selected the square is an empty outline
 * and Clear is dimmed. In Desktop mode there is no Multi: a click adds to the selection there (I9.9).
 */
export function SelectionChip({ store }: { store: SelectionStore }) {
  const { items, multi, field } = useSelection(store)
  const tablet = useMode() === 'tablet'
  const empty = items.length === 0
  return (
    <div className="selection-chip" data-testid="selection-chip" data-empty={empty || undefined}>
      <span className="selection-chip-count">
        <span className="selection-chip-swatch" aria-hidden="true" />
        <span data-testid="selection-count">{countText(items)}</span>
      </span>
      {tablet && (
        <button type="button" className="selection-chip-multi" aria-pressed={multi} data-testid="selection-multi" onClick={() => store.setMulti(!multi)}>
          Multi
        </button>
      )}
      {!field && (
        <button type="button" className="selection-chip-clear" data-testid="selection-clear" disabled={empty} onClick={() => store.clear()}>
          Clear
        </button>
      )}
    </div>
  )
}
