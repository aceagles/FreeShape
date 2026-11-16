import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChoiceRow, DesktopChoiceRow, DesktopNumberRow, InfoRow, NumberRow } from '../dialog/fields'
import type { RowKeys } from '../dialog/fields'
import type { FieldSpec } from '../dialog/dialogApi'
import { ApiError } from '../net/api'
import { evaluateNumber, formatNumber } from '../pad/editor'
import { DialogArea } from '../shell/DialogArea'
import { ConfirmDialog } from '../shell/Modal'
import { TextPrompt } from '../shell/TextPrompt'
import { Icon, paths } from '../shell/icons'
import { useMode } from '../shell/mode'
import { ANGLE_RANGE_DEG, DEFL_RANGE_MM, EXPORT_PRESETS, PRESET_VALUES, existingName, exportTab, fetchExportRoots } from './filesApi'
import type { ExportDestination, ExportFormat, ExportOptions, ExportPreset, ExportResult, ExportRoot } from './filesApi'
import './files.css'

/** Where the last choices are kept, per device. */
export const EXPORT_KEY = 'freeshape.export'

/** The Destination value of a download; any other value is an export root's name. */
export const DOWNLOAD = 'download'

/** What the dialog remembers: the format, the STL options and the destination. A part choice is not kept (it follows the selection). */
export interface ExportPrefs {
  format: ExportFormat
  preset: ExportPreset
  /** The Custom resolution as typed: deviation (mm) and angle (degrees). */
  defl: string
  angle: string
  ascii: boolean
  /** `download` or an export root's name; a root the server no longer lists is not used (the dialog falls back to Download). */
  dest: string
}

const FINE = PRESET_VALUES.fine
export const DEFAULT_PREFS: ExportPrefs = {
  format: 'step',
  preset: 'fine',
  defl: formatNumber(FINE.defl),
  angle: formatNumber(FINE.angle),
  ascii: false,
  dest: DOWNLOAD,
}

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
      dest: typeof r.dest === 'string' && r.dest !== '' ? r.dest : DOWNLOAD,
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

const choice = (key: string, label: string, options: Array<[string, string]>): FieldSpec => ({
  key,
  kind: 'choice',
  standard: true,
  visible: true,
  label,
  options,
})
const FORMAT = choice('format', 'Format', [
  ['step', 'STEP'],
  ['stl', 'STL'],
])
const RESOLUTION = choice(
  'preset',
  'Resolution',
  EXPORT_PRESETS.map(([id, label]): [string, string] => [id, label]),
)
const ENCODING = choice('encoding', 'Encoding', [
  ['binary', 'Binary'],
  ['ascii', 'ASCII'],
])
const NAME: FieldSpec = {
  key: 'name',
  kind: 'text',
  standard: true,
  visible: true,
  label: 'File name',
}
const DEFL: FieldSpec = {
  key: 'defl',
  kind: 'num',
  standard: true,
  visible: true,
  label: 'Deviation',
  unit: 'mm',
}
const ANGLE: FieldSpec = {
  key: 'angle',
  kind: 'num',
  standard: true,
  visible: true,
  label: 'Angle',
  unit: '°',
}

/** A Custom number's value in `unit`, or why the text is no good. */
function readNumber(text: string, range: readonly [number, number], what: string, unit: string): { ok: true; value: number } | { ok: false; error: string } {
  const r = evaluateNumber(text)
  if (!r.ok) return { ok: false, error: `${what}: ${r.error}` }
  if (r.value < range[0] || r.value > range[1])
    return {
      ok: false,
      error: `${what} must be between ${formatNumber(range[0])} and ${formatNumber(range[1], 2)} ${unit}.`,
    }
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
  /** The tab's name: the file name a save starts from. */
  tabName: string
  /** How many parts the Part Studio has. */
  partCount: number
  /** The dialog is done: the export that was made, or null when it was cancelled. */
  onDone: (result: ExportResult | null) => void
}

/**
 * Export STEP or STL (card I11.9): a feature-style dialog in the dialog area, floating in Desktop mode (typed fields) and over
 * the left column in Tablet mode (numbers on the pad). Export downloads the tab as it is now; the last choices are kept in
 * localStorage under `freeshape.export`. When the server lists export roots (I11.14) a Destination row offers each beside Download,
 * and a save posts the export to the server folder under the File name given; a name already there asks before it replaces it.
 */
