import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError } from '../net/api'
import type { ConnectionStore } from '../net/connection'
import { evaluateNumber, formatNumber } from '../pad/editor'
import type { SelectionItem, SelectionStore } from '../selection/store'
import { DialogArea } from '../shell/DialogArea'
import { explainError } from '../shell/errors'
import { Icon, paths } from '../shell/icons'
import { useMode } from '../shell/mode'
import { ToolIcon } from '../tools/icons'
import type { FieldSpec, SelItem } from '../dialog/dialogApi'
import { DesktopNumberRow, NumberRow, SelectionRow } from '../dialog/fields'
import type { RowKeys } from '../dialog/fields'
import type { AssemblyOutputs } from '../wire'
import type { AssemblyTypes } from './strip'
import { acceptMateDialog, cancelMateDialog, hasAngle, hasFlip, hasOffset, MATE_PICK_KINDS, openMateDialog, otherSide, pickOf, sideItem, sideText, updateMateDialog } from './mates'
import type { MateUpdate, MateView, Side, SidePick } from './mates'
import '../dialog/dialog.css'
import './mates.css'

/** What opens a mate dialog: a new mate of a type (with the selection as its picks), or a stored mate to edit. */
export interface MateStart {
  /** Counts the starts, so a new dialog is a new component. */
  seq: number
  tab: string
  what: { type: string; picks: SidePick[]; later: SidePick[] } | { mate: string }
}

export const NEED_BOTH = 'Pick a mate connector on each of the two parts first.'
export const PICK_HINT = 'Click a flat face, a round face (picks the end nearest the click), a circular edge, a straight edge or a teal mate connector on each part.'
export const LIMITS_NOTE = 'Desktop only for now'

/** What the desktop's Enter and Esc do to the open mate dialog, and the type buttons of the toolbar while it is open (card I10.6). */
export interface MateKeys {
  accept(): boolean
  cancel(): void
  setType(type: string): void
  /** The type the dialog shows now, or null before it has opened. */
  type(): string | null
}

let openKeys: MateKeys | null = null

/** The open mate dialog's keys, or null when none is open. */
export const mateDialogKeys = (): MateKeys | null => openKeys

export interface MateDone {
  accepted: boolean
  revision: number | null
  /** The last preview, which the view keeps drawing until the revision an accept made arrives. */
  view: MateView | null
}

interface Options {
  store: ConnectionStore
  documentId: string
  documentName: string
  tab: string
  start: MateStart
  /** The committed Assembly: the instances' names and parts, to place a pick and to highlight a side. */
  outputs: AssemblyOutputs | null
  selection: SelectionStore
  /** The preview to draw (null: the committed Assembly again). */
  onView: (view: MateView | null) => void
  onDone: (done: MateDone) => void
}

export interface MateDialogState {
  phase: 'opening' | 'open' | 'failed'
  failure: string | null
  view: MateView | null
  /** The field the view's picks go to, or null when none is active. */
  active: Side | null
  /** What was sent and has no answer yet, by key: the field shows it at once. */
  sent: Record<string, unknown>
  /** Accept (or an update) was refused or failed; the dialog stays open with every value. */
  problem: string | null
  accepting: boolean
  activate(side: Side | null): void
  clear(side: Side): void
  set(changes: MateUpdate): void
  accept(): Promise<void>
  cancel(): void
}

/**
 * One mate dialog against the server (freeshape/serve.py "Mate dialogs"): opens it, sends each change as one update (one at a time,
 * in order), takes each reply as the preview to draw, and ends it with Accept or Cancel. It has the view's selection while it is open:
 * a pick of a face, an edge or a connector goes to the active field, and after it the other field becomes the active one when it is empty.
 * Leaving the screen with the dialog open cancels it on the server.
 */
