import { Fragment, useEffect, useMemo, useState } from 'react'
import type { ConnectionStore } from '../net/connection'
import type { SelectionStore } from '../selection/store'
import type { StudioOutputs } from '../wire'
import { DialogArea } from '../shell/DialogArea'
import { Icon, paths } from '../shell/icons'
import { useMode } from '../shell/mode'
import { CheckRow, ChoiceRow, DesktopCheckRow, DesktopChoiceRow, DesktopNumberRow, DesktopTextRow, FlipButton, InfoRow, NumberRow, SelectionRow, TextRow } from './fields'
import type { RowKeys } from './fields'
import { setManipulatorSink } from './manipulator'
import { visibleRows } from './schema'
import { itemLabel, readableError } from './selectionFields'
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
  /** Create a feature of this type (with these picks) instead of editing `feature`; its name is the type's label. */
  create?: { type: string; picks: readonly unknown[] }
  /** The variable names in scope for the pad's # key, without the "#". */
  variables: readonly string[]
  /** The view's selection: while the dialog is open its picks go to the active selection field (see useSelectionFields). */
  selection: SelectionStore
  /** The model the view draws (the dialog's base once it has arrived): the names of a field's items are matched against it to highlight them, and a sketch region is named from it. */
  outputs: StudioOutputs | null
  /** The dialog's base (card F3), the model the refs route resolves the dialog's picks against; null until it has arrived. */
  base: StudioOutputs | null
  /** The committed revision on screen: the base is fetched again when it moves. */
  revision: number | null
  onPreview: (p: PreviewState | null) => void
  /** The base model to draw while the dialog is open, or null when it is over. */
  onBase: (base: StudioOutputs | null) => void
  onDone: (result: { accepted: boolean; revision: number | null }) => void
}

/**
 * The dialog of one feature, in the dialog area over the feature panel, drawn from the description the server gives:
 * a number is a field that opens the pad, a choice segmented buttons or a list, a check a switch, a selection row shows its items as chips
 * and, once tapped, takes the picks of the view. Cancel and Accept are in the header. A change is sent as one dialog update and the view draws the preview
 * the server pushes back (see useFeatureDialog and the Shell).
 */
/** What the desktop's Enter and Esc do to a feature dialog (card I9.6). */
export interface DialogKeys {
  /** Accept, when Accept is on now; false when it is not. */
  accept(): boolean
  cancel(): void
}

let openKeys: DialogKeys | null = null

/** The open feature dialog's Enter and Esc, or null when none is open. */
export const featureDialogKeys = (): DialogKeys | null => openKeys

