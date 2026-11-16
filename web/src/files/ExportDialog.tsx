import { useState } from 'react'
import { ChoiceRow, DesktopChoiceRow, DesktopNumberRow, InfoRow, NumberRow } from '../dialog/fields'
import type { RowKeys } from '../dialog/fields'
import type { FieldSpec } from '../dialog/dialogApi'
import { ApiError } from '../net/api'
import { evaluateNumber, formatNumber } from '../pad/editor'
import { DialogArea } from '../shell/DialogArea'
import { Icon, paths } from '../shell/icons'
import { useMode } from '../shell/mode'
import { ANGLE_RANGE_DEG, DEFL_RANGE_MM, EXPORT_PRESETS, PRESET_VALUES, exportTab } from './filesApi'
import type { ExportFormat, ExportOptions, ExportPreset, ExportResult } from './filesApi'

/** Where the last choices are kept, per device. */
export const EXPORT_KEY = 'freeshape.export'

/** What the dialog remembers: the format and the STL options. A part choice is not kept (it follows the selection). */
export interface ExportPrefs {
  format: ExportFormat
  preset: ExportPreset
  /** The Custom resolution as typed: deviation (mm) and angle (degrees). */
  defl: string
  angle: string
  ascii: boolean
}

const FINE = PRESET_VALUES.fine
export const DEFAULT_PREFS: ExportPrefs = { format: 'step', preset: 'fine', defl: formatNumber(FINE.defl), angle: formatNumber(FINE.angle), ascii: false }

export function loadPrefs(): ExportPrefs {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(EXPORT_KEY) ?? 'null')
    if (typeof raw !== 'object' || raw === null) return DEFAULT_PREFS
    const r = raw as Record<string, unknown>
    return {
      format: r.format === 'stl' ? 'stl' : 'step',
      preset: EXPORT_PRESETS.some(([id]) => id === r.preset) ? (r.preset as ExportPreset) : DEFAULT_PREFS.preset,
      defl: typeof r.defl === 'string' ? r.defl : DEFAULT_PREFS.defl,
      angle: typeof r.angle === 'string' ? r.angle : DEFAULT_PREFS.angle,
      ascii: r.ascii === true,
    }
  } catch {
    return DEFAULT_PREFS
  }
}

export function savePrefs(p: ExportPrefs): void {
  try {
    window.localStorage.setItem(EXPORT_KEY, JSON.stringify(p))
  } catch {
    // private browsing or a full store: the choices are only not kept
  }
}

const choice = (key: string, label: string, options: Array<[string, string]>): FieldSpec => ({ key, kind: 'choice', standard: true, visible: true, label, options })
const FORMAT = choice('format', 'Format', [
  ['step', 'STEP'],
  ['stl', 'STL'],
])
const RESOLUTION = choice('preset', 'Resolution', EXPORT_PRESETS.map(([id, label]): [string, string] => [id, label]))
const ENCODING = choice('encoding', 'Encoding', [
  ['binary', 'Binary'],
  ['ascii', 'ASCII'],
])
const DEFL: FieldSpec = { key: 'defl', kind: 'num', standard: true, visible: true, label: 'Deviation', unit: 'mm' }
const ANGLE: FieldSpec = { key: 'angle', kind: 'num', standard: true, visible: true, label: 'Angle', unit: '°' }

/** A Custom number's value in `unit`, or why the text is no good. */
function readNumber(text: string, range: readonly [number, number], what: string, unit: string): { ok: true; value: number } | { ok: false; error: string } {
  const r = evaluateNumber(text)
  if (!r.ok) return { ok: false, error: `${what}: ${r.error}` }
  if (r.value < range[0] || r.value > range[1]) return { ok: false, error: `${what} must be between ${formatNumber(range[0])} and ${formatNumber(range[1], 2)} ${unit}.` }
  return { ok: true, value: r.value }
}

export interface ExportDialogProps {
  documentId: string
  tab: string
  tabKind: 'studio' | 'assembly'
  /** The format the menu item names; absent, the one used last. */
  format?: ExportFormat
  /** The parts selected in the Part Studio when the dialog opened (an Assembly has none to choose). */
  selected: ReadonlyArray<{ id: string; name: string }>
  /** How many parts the Part Studio has. */
  partCount: number
  /** The dialog is done: the export that was made, or null when it was cancelled. */
  onDone: (result: ExportResult | null) => void
}

/**
 * Export STEP or STL (card I11.9): a feature-style dialog in the dialog area, floating in Desktop mode (typed fields) and over
 * the left column in Tablet mode (numbers on the pad). Export downloads the tab as it is now; the last choices are kept in
 * localStorage under `freeshape.export`.
 */