export function useMateDialog({ store, documentId, documentName, tab, start, outputs, selection, onView, onDone }: Options): MateDialogState {
  const [phase, setPhase] = useState<MateDialogState['phase']>('opening')
  const [failure, setFailure] = useState<string | null>(null)
  const [view, setView] = useState<MateView | null>(null)
  const [active, setActiveState] = useState<Side | null>(null)
  const activeRef = useRef<Side | null>(null)
  const [sent, setSent] = useState<Record<string, unknown>>({})
  const [problem, setProblem] = useState<string | null>(null)
  const [accepting, setAccepting] = useState(false)
  const dialog = useRef<string | null>(null)
  const over = useRef(false)
  const queue = useRef<Promise<void>>(Promise.resolve())
  const client = store.getState().client
  const latest = useRef({ outputs, view, onView, onDone, documentName })
  useEffect(() => {
    latest.current = { outputs, view, onView, onDone, documentName }
  })
  const setActive = useCallback((side: Side | null) => {
    activeRef.current = side
    setActiveState(side)
  }, [])

  const show = useCallback((r: MateView) => {
    setView(r)
    latest.current.view = r
    latest.current.onView(r)
  }, [])

  // one update at a time, in order; `after` reads the answer (a pick moves the active field)
  const send = useCallback(
    (changes: MateUpdate, after?: (r: MateView) => void) => {
      if (over.current) return
      const keys = Object.keys(changes).filter((k) => k !== 'a' && k !== 'b')
      setSent((old) => ({ ...old, ...Object.fromEntries(keys.map((k) => [k, (changes as Record<string, unknown>)[k]])) }))
      queue.current = queue.current.then(async () => {
        try {
          if (over.current || dialog.current === null) return
          const r = await updateMateDialog(documentId, tab, dialog.current, client, changes)
          if (over.current) return
          show(r)
          setProblem(null)
          after?.(r)
        } catch (e) {
          if (!over.current) setProblem(explainError(e, latest.current.documentName))
        } finally {
          setSent((old) => {
            const next = { ...old }
            for (const k of keys) delete next[k]
            return next
          })
        }
      })
    },
    [documentId, tab, client, show],
  )

  useEffect(() => {
    let dead = false
    const what = start.what
    openMateDialog(documentId, tab, client, 'mate' in what ? { mate: what.mate } : { type: what.type, picks: what.picks }).then(
      (r) => {
        if (dead) {
          void cancelMateDialog(documentId, tab, r.dialog, client).catch(() => {})
          return
        }
        dialog.current = r.dialog
        show(r)
        setPhase('open')
        setActive(r.a === null ? 'a' : r.b === null ? 'b' : 'a')
        // a preselected connector cannot ride in the open request: it is the first update
        if (!('mate' in what) && what.later.length > 0) {
          const [first, second] = what.later
          send({ a: first ?? null, ...(second !== undefined ? { b: second } : {}) }, (v) => setActive(v.a === null ? 'a' : v.b === null ? 'b' : 'a'))
        }
      },
      (e: unknown) => {
        if (dead) return
        setFailure(explainError(e, latest.current.documentName))
        setPhase('failed')
      },
    )
    return () => {
      dead = true
      const did = dialog.current
      if (did !== null && !over.current) {
        over.current = true
        void cancelMateDialog(documentId, tab, did, client).catch(() => {})
      }
      latest.current.onView(null)
    }
  }, [documentId, tab, client, start, show, send, setActive])

  // the dialog has the view while it is open: a pick goes to the active field
  const pick = useCallback(
    (item: SelectionItem | null) => {
      const side = activeRef.current
      if (item === null || side === null) return
      const p = pickOf(item, latest.current.outputs, latest.current.view?.placements ?? null)
      if (p === null) return
      send({ [side]: p }, (r) => {
        const got = r[side]
        const took = got !== null && got.instance === p.instance && ('connector' in p ? got.kind !== 'face' && got.kind !== 'edge' : got.index === p.index)
        if (took && r[otherSide(side)] === null) setActive(otherSide(side))
      })
    },
    [send, setActive],
  )
  const picker = useRef(pick)
  useEffect(() => {
    picker.current = pick
  }, [pick])
  const [stable] = useState(() => (item: SelectionItem | null) => picker.current(item))
  useEffect(() => {
    selection.enterDialog()
    return () => selection.leaveDialog()
  }, [selection])
  useEffect(() => {
    selection.setPickFilter(active === null ? [] : [...MATE_PICK_KINDS])
    selection.setDialogMulti(false)
    selection.setFieldPicker(active === null ? null : stable)
  }, [selection, active, stable])
  // what the view draws as selected: the face or edge each field holds
  const sideA = view?.a ?? null
  const sideB = view?.b ?? null
  useEffect(() => {
    selection.set([sideItem(sideA, outputs), sideItem(sideB, outputs)].filter((i): i is SelectionItem => i !== null))
  }, [selection, sideA, sideB, outputs])

  const accept = useCallback(async () => {
    if (over.current || dialog.current === null || latest.current.view === null) return
    if (latest.current.view.a === null || latest.current.view.b === null) {
      setProblem(NEED_BOTH)
      return
    }
    setAccepting(true)
    setProblem(null)
    try {
      await queue.current
      const r = await acceptMateDialog(documentId, tab, dialog.current, client)
      over.current = true
      latest.current.onDone({ accepted: true, revision: r.revision, view: latest.current.view })
    } catch (e) {
      const name = latest.current.documentName
      setProblem(
        e instanceof ApiError && e.status === 423
          ? `"${name}" is open read-only here, so Accept did nothing. Your values stay in the dialog: use Take over in the banner and Accept again, or Cancel.`
          : explainError(e, name),
      )
    } finally {
      setAccepting(false)
    }
  }, [documentId, tab, client])

  const cancel = useCallback(() => {
    if (over.current) return
    over.current = true
    const did = dialog.current
    if (did !== null) void cancelMateDialog(documentId, tab, did, client).catch(() => {})
    latest.current.onDone({ accepted: false, revision: null, view: null })
  }, [documentId, tab, client])

  const clear = useCallback(
    (side: Side) => {
      send({ [side]: null })
      setActive(side)
    },
    [send, setActive],
  )
  const activate = useCallback((side: Side | null) => setActive(side === activeRef.current ? null : side), [setActive])

  return { phase, failure, view, active, sent, problem, accepting, activate, clear, set: send, accept, cancel }
}

