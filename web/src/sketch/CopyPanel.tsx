import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { FieldSpec } from '../dialog/dialogApi'
import { DesktopNumberRow } from '../dialog/fields'
import type { RowKeys } from '../dialog/fields'
import { NumberPad, PadField } from '../pad/NumberPad'
import { usePadEditor } from '../pad/usePadEditor'
import { Icon, paths } from '../shell/icons'
import { useMode } from '../shell/mode'
import type { ShellApi } from '../shell/ShellContext'
import { PATTERN_PANEL } from './tools/copy'
import type { CopyTools, PatternPanel, PatternRow, PatternRowKey } from './tools/copy'
import '../dialog/dialog.css'
import './sketch.css'

/*
 * The linear and circular pattern's numbers (card I14.3), in one small panel in the dialog area, opened as the corner
 * fillet's radius is (EditPad.tsx). The desktop asks for them in two inline boxes one after the other (Sketcher._prompt);
 * here both rows are shown at once. Tablet mode: each row opens the on-screen pad, as the feature dialog's number rows do;
 * every key redraws the ghost. Desktop mode: I9.16's typed fields, one data-dlg-fields group (Tab moves between them),
 * Enter in Instances moves to the second row and Enter there accepts; a pen tap opens the pad.
 */

interface RowLook {
  key: PatternRowKey
  label: string
  unit: string | null
}

const PLAIN_NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)$/

/** A Tablet row, closed: its label and the value as a button that opens the pad. */
function TabletRow({ look, row, onEdit }: { look: RowLook; row: PatternRow; onEdit: () => void }) {
  const t = row.text.trim()
  const shown = look.unit !== null && PLAIN_NUMBER.test(t) ? `${t} ${look.unit}` : t
  return (
    <div className="dlg-row" data-testid={`copy-row-${look.key}`}>
      <div className="dlg-label">{look.label}</div>
      <div className="dlg-number">
        <button type="button" className="dlg-field" data-invalid={row.error !== null ? '' : undefined} aria-label={`${look.label}, ${shown}. Tap to edit on the pad`} onClick={onEdit}>
          <span>{shown}</span>
        </button>
      </div>
      {row.error !== null && (
        <div className="dlg-message" role="alert">
          {row.error}
        </div>
      )}
    </div>
  )
}

/** A row on the pad: every key is a change of the row (the ghost follows); Enter closes the pad, Cancel puts the opening text back. */
function PadRow({ look, row, tool, variables, onDone }: { look: RowLook; row: PatternRow; tool: CopyTools; variables: readonly string[]; onDone: () => void }) {
  const [opening] = useState(row.text)
  const editor = usePadEditor({
    value: opening,
    variables,
    onChange: (text) => tool.edit(look.key, text),
    onCommit: onDone,
    onCancel: () => {
      tool.edit(look.key, opening)
      onDone()
    },
  })
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    box.current?.scrollIntoView?.({ block: 'nearest' })
  }, [])
  return (
    <div className="dlg-row" data-testid={`copy-row-${look.key}`}>
      <div ref={box} className="dlg-number">
        <div className="dlg-number-field">
          <PadField editor={editor} label={look.unit !== null ? `${look.label} (${look.unit})` : look.label} />
        </div>
        <NumberPad editor={editor} />
      </div>
      {row.error !== null && (
        <div className="dlg-message" role="alert">
          {row.error}
        </div>
      )}
    </div>
  )
}

