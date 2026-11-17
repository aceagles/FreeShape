import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../net/api'
import type { ConnectionStore, PreviewMessage } from '../net/connection'
import { explainError } from '../shell/errors'
import { decodeOutputs, isStudio } from '../wire'
import type { StudioOutputs } from '../wire'
import type { CalibrateBody } from '../canvas/ruler'
import { acceptFeatureDialog, calibrateCanvasDialog, cancelFeatureDialog, fetchBaseBytes, fetchPreviewBytes, openCreateDialog, openFeatureDialog, updateFeatureDialog } from './dialogApi'
import type { DialogView, OpenReply } from './dialogApi'

/** The feature types whose preview carries drag arrows (dialog_schema.manipulators). */
const HAS_ARROWS: ReadonlySet<string> = new Set(['extrude', 'plane'])
/** The types that ask for a preview as soon as they open: those with arrows, and a Canvas, whose preview is the picture itself (card I12.5). */
const PREVIEW_ON_OPEN: ReadonlySet<string> = new Set([...HAS_ARROWS, 'canvas'])

/** The model a dialog's update produced, drawn in place of the committed one. */
export interface PreviewState {
  outputs: StudioOutputs
  /** The preview's number (the newest is drawn; an older one that arrives late is dropped). */
  number: number
}

export interface FeatureDialogState {
  phase: 'opening' | 'open' | 'failed'
  /** Why the dialog could not open (phase 'failed'). */
  failure: string | null
  opened: OpenReply | null
  view: DialogView | null
  /** What each row last refused, by key. */
  errors: Record<string, string>
  /** What was sent and not yet answered, by key: the field shows it at once. */
  sent: Record<string, unknown>
  /** The feature failed to build with these values (the model drawn is the one before it). */
  previewError: string | null
  /** Accept was refused or failed; the dialog stays open with every value. */
  problem: string | null
  /** Accept is on its way. */
  accepting: boolean
  set(values: Record<string, unknown>): void
  /** A Canvas dialog's Calibrate (card I12.6): sent after every change already on its way; true once the dialog shows the new size. */
  calibrate(body: CalibrateBody): Promise<boolean>
  accept(): Promise<void>
  cancel(): void
}

interface Options {
  store: ConnectionStore
  documentId: string
  documentName: string
  tab: string
  feature: string
  /** Open a dialog that creates a feature of this type with these picks, instead of editing `feature` (the Shell keys the dialog, so it never changes while open). */
  create?: { type: string; picks: readonly unknown[]; params?: Record<string, unknown> }
  /** The preview to draw, or null for the committed model again. */
  onPreview: (p: PreviewState | null) => void
  /**
   * The committed revision on screen (card F3): the dialog's base model is fetched again when it moves, since the base is
   * built from the committed model.
   */
  revision?: number | null
  /** The model the dialog picks against (card F3), to draw while it is open; null when the dialog is over. */
  onBase?: (base: StudioOutputs | null) => void
  /** The dialog is over. `revision` is the revision an accept made (null for Cancel). */
  onDone: (result: { accepted: boolean; revision: number | null }) => void
}

/**
 * One feature dialog against the server: opens it, sends each change as one dialog update (one at a time, in order),
 * follows the previews the server pushes for it, and ends it with Accept or Cancel. Leaving the screen with the dialog
 * still open cancels it on the server.
 */
