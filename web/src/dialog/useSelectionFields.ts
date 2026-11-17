import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SelectionItem, SelectionStore } from '../selection/store'
import { explainError } from '../shell/errors'
import type { StudioOutputs } from '../wire'
import type { FieldSpec, SelItem } from './dialogApi'
import { afterPick, asSent, BASE_LOADING, baseIndex, NOT_IN_MODEL, fetchRefs, highlightItems, itemsOfAnswer, pickKinds, refPick } from './selectionFields'

interface Options {
  selection: SelectionStore
  /** The model the view draws (the dialog's base once it has arrived): Element names are matched against it to highlight a field's items. */
  outputs: StudioOutputs | null
  /**
   * The dialog's base (card F3): the model before the edited feature, which the refs route resolves the dialog's picks
   * against. A pick made on another drawn model is found in it by Element name first. Null until it has arrived.
   */
  base: StudioOutputs | null
  /** The open dialog and the client it belongs to, sent with each pick so the server reads the base; null until it opens. */
  dialog: { id: string; client: string } | null
  documentId: string
  documentName: string
  tab: string
  /** The dialog's rows (null until it has opened). */
  specs: readonly FieldSpec[] | null
  /** What the server holds for each selection row. */
  fields: Record<string, SelItem[]>
  /** What the dialog sent and has no answer to yet, by key. */
  sent: Record<string, unknown>
  /** The field the server named to fill next (the update reply's `active`). */
  serverActive: string | null
  set(values: Record<string, unknown>): void
}

export interface SelectionFields {
  activeKey: string | null
  /** Make the field active, or (null, or the active one again) none. */
  activate(key: string | null): void
  items(key: string): SelItem[]
  remove(key: string, index: number): void
  /** Why the last pick was refused, with the field it was for. */
  refusal: { key: string; text: string } | null
}

/** A field with a number per item (fillet vertices with a radius each) is read-only: the server refuses it for now. */
export const isReadOnlyField = (spec: FieldSpec): boolean => spec.number === true

/**
 * The selection fields of one open feature dialog. It takes the view over from the general selection while the dialog is
 * open (`SelectionStore.enterDialog`): the active field's items are what the view highlights, the field's `want` is the pick
 * filter, a field that takes several items turns Multi on, and each Commit goes to the refs route and then to the field.
 * Leaving the dialog puts the general selection back.
 */