export function ExportDialog({ documentId, tab, tabKind, format, selected, tabName, partCount, onDone }: ExportDialogProps) {
  const desktop = useMode() === 'desktop'
  const [prefs, setPrefs] = useState<ExportPrefs>(() => {
    const p = loadPrefs()
    return format === undefined ? p : { ...p, format }
  })
  const [scope, setScope] = useState<'selected' | 'all'>('selected')
  const [padKey, setPadKey] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [roots, setRoots] = useState<ExportRoot[]>([])
  const [typedName, setTypedName] = useState<string | null>(null)
  const [naming, setNaming] = useState(false)
  const [replacing, setReplacing] = useState<{ name: string } | null>(null)
  useEffect(() => {
    let live = true
    void fetchExportRoots().then((r) => live && setRoots(r))
    return () => {
      live = false
    }
  }, [])
  const set = (change: Partial<ExportPrefs>) => {
    setFailure(null)
    setPrefs((p) => ({ ...p, ...change }))
  }

  const stl = prefs.format === 'stl'
  const custom = stl && prefs.preset === 'custom'
  const defl = readNumber(prefs.defl, DEFL_RANGE_MM, 'Deviation', 'mm')
  const angle = readNumber(prefs.angle, ANGLE_RANGE_DEG, 'Angle', '°')

  const dest = roots.some((r) => r.name === prefs.dest) ? prefs.dest : DOWNLOAD
  const saving = dest !== DOWNLOAD
  const stem = typedName ?? (tabKind === 'studio' && scope === 'selected' && selected.length === 1 ? selected[0]!.name : tabName)
  const fileName = stem.replace(new RegExp(`\\.${prefs.format}$`, 'i'), '').trim()
  const canExport = !busy && (!custom || (defl.ok && angle.ok)) && (!saving || fileName !== '')

  const run = async (replace = false) => {
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
      const to: ExportDestination = saving
        ? {
            kind: 'root',
            root: dest,
            name: fileName,
            ...(replace ? { replace: true } : {}),
          }
        : { kind: 'download' }
      const result = await exportTab(documentId, tab, options, to)
      savePrefs({ ...prefs, dest })
      onDone(result)
    } catch (e) {
      setBusy(false)
      const taken = existingName(e)
      if (taken !== null) {
        setReplacing({
          name: taken === '' ? `${fileName}.${prefs.format}` : taken,
        })
        return
      }
      setFailure(e instanceof ApiError || e instanceof Error ? e.message : 'The export failed.')
    }
  }
  const keys: RowKeys = {
    onEnter: () => void run(),
    onEscape: () => onDone(null),
  }

  const actions = (
    <>
      <button type="button" className="dlg-action" aria-label="Cancel" onClick={() => onDone(null)}>
        <Icon d={paths.close} />
      </button>
      <button
        type="button"
        className="dlg-action dlg-accept"
        aria-label={busy ? (saving ? `Saving to ${dest}` : 'Exporting') : saving ? `Save to ${dest}` : 'Export'}
        title={saving ? `Save to ${dest}` : 'Export'}
        aria-busy={busy}
        disabled={!canExport}
        onClick={() => void run()}
      >
        <Icon d={paths.check} sw={2.2} />
      </button>
    </>
  )

  const choiceRow = (spec: FieldSpec, value: string, onPick: (v: string) => void, error: string | null = null) =>
    desktop ? <DesktopChoiceRow spec={spec} value={value} error={error} keys={keys} onPick={onPick} /> : <ChoiceRow spec={spec} value={value} error={error} onPick={onPick} />
  const numberRow = (spec: FieldSpec, text: string, error: string | null, onCommit: (t: string) => void) => {
    const value = { value: null, text }
    if (desktop && padKey !== spec.key)
      return <DesktopNumberRow spec={spec} value={value} sent={undefined} error={error} variables={[]} keys={keys} onCommit={onCommit} onPad={() => setPadKey(spec.key)} />
    return (
      <NumberRow
        spec={spec}
        value={value}
        sent={undefined}
        error={error}
        info={null}
        editing={padKey === spec.key}
        variables={[]}
        flip={null}
        onEdit={() => setPadKey(spec.key)}
        onCommit={onCommit}
        onDone={() => setPadKey(null)}
      />
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
        {busy && (
          <p className="dlg-status" role="status" data-testid="export-working">
            {saving ? `Saving to ${dest}…` : 'Exporting…'}
          </p>
        )}
      </div>
      <div className="dlg-form" data-testid="export-form" data-dlg-fields="" data-desktop={desktop ? '' : undefined}>
        {choiceRow(FORMAT, prefs.format, (v) => set({ format: v === 'stl' ? 'stl' : 'step' }))}
        {roots.length > 0 && choiceRow(choice('dest', 'Destination', [[DOWNLOAD, 'Download'], ...roots.map((r): [string, string] => [r.name, `Save to ${r.name}`])]), dest, (v) => set({ dest: v }))}
        {saving &&
          nameRow(
            NAME,
            desktop ? stem : fileName,
            `.${prefs.format}`,
            desktop,
            () => setNaming(true),
            (t) => {
              setFailure(null)
              setTypedName(t)
            },
            keys,
          )}
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
        {stl &&
          choiceRow(RESOLUTION, prefs.preset, (v) =>
            set({
              preset: v as ExportPreset,
              ...(v !== 'custom' ? {} : presetText(prefs.preset)),
            }),
          )}
        {custom && numberRow(DEFL, prefs.defl, defl.ok ? null : defl.error, (t) => set({ defl: t }))}
        {custom && numberRow(ANGLE, prefs.angle, angle.ok ? null : angle.error, (t) => set({ angle: t }))}
        {stl && choiceRow(ENCODING, prefs.ascii ? 'ascii' : 'binary', (v) => set({ ascii: v === 'ascii' }))}
      </div>
      {naming &&
        createPortal(
          <TextPrompt
            title="File name"
            label="File name"
            initial={fileName}
            confirmLabel="OK"
            onSubmit={(v) => {
              setFailure(null)
              setTypedName(v)
              setNaming(false)
            }}
            onCancel={() => setNaming(false)}
          />,
          overlayHost(),
        )}
      {replacing !== null &&
        createPortal(
          <ConfirmDialog
            title="Replace file?"
            message={`Replace ${replacing.name} in ${dest}?`}
            confirm={{ label: 'Replace' }}
            onConfirm={() => {
              setReplacing(null)
              void run(true)
            }}
            onCancel={() => setReplacing(null)}
          />,
          overlayHost(),
        )}
    </DialogArea>
  )
}

