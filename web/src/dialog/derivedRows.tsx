import { useMemo, useState } from 'react'
import { PartPicker, useStudioParts } from '../assembly/PartPicker'
import type { PickerChoice } from '../assembly/PartPicker'
import type { ConnectionStore } from '../net/connection'
import type { FieldSpec } from './dialogApi'
import { labelOf } from './schema'

/** One tick list of a group: its row, the ids ticked now and the message of its last refused change. */
export interface TickList {
  spec: FieldSpec
  ticked: readonly string[]
  error: string | null
}

/** A tab's title: its label, with how many of its rows are ticked once there are any (the desktop's "Parts (1)"). */
export const tabTitle = (list: TickList): string => `${labelOf(list.spec)}${list.ticked.length > 0 ? ` (${list.ticked.length})` : ''}`

/**
 * The tick lists of one group as the tabs of one row (card I10.2: the Derived's Parts, Sketches and Mate connectors, as the
 * desktop's `_make_derived_tabs`). One list shows at a time; a tab's title counts its ticks, so a hidden list still says what
 * it brings in, and the row opens on the first tab with a tick. A tick sends the list's whole new set of ids; the server keeps
 * them in the list's order and may refuse one (its message shows under the list). Tablet mode: each row is a 44 px tick
 * button. Desktop mode: a checkbox per row, as the desktop's QListWidget.
 */
export function TickTabs({ lists, desktop, empty, onTick }: { lists: readonly TickList[]; desktop: boolean; empty: string; onTick: (key: string, ticked: string[]) => void }) {
  const [chosen, setChosen] = useState<string | null>(() => lists.find((l) => l.ticked.length > 0)?.spec.key ?? null)
  const shown = lists.find((l) => l.spec.key === chosen) ?? lists[0]
  if (shown === undefined) return null
  const key = shown.spec.key ?? ''
  const toggle = (id: string) => {
    const on = shown.ticked.includes(id)
    onTick(key, on ? shown.ticked.filter((x) => x !== id) : [...shown.ticked, id])
  }
  const options = shown.spec.options ?? []
  return (
    <div className="dlg-row dlg-ticks" data-testid="tick-tabs">
      <div className="dlg-tabs" role="tablist" aria-label="What to bring in">
        {lists.map((l) => (
          <button key={l.spec.key} type="button" role="tab" className="dlg-tab" aria-selected={l === shown} data-testid={`tick-tab-${l.spec.key}`} onClick={() => setChosen(l.spec.key)}>
            {tabTitle(l)}
          </button>
        ))}
      </div>
      <div className="dlg-tick-list" role="tabpanel" aria-label={labelOf(shown.spec)} data-testid={`ticks-${key}`}>
        {options.length === 0 && <div className="dlg-tick-empty">{empty}</div>}
        {options.map(([id, text]) =>
          desktop ? (
            <label key={id} className="dlg-check dlg-tick-check">
              <input type="checkbox" data-dlg-field="" data-tick={id} checked={shown.ticked.includes(id)} onChange={() => toggle(id)} />
              <span>{text}</span>
            </label>
          ) : (
            <button key={id} type="button" role="checkbox" aria-checked={shown.ticked.includes(id)} className="dlg-tick" data-tick={id} onClick={() => toggle(id)}>
              <span className="dlg-tick-box" aria-hidden="true" />
              <span className="dlg-tick-name">{text}</span>
            </button>
          ),
        )}
      </div>
      {shown.error && (
        <div className="dlg-message" role="alert">
          {shown.error}
        </div>
      )}
    </div>
  )
}

/**
 * A choice of this Document's Part Studios (`picker: "studio"`), drawn as I10.1's Part Studio list: each Part Studio with its
 * parts under it, read from the tab's outputs. A tap (Desktop: a click) on a Part Studio or on one of its parts chooses that
 * Part Studio. The options the server lists as not choosable here (a source in another Document) are left out; the row's
 * label and its message are the dialog's.
 */
export function StudioChoiceRow({ spec, value, error, store, documentId, onPick }: { spec: FieldSpec; value: unknown; error: string | null; store: ConnectionStore; documentId: string; onPick: (v: string) => void }) {
  const tabs = useMemo(() => (spec.options ?? []).filter(([v]) => v !== '' && spec.disabled?.[v] === undefined).map(([id, name]) => ({ id, kind: 'studio', name })), [spec.options, spec.disabled])
  const studios = useStudioParts(store, documentId, tabs)
  const choice: PickerChoice | null = typeof value === 'string' && value !== '' ? { tab: value, part: null } : null
  const label = labelOf(spec)
  return (
    <div className="dlg-row dlg-studio-choice" data-testid={`studio-${spec.key}`}>
      <div className="dlg-label">{label}</div>
      {studios.length > 0 && <PartPicker studios={studios} choice={choice} label={label} onChoose={(c) => c.tab !== value && onPick(c.tab)} />}
      {error && (
        <div className="dlg-message" role="alert">
          {error}
        </div>
      )}
    </div>
  )
}