/** A number row's field: the schema row the standard rows draw from, for the two numbers of a mate. */
const numberSpec = (key: 'offset' | 'angle'): FieldSpec => ({ key, kind: 'num', standard: true, visible: true, label: key === 'offset' ? 'Offset' : 'Angle', unit: key === 'offset' ? 'mm' : '°' })
const FIELD_LABEL: Record<Side, string> = { a: 'First mate connector', b: 'Second mate connector' }
const selectionSpec = (side: Side): FieldSpec => ({ key: side, kind: 'sel', standard: true, visible: true, label: FIELD_LABEL[side], want: ['connector'], multi: false, number: false, list: false, tip: null })

export interface MateDialogProps {
  store: ConnectionStore
  documentId: string
  documentName: string
  tab: string
  start: MateStart
  /** The mate types and their labels and help, from the server (null until it has answered). */
  types: AssemblyTypes | null
  outputs: AssemblyOutputs | null
  selection: SelectionStore
  onView: (view: MateView | null) => void
  onDone: (done: MateDone) => void
}

/**
 * The mate dialog (card I10.6), in the dialog area, as the desktop's MateDialog (freeshape/ui/assembly_ui.py): a row of the eight type
 * buttons, the type's help line, the First and Second mate connector fields (the active one highlighted, each with a clear button), the
 * pick hint, Flip and Rotate 90, Offset and Angle, the Mate values (read-only) and Limits (off: the desktop sets them). Numbers are typed in
 * Desktop mode and set on the pad in Tablet mode. Cancel and Accept are in the header; Enter and Esc reach them through `mateDialogKeys`.
 */
