import { useEffect, useMemo, useRef, useState } from 'react'
import type { ConnectionStore } from '../net/connection'
import { fetchOutputsBytes } from '../net/api'
import { cssColor } from '../features/partPalette'
import { Icon, paths } from '../shell/icons'
import { useMode } from '../shell/mode'
import { size } from '../theme/tokens'
import { decodeOutputs, isStudio } from '../wire'
import '../features/features.css'
import './insert.css'

/** One part a Part Studio shows, as the picker lists it: the id the engine names it by, its name and its colour (r g b, 0..1). */
export interface PickerPart {
  id: string
  name: string
  color: readonly number[]
}

/** A Part Studio tab of the Document and its parts; `parts` is null while its outputs are on their way. */
export interface PickerStudio {
  tab: string
  name: string
  parts: readonly PickerPart[] | null
}

/** What a row stands for: one part of a Part Studio tab, or (`part` null) the whole Part Studio. */
export interface PickerChoice {
  tab: string
  part: string | null
}

export const sameChoice = (a: PickerChoice | null, b: PickerChoice): boolean => a !== null && a.tab === b.tab && a.part === b.part

interface Props {
  studios: readonly PickerStudio[]
  /** The row that is selected, or null. */
  choice: PickerChoice | null
  onChoose: (choice: PickerChoice) => void
  /** A row was double-clicked (Desktop mode only: a tap only selects). */
  onActivate?: (choice: PickerChoice) => void
  /** A label for the list, for screen readers. */
  label?: string
}

/**
 * The Part Studios of this Document with their parts, one swatch and name per part (card I10.1; the Derived feature's source
 * picker, I10.2, reuses it). A Part Studio row stands for all its parts; a Part Studio with none says "(no parts yet)".
 * Desktop mode: a click selects, a double-click calls `onActivate`. Tablet mode: a tap selects.
 */
export function PartPicker({ studios, choice, onChoose, onActivate, label = 'Part Studios' }: Props) {
  const desktop = useMode() === 'desktop'
  const row = (c: PickerChoice) => ({
    'aria-current': sameChoice(choice, c) ? ('true' as const) : undefined,
    onClick: () => onChoose(c),
    onDoubleClick: desktop && onActivate ? () => onActivate(c) : undefined,
  })
  if (studios.length === 0) return <p className="panel-empty picker-empty">This Document has no Part Studio.</p>
  return (
    <ul className="parts-list picker" aria-label={label} data-testid="part-picker">
      {studios.map((s) => (
        <li key={s.tab} className="picker-studio" data-picker-studio-item={s.tab}>
          <button type="button" className="panel-row picker-studio-row" data-picker-studio={s.tab} title={`All the parts of ${s.name}`} {...row({ tab: s.tab, part: null })}>
            <Icon d={paths.cube} px={size.iconSmall} stroke="var(--c-inkSoft)" />
            <span className="row-text">
              <span className="row-name">{s.name}</span>
            </span>
          </button>
          {s.parts === null ? (
            <div className="picker-note" data-picker-loading={s.tab}>
              Loading…
            </div>
          ) : s.parts.length === 0 ? (
            <div className="picker-note" data-picker-empty={s.tab}>
              (no parts yet)
            </div>
          ) : (
            s.parts.map((p) => (
              <button key={p.id} type="button" className="panel-row" data-indent="" data-picker-part={p.id} data-picker-tab={s.tab} {...row({ tab: s.tab, part: p.id })}>
                <span className="part-swatch" style={{ background: cssColor(p.color) }} />
                <span className="row-text">
                  <span className="row-name">{p.name}</span>
                </span>
              </button>
            ))
          )}
        </li>
      ))}
    </ul>
  )
}

/**
 * The parts of each Part Studio tab in `tabs` (a tab of another kind is skipped), read from the tab's outputs and read again when
 * its revision rises. A tab whose outputs have not arrived has `parts` null; one that cannot be read has none. The list follows
 * the tabs' order.
 */
export function useStudioParts(store: ConnectionStore, documentId: string, tabs: readonly { id: string; kind: string; name: string }[]): PickerStudio[] {
  const studios = useMemo(() => tabs.filter((t) => t.kind === 'studio').map((t) => ({ id: t.id, name: t.name })), [tabs])
  const key = studios.map((t) => t.id).join('\n')
  const [loaded, setLoaded] = useState<Record<string, readonly PickerPart[]>>({})
  const latest = useRef(0)
  useEffect(() => {
    const ids = key === '' ? [] : key.split('\n')
    const ctl = new AbortController()
    const load = (tab: string): void => {
      const turn = ++latest.current
      void fetchOutputsBytes(documentId, tab, ctl.signal).then(
        (bytes) => {
          const out = decodeOutputs(bytes)
          if (ctl.signal.aborted || !isStudio(out) || turn < (seen.get(tab) ?? 0)) return
          seen.set(tab, turn)
          setLoaded((l) => ({ ...l, [tab]: out.parts.map((p) => ({ id: p.id, name: p.name, color: p.color })) }))
        },
        () => {
          if (!ctl.signal.aborted) setLoaded((l) => (tab in l ? l : { ...l, [tab]: [] }))
        },
      )
    }
    const seen = new Map<string, number>()
    const offs = ids.map((tab) => store.onRevision(tab, () => load(tab)))
    ids.forEach(load)
    return () => {
      ctl.abort()
      offs.forEach((off) => off())
    }
  }, [store, documentId, key])
  return studios.map((t) => ({ tab: t.id, name: t.name, parts: loaded[t.id] ?? null }))
}