export function ExportDialog({ documentId, tab, tabKind, format, selected, partCount, onDone }: ExportDialogProps) {
  const desktop = useMode() === 'desktop'
  const [prefs, setPrefs] = useState<ExportPrefs>(() => {
    const p = loadPrefs()
    return format === undefined ? p : { ...p, format }
  })
  const [scope, setScope] = useState<'selected' | 'all'>('selected')
  const [padKey, setPadKey] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const set = (change: Partial<ExportPrefs>) => {
    setFailure(null)
    setPrefs((p) => ({ ...p, ...change }))
  }

  const stl = prefs.format === 'stl'
  const custom = stl && prefs.preset === 'custom'
  const defl = readNumber(prefs.defl, DEFL_RANGE_MM, 'Deviation', 'mm')
  const angle = readNumber(prefs.angle, ANGLE_RANGE_DEG, 'Angle', '°')
  const canExport = !busy && (!custom || (defl.ok && angle.ok))

  const run = async () => {
    if (!canExport) return
    const parts = tabKind === 'studio' && scope === 'selected' ? selected.map((s) => s.id) : []
    const options: ExportOptions = { format: prefs.format, parts }
    if (stl) {
      options.preset = prefs.preset
      options.ascii = prefs.ascii
      if (custom && defl.ok && angle.ok) {
        options.defl = defl.value
        options.angleDeg = angle.value
      }
    }
    setBusy(true)
    setFailure(null)
    try {
      const result = await exportTab(documentId, tab, options, { kind: 'download' })
      savePrefs(prefs)
      onDone(result)
    } catch (e) {
      setBusy(false)
      setFailure(e instanceof ApiError || e instanceof Error ? e.message : 'The export failed.')
    }
  }
  const keys: RowKeys = { onEnter: () => void run(), onEscape: () => onDone(null) }

  const actions = (
    <>
      <button type="button" className="dlg-action" aria-label="Cancel" onClick={() => onDone(null)}>
        <Icon d={paths.close} />
      </button>
      <button type="button" className="dlg-action dlg-accept" aria-label="Export" aria-busy={busy} disabled={!canExport} onClick={() => void run()}>
        <Icon d={paths.check} sw={2.2} />
      </button>
    </>
  )

  const choiceRow = (spec: FieldSpec, value: string, onPick: (v: string) => void, error: string | null = null) =>
    desktop ? <DesktopChoiceRow spec={spec} value={value} error={error} keys={keys} onPick={onPick} /> : <ChoiceRow spec={spec} value={value} error={error} onPick={onPick} />
  const numberRow = (spec: FieldSpec, text: string, error: string | null, onCommit: (t: string) => void) => {
    const value = { value: null, text }
    if (desktop && padKey !== spec.key) return <DesktopNumberRow spec={spec} value={value} sent={undefined} error={error} variables={[]} keys={keys} onCommit={onCommit} onPad={() => setPadKey(spec.key)} />
    return (
      <NumberRow spec={spec} value={value} sent={undefined} error={error} info={null} editing={padKey === spec.key} variables={[]} flip={null} onEdit={() => setPadKey(spec.key)} onCommit={onCommit} onDone={() => setPadKey(null)} />
    )
  }

  const studio = tabKind === 'studio'
  const names = selected.map((s) => s.name).join(', ')
  return (
    <DialogArea title="Export" onClose={() => onDone(null)} actions={actions}>
      <div className="dlg-notices">
        {failure !== null && (
          <div className="dlg-banner" role="alert" data-testid="export-error">
            {failure}
          </div>
        )}
      </div>
      <div className="dlg-form" data-testid="export-form" data-dlg-fields="" data-desktop={desktop ? '' : undefined}>
        {choiceRow(FORMAT, prefs.format, (v) => set({ format: v === 'stl' ? 'stl' : 'step' }))}
        {studio &&
          (selected.length > 0 ? (
            choiceRow(
              choice('parts', 'Parts', [
                ['selected', `Selected (${selected.length})`],
                ['all', `All (${partCount})`],
              ]),
              scope,
              (v) => setScope(v === 'all' ? 'all' : 'selected'),
            )
          ) : (
            <InfoRow text={`All parts (${partCount})`} />
          ))}
        {studio && selected.length > 0 && scope === 'selected' && <InfoRow text={names} />}
        {stl && choiceRow(RESOLUTION, prefs.preset, (v) => set({ preset: v as ExportPreset, ...(v !== 'custom' ? {} : presetText(prefs.preset)) }))}
        {custom && numberRow(DEFL, prefs.defl, defl.ok ? null : defl.error, (t) => set({ defl: t }))}
        {custom && numberRow(ANGLE, prefs.angle, angle.ok ? null : angle.error, (t) => set({ angle: t }))}
        {stl && choiceRow(ENCODING, prefs.ascii ? 'ascii' : 'binary', (v) => set({ ascii: v === 'ascii' }))}
      </div>
    </DialogArea>
  )
}

/** The Custom fields start from the preset that was on. */
function presetText(from: ExportPreset): Partial<ExportPrefs> {
  if (from === 'custom') return {}
  const v = PRESET_VALUES[from]
  return { defl: formatNumber(v.defl), angle: formatNumber(v.angle) }
}