export function MateDialog({ store, documentId, documentName, tab, start, types, outputs, selection, onView, onDone }: MateDialogProps) {
  const dlg = useMateDialog({ store, documentId, documentName, tab, start, outputs, selection, onView, onDone })
  const desktop = useMode() === 'desktop'
  const view = dlg.view
  const [padKey, setPadKey] = useState<'offset' | 'angle' | null>(null)
  const [typed, setTyped] = useState<{ key: string; text: string } | null>(null)
  const { set, accept, cancel } = dlg
  const type = (dlg.sent['type'] as string | undefined) ?? view?.type ?? null
  const latestType = useRef(type)
  useEffect(() => {
    latestType.current = view?.type ?? null
  })
  const canAccept = dlg.phase === 'open' && !dlg.accepting
  useEffect(() => {
    const keys: MateKeys = {
      accept: () => canAccept && (void accept(), true),
      cancel,
      setType: (t) => {
        if (t !== latestType.current) set({ type: t })
      },
      type: () => latestType.current,
    }
    openKeys = keys
    return () => {
      if (openKeys === keys) openKeys = null
    }
  }, [canAccept, accept, cancel, set])

  const name = (id: string): string => outputs?.instances.find((i) => i.id === id)?.name ?? id
  const info = types?.mates.find((m) => m.id === type)
  const freedoms = info?.freedoms ?? []
  const numberText = (key: 'offset' | 'angle'): { value: { value: number } | undefined; sent: unknown } => ({ value: view === null ? undefined : { value: view[key] }, sent: dlg.sent[key] })
  // a typed number: read as the pad's text is read; text that is no number is shown as the problem and nothing is sent
  const commit = (key: 'offset' | 'angle') => (text: string) => {
    const r = evaluateNumber(text)
    if (!r.ok) return setTyped({ key, text: r.error })
    setTyped(null)
    set({ [key]: r.value })
  }
  const keys: RowKeys = { onEnter: () => void accept(), onEscape: cancel }
  const sideItems = (side: Side): SelItem[] => {
    const text = sideText(view?.[side] ?? null, name)
    return text === null ? [] : [{ ok: true, kind: view![side]!.kind, name: text }]
  }
  const numberRow = (key: 'offset' | 'angle') => {
    const spec = numberSpec(key)
    const { value, sent } = numberText(key)
    const error = typed?.key === key ? typed.text : null
    if (desktop && padKey !== key) return <DesktopNumberRow key={key} spec={spec} value={value} sent={sent} error={error} variables={[]} keys={keys} onCommit={commit(key)} onPad={() => setPadKey(key)} />
    return (
      <NumberRow
        key={key}
        spec={spec}
        value={value}
        sent={sent}
        error={error}
        info={null}
        editing={padKey === key}
        variables={[]}
        flip={null}
        onEdit={() => setPadKey(key)}
        onCommit={commit(key)}
        onDone={() => setPadKey(null)}
      />
    )
  }
  const flipOn = (dlg.sent['flip'] ?? view?.flip) === true

  const actions = (
    <>
      <button type="button" className="dlg-action" aria-label="Cancel" onClick={cancel}>
        <Icon d={paths.close} />
      </button>
      <button type="button" className="dlg-action dlg-accept" aria-label="Accept" disabled={dlg.phase !== 'open' || dlg.accepting} onClick={() => void accept()}>
        <Icon d={paths.check} sw={2.2} />
      </button>
    </>
  )
  const title = view?.name ?? (type !== null ? (info?.label ?? type) : 'Mate')
  const shown = useMemo(() => (view === null ? [] : view.values), [view])

  return (
    <DialogArea title={title} onClose={cancel} actions={actions}>
      <div className="dlg-notices">
        {dlg.phase === 'opening' && <div className="dlg-status">Opening the mate</div>}
        {dlg.phase === 'failed' && (
          <div className="dlg-banner" role="alert">
            {dlg.failure}
          </div>
        )}
        {dlg.problem && (
          <div className="dlg-banner" role="alert" data-testid="dialog-problem">
            {dlg.problem}
          </div>
        )}
      </div>
      {view !== null && (
        <div className="dlg-form mate-form" data-testid="mate-form" data-dlg-fields="" data-desktop={desktop ? '' : undefined} data-mate-type={view.type}>
          <div className="mate-types" role="radiogroup" aria-label="Mate type">
            {(types?.mates ?? []).map((m) => (
              <button
                key={m.id}
                type="button"
                role="radio"
                className="mate-type"
                aria-checked={type === m.id}
                aria-label={m.label}
                title={`${m.label} — ${m.help}`}
                data-mate-type-button={m.id}
                onClick={() => type !== m.id && set({ type: m.id })}
              >
                <ToolIcon name={`m_${m.id}`} px={22} />
              </button>
            ))}
          </div>
          {info !== undefined && (
            <div className="dlg-info mate-help" data-testid="mate-help">
              <b>{info.label}</b> — {info.help}
            </div>
          )}
          {(['a', 'b'] as const).map((side) => (
            <SelectionRow
              key={side}
              spec={selectionSpec(side)}
              items={sideItems(side)}
              label={(item) => item.name ?? ''}
              active={dlg.active === side}
              readOnly={false}
              message={null}
              onActivate={() => dlg.activate(side)}
              onRemove={() => dlg.clear(side)}
            />
          ))}
          <div className="dlg-info mate-hint" data-testid="mate-hint">
            {PICK_HINT}
          </div>
          {view.error !== null && (
            <div className="dlg-banner" role="alert" data-testid="mate-error">
              {view.error}
            </div>
          )}
          {(hasFlip(view.type) || hasAngle(view.type)) && (
            <div className="mate-options">
              {hasFlip(view.type) && (
                <button type="button" className="mate-option" aria-pressed={flipOn} title="Flip the primary (Z) axis — e.g. put a pin in the other way round" data-field-key="flip" data-dlg-field="" onClick={() => set({ flip: !flipOn })}>
                  <Icon d={paths.flip} px={18} sw={1.8} />
                  <span>Flip</span>
                </button>
              )}
              {hasAngle(view.type) && (
                <button type="button" className="mate-option" title="Turn the second part 90° about the Z axis" data-dlg-field="" onClick={() => set({ turn90: true })}>
                  <span aria-hidden="true">⟳</span>
                  <span>Rotate 90</span>
                </button>
              )}
            </div>
          )}
          {hasOffset(view.type) && numberRow('offset')}
          {hasAngle(view.type) && numberRow('angle')}
          {shown.length > 0 && (
            <div className="mate-values" data-testid="mate-values" role="group" aria-label="Mate values">
              {shown.map((v) => (
                <div key={v.dof} className="mate-value" data-dof={v.dof}>
                  <span className="mate-value-label">{shown.length === 1 ? 'Value' : v.label}</span>
                  <span className="mate-value-number">{formatNumber(v.value)}</span>
                  <span className="mate-value-unit">{v.unit}</span>
                </div>
              ))}
            </div>
          )}
          {freedoms.length > 0 && (
            <button type="button" role="switch" aria-checked={false} className="dlg-switch mate-limits" disabled title={LIMITS_NOTE} data-testid="mate-limits">
              <span>Limits</span>
              <span className="mate-limits-note">{LIMITS_NOTE}</span>
            </button>
          )}
        </div>
      )}
    </DialogArea>
  )
}
