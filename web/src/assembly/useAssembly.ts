import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ConnectionStore } from '../net/connection'
import { fetchOutputsBytes } from '../net/api'
import { useConnection, useRefetchOnRevision } from '../net/react'
import { explainError } from '../shell/errors'
import { decodeOutputs, isAssembly } from '../wire'
import type { AssemblyOutputs } from '../wire'
import type { DragSender } from './gizmo'

/** Fetch and decode an Assembly's outputs from `/documents/<document>/tabs/<tab>/outputs`; null when the tab is not an Assembly. */
export async function fetchAssembly(document: string, tab: string, signal?: AbortSignal): Promise<AssemblyOutputs | null> {
  const out = decodeOutputs(await fetchOutputsBytes(document, tab, signal))
  return isAssembly(out) ? out : null
}

export interface AssemblyEdits {
  /** An edit is on its way to the server. Edits are sent one at a time. */
  busy: boolean
  /** No edit can be made: the Document is read-only here. */
  disabled: boolean
  /** The eye of an instance (set_hidden). */
  setHidden(instance: string, hidden: boolean): void
  /** The fixed toggle of an instance (set_fixed). */
  setFixed(instance: string, fixed: boolean): void
  /** Delete one instance and the mates on it (delete_instances); resolves false when it failed. */
  deleteInstance(instance: string): Promise<boolean>
  /** A drag by a Drag handle: move_begin, move_to (the latest only while one is in flight) and move_end. */
  drag: DragSender
}

export interface AssemblyState {
  /** The outputs of the Assembly tab on screen, or null while they load (or when the tab is not an Assembly). */
  outputs: AssemblyOutputs | null
  edits: AssemblyEdits
}

interface Options {
  store: ConnectionStore
  /** The tab on screen, and its kind; the Assembly state is empty unless the kind is 'assembly'. */
  tab: string | null
  tabKind: string | null
  readOnly: boolean
  /** The Document's name, for the words of a refusal. */
  documentName: string
  /** A refusal to show the person. */
  onError: (title: string, message: string) => void
}

/**
 * Sends a drag as move_begin, then move_to for the newest transform only (a sample that arrives while one is in flight
 * replaces the one waiting, which loses nothing: move_to is absolute against the placements move_begin remembered), and
 * move_end once the last move_to has been answered. A refusal is shown once and ends the stream of moves.
 */
export function makeDragSender(
  post: (edit: Record<string, unknown>) => Promise<unknown>,
  onError: (e: unknown) => void,
  disabled: boolean,
): DragSender {
  let pending: number[][] | null = null
  let pumping: Promise<void> | null = null
  let started: Promise<boolean> = Promise.resolve(false)
  let ok = false
  const fail = (e: unknown): void => {
    if (ok) onError(e)
    ok = false
  }
  const pump = (): Promise<void> => {
    pumping ??= (async () => {
      try {
        while (pending && ok) {
          const T = pending
          pending = null
          await post({ op: 'move_to', T }).catch(fail)
        }
      } finally {
        pumping = null
      }
    })()
    return pumping
  }
  return {
    disabled,
    begin(instance) {
      pending = null
      ok = true
      started = post({ op: 'move_begin', instance }).then(
        () => true,
        (e) => (fail(e), false),
      )
    },
    move(T) {
      pending = T
      void started.then((s) => (s ? pump() : undefined))
    },
    async end() {
      const began = await started
      await pump()
      if (began) await post({ op: 'move_end' }).catch(() => {})
      ok = false
    },
  }
}

/**
 * The outputs of the Assembly tab on screen, loaded again when its revision rises (or on a reconnect), and the panel's
 * edits of it: set_hidden, set_fixed and delete_instances through the same `store.edit` path the feature panel uses (HTTP with
 * the client id and the base revision). A slower, older answer never replaces a newer one.
 */
export function useAssembly({ store, tab, tabKind, readOnly, documentName, onError }: Options): AssemblyState {
  const { document, epoch } = useConnection(store)
  const documentId = document?.id ?? null
  const mine = tabKind === 'assembly' ? tab : null
  const [loaded, setLoaded] = useState<AssemblyOutputs | null>(null)
  const latest = useRef(0)
  const load = useCallback(
    (id: string) => {
      if (!documentId) return
      const n = ++latest.current
      fetchAssembly(documentId, id).then(
        (o) => {
          if (o && n === latest.current) setLoaded(o)
        },
        () => {},
      )
    },
    [documentId],
  )
  useEffect(() => {
    if (mine) load(mine)
  }, [mine, load, epoch])
  useRefetchOnRevision(store, mine ?? '', () => mine && load(mine))
  const outputs = loaded !== null && loaded.tab === mine ? loaded : null

  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const disabled = readOnly || mine === null
  const act = useCallback(
    async (title: string, edit: Record<string, unknown>): Promise<boolean> => {
      if (mine === null || disabled || inFlight.current) return false
      inFlight.current = true
      setBusy(true)
      try {
        await store.edit(mine, edit)
        return true
      } catch (e) {
        onError(title, explainError(e, documentName))
        return false
      } finally {
        inFlight.current = false
        setBusy(false)
      }
    },
    [store, mine, disabled, onError, documentName],
  )
  const drag = useMemo(
    () =>
      makeDragSender(
        (edit) => (mine === null ? Promise.reject(new Error('no tab')) : store.edit(mine, edit)),
        (e) => onError('Could not move the instance', explainError(e, documentName)),
        disabled,
      ),
    [store, mine, disabled, onError, documentName],
  )
  const edits = useMemo<AssemblyEdits>(
    () => ({
      drag,
      busy,
      disabled,
      setHidden: (instance, hidden) => void act(hidden ? 'Could not hide the instance' : 'Could not show the instance', { op: 'set_hidden', instance, value: hidden }),
      setFixed: (instance, fixed) => void act(fixed ? 'Could not fix the instance' : 'Could not free the instance', { op: 'set_fixed', instance, value: fixed }),
      deleteInstance: (instance) => act('Could not delete the instance', { op: 'delete_instances', ids: [instance] }),
    }),
    [busy, disabled, act, drag],
  )
  return { outputs, edits }
}
