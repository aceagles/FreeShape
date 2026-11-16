import { useState } from 'react'
import type { ConnectionStore } from '../net/connection'
import type { SelectionStore } from '../selection/store'
import type { StudioOutputs } from '../wire'
import { DialogArea } from '../shell/DialogArea'
import { Icon, paths } from '../shell/icons'
import { CheckRow, ChoiceRow, FlipButton, InfoRow, NumberRow, SelectionRow, TextRow } from './fields'
import { visibleRows } from './schema'
import { itemLabel } from './selectionFields'
import { isReadOnlyField, useSelectionFields } from './useSelectionFields'
import { useFeatureDialog } from './useFeatureDialog'
import type { PreviewState } from './useFeatureDialog'
import './dialog.css'

export const DESKTOP_ONLY_TEXT = 'This feature has settings that can only be edited on the desktop. Cancel to close it.'

export interface FeatureDialogProps {
  store: ConnectionStore
  documentId: string
  documentName: string
  tab: string
  feature: { id: string; name: string }
  /** The variable names in scope for the pad's # key, without the "#". */
  variables: readonly string[]
  /** The view's selection: while the dialog is open its picks go to the active selection field (see useSelectionFields). */
  selection: SelectionStore
  /** The model the view draws (the preview while there is one): the names of a field's items are matched against it to highlight them, and a sketch region is named from it. */
  outputs: StudioOutputs | null
  /** The committed model, which is what the refs route resolves a pick against. */
  committed: StudioOutputs | null
  onPreview: (p: PreviewState | null) => void
  onDone: (result: { accepted: boolean; revision: number | null }) => void
}

/**
 * The dialog of one feature, in the dialog area over the feature panel, drawn from the description the server gives:
 * a number is a field that opens the pad, a choice segmented buttons or a list, a check a switch, a selection row shows its items as chips
 * and, once tapped, takes the picks of the view. Cancel and Accept are in the header. A change is sent as one dialog update and the view draws the preview
 * the server pushes back (see useFeatureDialog and the Shell).
 */
export function FeatureDialog({ store, documentId, documentName, tab, feature, variables, selection, outputs, committed, onPreview, onDone }: FeatureDialogProps) {
  const dlg = useFeatureDialog({ store, documentId, documentName, tab, feature: feature.id, onPreview, onDone })
  const sel = useSelectionFields({
    selection,
    outputs,
    committed,
    documentId,
    documentName,
    tab,
    specs: dlg.view?.description.fields ?? null,
    fields: dlg.view?.fields ?? {},
    sent: dlg.sent,
    serverActive: dlg.view?.active ?? null,
    set: dlg.set,
  })
  // the number on the pad: the first number of the dialog opens with it, as in the layout; a tap on another moves it
  const [padKey, setPadKey] = useState<string | null | undefined>(undefined)
  const description = dlg.view?.description ?? null
  const rows = description ? visibleRows(description.fields) : []
  const desktopOnly = description !== null && description.custom.length > 0
  const editing = desktopOnly || padKey === null ? null : (padKey ?? rows.find((r) => r.spec.kind === 'num')?.spec.key ?? null)
  const title = dlg.opened?.name ?? feature.name

  const actions = (
    <>
      <button type="button" className="dlg-action" aria-label="Cancel" onClick={dlg.cancel}>
        <Icon d={paths.close} />
      </button>
      {!desktopOnly && (
        <button type="button" className="dlg-action dlg-accept" aria-label="Accept" disabled={dlg.phase !== 'open' || dlg.accepting} onClick={() => void dlg.accept()}>
          <Icon d={paths.check} sw={2.2} />
        </button>
      )}
    </>
  )

  const values = dlg.view?.values ?? {}
  const messageOf = (key: string | null) => (key === null ? null : (dlg.errors[key] ?? values[key]?.error ?? null))

  return (
    <DialogArea title={title} onClose={dlg.cancel} actions={actions}>
      <div className="dlg-notices">
        {dlg.phase === 'opening' && <div className="dlg-status">Opening {feature.name}</div>}
        {dlg.phase === 'failed' && (
          <div className="dlg-banner" role="alert">
            {dlg.failure}
          </div>
        )}
        {dlg.previewError && (
          <div className="dlg-banner" role="alert" data-testid="preview-error">
            {dlg.previewError}
          </div>
        )}
        {dlg.problem && (
          <div className="dlg-banner" role="alert" data-testid="dialog-problem">
            {dlg.problem}
          </div>
        )}
      </div>
      {desktopOnly && (
        <p className="dlg-desktop-only" data-testid="desktop-only">
          {DESKTOP_ONLY_TEXT}
        </p>
      )}
      {description !== null && !desktopOnly && (
        <div className="dlg-form" data-testid="dialog-form">
          {rows.map(({ spec, flip }, i) => {
            const key = spec.key ?? `row${i}`
            const v = values[key]
            switch (spec.kind) {
              case 'num':
                return (
                  <NumberRow
                    key={`${key}:${i}`}
                    spec={spec}
                    value={v}
                    sent={dlg.sent[key]}
                    error={messageOf(key)}
                    info={null}
                    editing={editing === key}
                    variables={variables}
                    flip={flip === null ? null : <FlipButton spec={flip} on={(dlg.sent[flip.key!] ?? values[flip.key!]?.value) === true} onToggle={() => dlg.set({ [flip.key!]: !((dlg.sent[flip.key!] ?? values[flip.key!]?.value) === true) })} />}
                    onEdit={() => setPadKey(key)}
                    onCommit={(text) => dlg.set({ [key]: text })}
                    onDone={() => setPadKey(null)}
                  />
                )
              case 'choice':
              case 'ops':
                return <ChoiceRow key={`${key}:${i}`} spec={spec} value={dlg.sent[key] ?? v?.value} error={messageOf(key)} onPick={(x) => dlg.set({ [key]: x })} />
              case 'check': {
                const on = (dlg.sent[key] ?? v?.value) === true
                return <CheckRow key={`${key}:${i}`} spec={spec} on={on} onToggle={() => dlg.set({ [key]: !on })} />
              }
              case 'sel':
                return (
                  <SelectionRow
                    key={`${key}:${i}`}
                    spec={spec}
                    items={sel.items(key)}
                    label={(item) => itemLabel(item, outputs, committed)}
                    active={sel.activeKey === key}
                    readOnly={isReadOnlyField(spec)}
                    message={sel.refusal?.key === key ? sel.refusal.text : null}
                    onActivate={() => sel.activate(key)}
                    onRemove={(n) => sel.remove(key, n)}
                  />
                )
              case 'info':
                return <InfoRow key={`${key}:${i}`} text={spec.text ?? ''} />
              case 'text':
                return <TextRow key={`${key}:${i}`} spec={spec} value={typeof v?.value === 'string' ? v.value : (v?.text ?? '')} error={messageOf(key)} onCommit={(t) => dlg.set({ [key]: t })} />
              default:
                return null
            }
          })}
          {rows.length === 0 && <p className="dlg-status">This feature has nothing to set here.</p>}
        </div>
      )}
    </DialogArea>
  )
}