export function FeatureDialog({ store, documentId, documentName, tab, feature, create, variables, selection, outputs, base, revision, onPreview, onBase, onDone }: FeatureDialogProps) {
  const dlg = useFeatureDialog({ store, documentId, documentName, tab, feature: feature.id, create, onPreview, onDone, revision, onBase })
  const client = store.getState().client
  const dialogId = dlg.opened?.dialog ?? null
  const owner = useMemo(() => (dialogId === null ? null : { id: dialogId, client }), [dialogId, client])
  const sel = useSelectionFields({
    selection,
    outputs,
    base,
    dialog: owner,
    documentId,
    documentName,
    tab,
    specs: dlg.view?.description.fields ?? null,
    fields: dlg.view?.fields ?? {},
    sent: dlg.sent,
    serverActive: dlg.view?.active ?? null,
    set: dlg.set,
  })
  // Desktop mode (card I9.16): numbers are typed in the row; the pad opens only for a pen or a finger on a field
  const desktop = useMode() === 'desktop'
  // the number on the pad: the first number of the dialog opens with it, as in the layout; a tap on another moves it
  const [padKey, setPadKey] = useState<string | null | undefined>(undefined)
  // the drag arrows in the view send their values through this dialog, and a tap on their label opens the pad (Desktop
  // mode: focuses the typed field)
  const { set: setValues } = dlg
  useEffect(() => {
    // a drag closes the pad: it would still hold the old text and send it back on Enter
    setManipulatorSink({
      set: (v) => {
        setPadKey(null)
        setValues(v)
      },
      edit: (key) => {
        const field = desktop && key !== null ? document.querySelector<HTMLInputElement>(`.dlg-form [data-field-key="${CSS.escape(key)}"]`) : null
        if (field) {
          setPadKey(null)
          field.focus()
          field.select()
        } else setPadKey(key)
      },
    })
    return () => setManipulatorSink(null)
  }, [setValues, desktop])
  const description = dlg.view?.description ?? null
  const rows = description ? visibleRows(description.fields) : []
  const desktopOnly = description !== null && description.custom.length > 0
  const editing = desktopOnly || padKey === null ? null : (padKey ?? (desktop ? null : (rows.find((r) => r.spec.kind === 'num')?.spec.key ?? null)))
  const title = dlg.opened?.name ?? feature.name
  // Enter and Esc of Desktop mode reach this dialog through `featureDialogKeys` (the shell's one key listener)
  const canAccept = !desktopOnly && dlg.phase === 'open' && !dlg.accepting
  useEffect(() => {
    const keys: DialogKeys = { accept: () => canAccept && (void dlg.accept(), true), cancel: dlg.cancel }
    openKeys = keys
    return () => {
      if (openKeys === keys) openKeys = null
    }
  })

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
  // Enter and Esc inside a Desktop field: Accept (once the field has committed) and Cancel
  const keys: RowKeys = {
    onEnter: () => {
      if (canAccept) void dlg.accept()
    },
    onEscape: dlg.cancel,
  }
  const flipOn = (flip: { key: string | null }) => (dlg.sent[flip.key!] ?? values[flip.key!]?.value) === true

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
            {readableError(dlg.previewError)}
          </div>
        )}
        {dlg.problem && (
          <div className="dlg-banner" role="alert" data-testid="dialog-problem">
            {readableError(dlg.problem)}
          </div>
        )}
      </div>
      {desktopOnly && (
        <p className="dlg-desktop-only" data-testid="desktop-only">
          {DESKTOP_ONLY_TEXT}
        </p>
      )}
      {description !== null && !desktopOnly && (
        <div className="dlg-form" data-testid="dialog-form" data-dlg-fields="" data-desktop={desktop ? '' : undefined}>
          {rows.map(({ spec, flip }, i) => {
            const key = spec.key ?? `row${i}`
            const v = values[key]
            if (desktop) {
              switch (spec.kind) {
                case 'num':
                  if (editing === key) break // a pen or a finger opened the pad on it: the Tablet row below draws it
                  return (
                    <Fragment key={`${key}:${i}`}>
                      <DesktopNumberRow spec={spec} value={v} sent={dlg.sent[key]} error={messageOf(key)} variables={variables} keys={keys} onCommit={(text) => dlg.set({ [key]: text })} onPad={() => setPadKey(key)} />
                      {flip !== null && <DesktopCheckRow spec={flip} on={flipOn(flip)} keys={keys} onToggle={() => dlg.set({ [flip.key!]: !flipOn(flip) })} />}
                    </Fragment>
                  )
                case 'choice':
                  return <DesktopChoiceRow key={`${key}:${i}`} spec={spec} value={dlg.sent[key] ?? v?.value} error={messageOf(key)} keys={keys} onPick={(x) => dlg.set({ [key]: x })} />
                case 'check': {
                  const on = (dlg.sent[key] ?? v?.value) === true
                  return <DesktopCheckRow key={`${key}:${i}`} spec={spec} on={on} keys={keys} onToggle={() => dlg.set({ [key]: !on })} />
                }
                case 'text':
                  return <DesktopTextRow key={`${key}:${i}`} spec={spec} value={typeof v?.value === 'string' ? v.value : (v?.text ?? '')} error={messageOf(key)} variables={variables} keys={keys} onCommit={(t) => dlg.set({ [key]: t })} />
              }
            }
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
                    label={(item) => itemLabel(item, outputs, base)}
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