export function useSelectionFields({ selection, outputs, base, dialog, documentId, documentName, tab, specs, fields, sent, serverActive, set }: Options): SelectionFields {
  const [activeKey, setActive] = useState<string | null>(null)
  const activeKeyRef = useRef<string | null>(null)
  useEffect(() => {
    activeKeyRef.current = activeKey
  }, [activeKey])
  const [refusal, setRefusal] = useState<SelectionFields['refusal']>(null)
  // items this hook sent that the dialog has not answered: a second pick must start from them, not from the server's older list
  const mine = useRef<Record<string, SelItem[]>>({})
  const queue = useRef<Promise<void>>(Promise.resolve())
  const latest = useRef({ fields, sent, specs, set, outputs, base, dialog, documentName })
  useEffect(() => {
    latest.current = { fields, sent, specs, set, outputs, base, dialog, documentName }
  })

  const specOf = useCallback((key: string | null): FieldSpec | null => (key === null ? null : (latest.current.specs?.find((s) => s.kind === 'sel' && s.key === key && s.visible) ?? null)), [])
  const itemsOf = useCallback((key: string): SelItem[] => {
    const own = mine.current[key]
    if (own !== undefined) return own
    const s = latest.current.sent[key]
    return Array.isArray(s) ? (s as SelItem[]) : (latest.current.fields[key] ?? [])
  }, [])
  // the server's answer is the truth again once nothing of ours is waiting for one
  useEffect(() => {
    for (const k of Object.keys(mine.current)) if (sent[k] === undefined) delete mine.current[k]
  }, [sent, fields])

  const rows = useMemo(() => specs?.filter((s) => s.kind === 'sel' && s.visible && s.key !== null) ?? [], [specs])

  // the dialog has the view while it is open
  useEffect(() => {
    selection.enterDialog()
    return () => selection.leaveDialog()
  }, [selection])

  // the field to fill: the first empty one when the dialog opens, then the one the server names after each change
  const opened = useRef(false)
  useEffect(() => {
    if (opened.current || specs === null) return
    opened.current = true
    const first = rows.find((s) => !isReadOnlyField(s) && (fields[s.key!] ?? []).length === 0)
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (first) setActive(first.key)
  }, [specs, rows, fields])
  useEffect(() => {
    if (serverActive !== null && specOf(serverActive) !== null) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActive(serverActive)
      setRefusal(null)
    }
  }, [serverActive, specOf])

  const commitItems = useCallback((key: string, items: SelItem[]) => {
    mine.current[key] = items
    latest.current.set({ [key]: items.map(asSent) })
  }, [])

  const pick = useCallback(
    (item: SelectionItem | null) => {
      const key = activeKeyRef.current
      const spec = specOf(key)
      // a release over empty space changes nothing
      if (item === null || key === null || spec === null || isReadOnlyField(spec)) return
      const index = baseIndex(item, latest.current.outputs, latest.current.base)
      const owner = latest.current.dialog
      if (index === undefined || owner === null) {
        setRefusal({ key, text: BASE_LOADING })
        return
      }
      if (index === null) {
        setRefusal({ key, text: NOT_IN_MODEL })
        return
      }
      const body = refPick({ ...item, index }, spec.want ?? [])
      if (body === null) return
      queue.current = queue.current.then(async () => {
        try {
          const reply = await fetchRefs(documentId, tab, [body], owner)
          const got = reply.items[0]
          if (got === undefined) return
          if (!got.ok) {
            setRefusal({ key, text: got.reason })
            return
          }
          setRefusal(null)
          commitItems(key, afterPick(itemsOf(key), itemsOfAnswer(got.kind, got.ref, got.name, got.stamp), spec.multi === true))
        } catch (e) {
          setRefusal({ key, text: explainError(e, latest.current.documentName) })
        }
      })
    },
    [documentId, tab, specOf, itemsOf, commitItems],
  )
  // one stable function for the store, so the view does not see a new picker on every render
  const picker = useRef((item: SelectionItem | null) => pick(item))
  useEffect(() => {
    picker.current = (item) => pick(item)
  }, [pick])
  const [stable] = useState(() => (item: SelectionItem | null) => picker.current(item))

  const spec = activeKey === null ? null : (rows.find((s) => s.key === activeKey) ?? null)
  const active = spec !== null && !isReadOnlyField(spec) ? spec : null
  useEffect(() => {
    // no active field: nothing in the view is pickable, and a pick would have nowhere to go
    selection.setPickFilter(active === null ? [] : pickKinds(active.want))
    selection.setDialogMulti(active?.multi === true)
    selection.setFieldPicker(active === null ? null : stable)
  }, [selection, active, stable])

  // what the view highlights is the active field's items
  const shown = active === null ? null : (sent[active.key!] ?? fields[active.key!])
  useEffect(() => {
    selection.set(highlightItems(Array.isArray(shown) ? (shown as SelItem[]) : [], outputs))
  }, [selection, shown, outputs])

  const activate = useCallback(
    (key: string | null) => {
      setRefusal(null)
      setActive((was) => (key === was ? null : key))
    },
    [],
  )
  const remove = useCallback(
    (key: string, index: number) => {
      const spec = specOf(key)
      if (spec === null || isReadOnlyField(spec)) return
      setRefusal(null)
      commitItems(key, itemsOf(key).filter((_, i) => i !== index))
    },
    [specOf, itemsOf, commitItems],
  )
  const shownItems = (key: string): SelItem[] => (Array.isArray(sent[key]) ? (sent[key] as SelItem[]) : (fields[key] ?? []))
  return { activeKey: active === null ? null : active.key, activate, items: shownItems, remove, refusal }
}