function PanelRows({ tool, panel, variables }: { tool: CopyTools; panel: PatternPanel; variables: readonly string[] }) {
  const desktop = useMode() === 'desktop'
  // the row on the pad: Tablet mode's tapped row (Instances when the panel opens, as a feature dialog opens on its first
  // number), or a Desktop row a pen or a finger pressed
  const [padKey, setPadKey] = useState<PatternRowKey | null>(desktop ? null : 'count')
  const form = useRef<HTMLDivElement>(null)
  const look = PATTERN_PANEL[panel.tool]
  const rows: RowLook[] = [
    { key: 'count', label: 'Instances', unit: null },
    { key: 'second', label: look.second, unit: look.unit },
  ]
  const field = (key: PatternRowKey) => form.current?.querySelector<HTMLInputElement>(`input[data-field-key="${key}"]`) ?? null
  // Desktop mode: the count is typed first, as on the desktop
  useEffect(() => {
    if (!desktop) return
    const el = field('count')
    el?.focus()
    el?.select()
  }, [desktop])
  const keysOf = (key: PatternRowKey): RowKeys => ({
    // the desktop's keystrokes: count, Enter, spacing, Enter
    onEnter: () => {
      if (key === 'second') {
        tool.accept()
        return
      }
      if (tool.getState().panel?.count.error !== null) return
      const next = field('second')
      next?.focus()
      next?.select()
    },
    onEscape: tool.cancel,
  })
  return (
    <div ref={form} className="dlg-form copy-panel" data-testid="copy-panel" data-dlg-fields="" data-desktop={desktop ? '' : undefined}>
      {rows.map((r) => {
        const row = panel[r.key]
        if (padKey === r.key) return <PadRow key={r.key} look={r} row={row} tool={tool} variables={variables} onDone={() => setPadKey(null)} />
        if (desktop) {
          const spec: FieldSpec = { key: r.key, kind: 'num', standard: true, visible: true, label: r.label, unit: r.unit }
          return (
            <DesktopNumberRow
              key={r.key}
              spec={spec}
              value={{ value: undefined, text: row.text }}
              sent={undefined}
              error={row.error}
              variables={variables}
              keys={keysOf(r.key)}
              onCommit={(text) => tool.edit(r.key, text)}
              onPad={() => setPadKey(r.key)}
            />
          )
        }
        return <TabletRow key={r.key} look={r} row={row} onEdit={() => setPadKey(r.key)} />
      })}
      {panel.refusal !== null && (
        <div className="dlg-message copy-refusal" role="alert" data-testid="copy-refusal">
          {panel.refusal}
        </div>
      )}
    </div>
  )
}

/** The panel's body: the two rows and the server's refusal under them. */
export function CopyPanel({ tool, variables }: { tool: CopyTools; variables: readonly string[] }) {
  const { panel } = useSyncExternalStore(tool.subscribe, tool.getState)
  if (panel === null) return null
  return <PanelRows key={panel.seq} tool={tool} panel={panel} variables={variables} />
}

/** Cancel and Accept in the dialog header, as a feature dialog has them. */
export function CopyActions({ tool }: { tool: CopyTools }) {
  const state = useSyncExternalStore(tool.subscribe, tool.getState)
  if (state.panel === null) return null
  return (
    <>
      <button type="button" className="dlg-action" aria-label="Cancel" disabled={state.panel.pending} onClick={tool.cancel}>
        <Icon d={paths.close} />
      </button>
      <button type="button" className="dlg-action dlg-accept" aria-label="Accept" disabled={!tool.canAccept()} onClick={() => void tool.accept()}>
        <Icon d={paths.check} sw={2.2} />
      </button>
    </>
  )
}

/** Opens and closes the pattern panel in the shell's dialog area; the shell binds its current API and variables after each render. */
export class CopyDock {
  private api: ShellApi | null = null
  private vars: readonly string[] = []

  bind(api: ShellApi, variables: readonly string[]): void {
    this.api = api
    this.vars = variables
  }
  readonly variables = (): readonly string[] => this.vars
  readonly open = (tool: CopyTools): void => {
    const panel = tool.getState().panel
    if (panel === null) return
    this.api?.openDialog(PATTERN_PANEL[panel.tool].title, <CopyPanel tool={tool} variables={this.vars} />, <CopyActions tool={tool} />)
  }
  readonly close = (): void => this.api?.closeDialog()
}