export function useFeatureDialog({ store, documentId, documentName, tab, feature, create, onPreview, onDone, revision = null, onBase }: Options): FeatureDialogState {
  const [phase, setPhase] = useState<FeatureDialogState['phase']>('opening')
  const [failure, setFailure] = useState<string | null>(null)
  const [opened, setOpened] = useState<OpenReply | null>(null)
  const [view, setView] = useState<DialogView | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [sent, setSent] = useState<Record<string, unknown>>({})
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [accepting, setAccepting] = useState(false)

  const latest = useRef({ onPreview, onDone, onBase, documentName })
  useEffect(() => {
    latest.current = { onPreview, onDone, onBase, documentName }
  })
  const dialog = useRef<string | null>(null)
  const over = useRef(false)
  const queue = useRef<Promise<void>>(Promise.resolve())
  const pending = useRef<Record<string, unknown>>({})
  const shown = useRef(0)
  const client = store.getState().client
  const createWith = useRef(create)

  const onPreviewMessage = useCallback(
    async (m: PreviewMessage) => {
      if (m.dialog !== dialog.current || over.current || m.preview < shown.current) return
      if (!m.outputs) {
        setPreviewError(m.error ?? 'The preview could not be built.')
        return
      }
      try {
        const got = await fetchPreviewBytes(documentId, tab, m.dialog, client)
        // a newer preview may have been drawn while this one loaded, or the dialog may be over
        if (got === null || over.current || got.number < shown.current) return
        const out = decodeOutputs(got.bytes)
        if (!isStudio(out)) return
        shown.current = got.number
        latest.current.onPreview({ outputs: out, number: got.number })
        if (got.number === m.preview) setPreviewError(m.ok ? null : m.error)
      } catch {
        // the model of this preview did not arrive: the last one stays drawn
      }
    },
    [documentId, tab, client],
  )

  useEffect(() => {
    let dead = false
    const stop = store.onPreview((m) => void onPreviewMessage(m))
    const want = createWith.current
    const opening = want ? openCreateDialog(documentId, tab, want.type, want.picks, client, want.params) : openFeatureDialog(documentId, tab, feature, client)
    opening.then(
      (r) => {
        if (dead) {
          void cancelFeatureDialog(documentId, tab, r.dialog, client).catch(() => {})
          return
        }
        dialog.current = r.dialog
        setOpened(r)
        setView(r)
        setPhase('open')
      },
      (e: unknown) => {
        if (dead) return
        setFailure(explainError(e, latest.current.documentName))
        setPhase('failed')
      },
    )
    return () => {
      dead = true
      stop()
      const did = dialog.current
      if (did !== null && !over.current) {
        over.current = true
        latest.current.onPreview(null)
        void cancelFeatureDialog(documentId, tab, did, client).catch(() => {})
      }
      latest.current.onBase?.(null)
    }
  }, [store, documentId, tab, feature, client, onPreviewMessage])

  // the model picks resolve against (card F3): once the dialog is open, and again after each committed change
  const openedId = opened?.dialog ?? null
  useEffect(() => {
    if (openedId === null) return
    let stale = false
    fetchBaseBytes(documentId, tab, openedId, client).then(
      (got) => {
        if (stale || over.current) return
        const out = decodeOutputs(got.bytes)
        if (isStudio(out)) latest.current.onBase?.(out)
      },
      () => {
        // no base: the view keeps drawing the committed model (or the preview) and a pick says the base is not there yet;
        // the next committed change asks again. The dialog's own banner stays for what the person did.
      },
    )
    return () => {
      stale = true
    }
  }, [openedId, revision, documentId, tab, client])

  const flush = useCallback(async () => {
    const values = pending.current
    if (Object.keys(values).length === 0 || dialog.current === null || over.current) return
    pending.current = {}
    try {
      const r = await updateFeatureDialog(documentId, tab, dialog.current, client, values)
      if (over.current) return
      setView(r)
      setErrors((old) => {
        const next = { ...old }
        for (const k of Object.keys(values)) {
          if (r.errors[k] !== undefined) next[k] = r.errors[k]
          else delete next[k]
        }
        return next
      })
      setProblem(null)
    } catch (e) {
      if (!over.current) setProblem(explainError(e, latest.current.documentName))
    } finally {
      setSent((old) => {
        const next = { ...old }
        for (const k of Object.keys(values)) if (next[k] === values[k]) delete next[k]
        return next
      })
    }
  }, [documentId, tab, client])

  const set = useCallback(
    (values: Record<string, unknown>) => {
      if (over.current) return
      Object.assign(pending.current, values)
      setSent((old) => ({ ...old, ...values }))
      queue.current = queue.current.then(flush)
    },
    [flush],
  )

  const calibrate = useCallback(
    (body: CalibrateBody): Promise<boolean> => {
      const run = queue.current.then(async () => {
        if (over.current || dialog.current === null) return false
        try {
          const r = await calibrateCanvasDialog(documentId, tab, dialog.current, client, body)
          if (over.current) return false
          setView(r)
          // the new size (and X, Y and Rotation) replace what was refused before; what the server still refuses stays
          setErrors((old) => {
            const next = { ...old }
            for (const k of ['width', 'height', 'x', 'y', 'rotation']) delete next[k]
            return { ...next, ...r.errors }
          })
          setProblem(null)
          return true
        } catch (e) {
          if (!over.current) setProblem(explainError(e, latest.current.documentName))
          return false
        }
      })
      queue.current = run.then(() => undefined)
      return run
    },
    [documentId, tab, client],
  )

  // The server previews on an update, not on open. A dialog of a feature that has drag arrows (or a Canvas) sends its first number back
  // unchanged once it is open, so the preview (and the arrows it carries) is there from the start, as on the desktop.
  useEffect(() => {
    if (!opened || !PREVIEW_ON_OPEN.has(opened.description.type)) return
    const text = (key: string | null): string => {
      const v = key ? opened.values[key] : undefined
      return v?.text ? v.text : typeof v?.value === 'number' ? String(v.value) : ''
    }
    const row = opened.description.fields.find((f) => f.kind === 'num' && f.visible && f.key && text(f.key) !== '')
    if (row?.key) set({ [row.key]: text(row.key) })
  }, [opened, set])

  const accept = useCallback(async () => {
    if (over.current || dialog.current === null) return
    setAccepting(true)
    setProblem(null)
    try {
      await queue.current
      const r = await acceptFeatureDialog(documentId, tab, dialog.current, client)
      over.current = true
      latest.current.onDone({ accepted: true, revision: r.revision })
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
    if (did !== null) void cancelFeatureDialog(documentId, tab, did, client).catch(() => {})
    latest.current.onDone({ accepted: false, revision: null })
  }, [documentId, tab, client])

  return { phase, failure, opened, view, errors, sent, previewError, problem, accepting, set, calibrate, accept, cancel }
}