/**
 * The File name row of a save. Desktop mode types the name inline, the format's extension after it (it is not editable);
 * Tablet mode shows the whole file name as a button that opens a text prompt (a name is words, so it never uses the pad).
 */
function nameRow(spec: FieldSpec, name: string, ext: string, desktop: boolean, onOpen: () => void, onCommit: (t: string) => void, keys: RowKeys) {
  if (!desktop) {
    return (
      <div className="dlg-row">
        <div className="dlg-label">{spec.label}</div>
        <button type="button" className="dlg-field dlg-field-choice" aria-label={`${spec.label}, ${name}${ext}`} onClick={onOpen}>
          <span className="export-name">
            {name}
            {ext}
          </span>
          <Icon d={paths.chevronRight} px={20} stroke="var(--c-muted)" />
        </button>
      </div>
    )
  }
  return (
    <div className="dlg-row dlg-row-inline">
      <div className="dlg-row-line">
        <div className="dlg-label">{spec.label}</div>
        <span className="export-name-inline">
          <input
            className="dlg-text-inline"
            type="text"
            aria-label={spec.label}
            data-dlg-field=""
            value={name}
            spellCheck={false}
            onChange={(e) => onCommit(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') keys.onEnter()
              else if (e.key === 'Escape') keys.onEscape()
            }}
          />
          <span className="export-ext" aria-hidden="true">
            {ext}
          </span>
        </span>
      </div>
    </div>
  )
}

/** The Custom fields start from the preset that was on. */
function presetText(from: ExportPreset): Partial<ExportPrefs> {
  if (from === 'custom') return {}
  const v = PRESET_VALUES[from]
  return { defl: formatNumber(v.defl), angle: formatNumber(v.angle) }
}

/** Where the dialogs over the whole page go: the shell's root, so they carry its styles and are not clipped by the dialog area. */
function overlayHost(): HTMLElement {
  return document.querySelector<HTMLElement>('.shell') ?? document.body
}
