import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ConnectionStore } from '../net/connection'
import { fetchOutputsBytes } from '../net/api'
import { useConnection, useRefetchOnRevision } from '../net/react'
import { explainError } from '../shell/errors'
import { decodeOutputs, isAssembly } from '../wire'
import type { AssemblyOutputs } from '../wire'
import type { DragSender, SolvedListener } from './gizmo'

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
  /** Undo and redo of the Assembly (the engine's undo and redo ops; the outputs do not say whether there is a step to take). */
  undo(): void
  redo(): void
  /** Insert a part of a Part Studio tab of this Document, or (`part` null) all its parts as they sit there: one undo step; resolves false when it failed (the refusal is shown). */
  insert(tab: string, part: string | null): Promise<boolean>
  /** The fixed toggle of several instances: one set_fixed each, one after the other; stops at the first that fails. */
  setFixedAll(instances: readonly string[], fixed: boolean): Promise<void>
  /** A mate connector at an instance's part origin (add_origin_connector, card I10.4): one undo step, nothing new when it has one; resolves false when it failed. */
  addOriginConnector(instance: string): Promise<boolean>
  /** Delete one instance and the mates on it (delete_instances); resolves false when it failed. */
  deleteInstance(instance: string): Promise<boolean>
  /** Rename an instance or a mate (rename); resolves with a message in plain words when the server refused (the rename dialog shows it). */
  rename(id: string, name: string): Promise<string | void>
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
 * Loads one tab's outputs at a time (F17). `reset` starts afresh (another Document, tab or a reconnect: any answer for what
 * was asked before is dropped); `load` asks again after a revision rose. While a fetch is in flight a further `load` only
 * marks that one more is due, so a stream of revisions never cancels the answer on its way, and an answer older than the
 * outputs on screen is dropped.
 */
export class AssemblyLoader {
  private gen = 0
  private busy = -1
  private again = false
  private shown: number | null = null
  private want: { doc: string; tab: string } | null = null

  constructor(
    private readonly fetch: (document: string, tab: string) => Promise<AssemblyOutputs | null>,
    private readonly show: (outputs: AssemblyOutputs) => void,
  ) {}

  reset(doc: string, tab: string): void {
    this.gen++
    this.shown = null
    this.again = false
    this.want = { doc, tab }
    this.run()
  }

  load(doc: string, tab: string): void {
    if (!this.want || this.want.doc !== doc || this.want.tab !== tab) return this.reset(doc, tab)
    if (this.busy === this.gen) {
      this.again = true
      return
    }
    this.run()
  }

  private run(): void {
    const want = this.want
    if (!want) return
    const gen = this.gen
    this.busy = gen
    this.fetch(want.doc, want.tab)
      .then(
        (o) => {
          if (!o || gen !== this.gen || o.tab !== want.tab) return
          if (this.shown !== null && o.revision < this.shown) return
          this.shown = o.revision
          this.show(o)
        },
        () => {},
      )
      .finally(() => {
        if (gen !== this.gen) return
        this.busy = -1
        if (this.again) {
          this.again = false
          this.run()
        }
      })
  }
}

/**
 * Sends a drag as move_begin, then move_to for the newest transform only (a sample that arrives while one is in flight
 * replaces the one waiting, which loses nothing: move_to is absolute against the placements move_begin remembered), and
 * move_end once the last move_to has been answered. A refusal is shown once and ends the stream of moves. Each move_to's
 * reply carries the placements its solve changed (F17), handed to the listener with the motion it answered.
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
  let listener: SolvedListener | null = null
  const solved = (reply: unknown, T: number[][]): void => {
    const placements = (reply as { placements?: unknown } | null)?.placements
    if (placements && typeof placements === 'object') listener?.(placements as Record<string, number[][]>, T)
  }
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
          await post({ op: 'move_to', T }).then((r) => solved(r, T), fail)
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
    listen(onSolved) {
      listener = onSolved
    },
  }
}

/**
 * The outputs of the Assembly tab on screen, loaded again when its revision rises (or on a reconnect), and the panel's
 * edits of it: set_hidden, set_fixed and delete_instances through the same `store.edit` path the feature panel uses (HTTP with
 * the client id and the base revision). A slower, older answer never replaces a newer one: an answer is kept unless the
 * outputs on screen have a higher revision. At most one fetch is in flight; revisions that rise meanwhile fetch once more
 * after it (F17: during a drag every move_to raises the revision, and dropping each answer because a newer fetch had
 * started left the view unchanged until the drag stopped).
 */
export function useAssembly({ store, tab, tabKind, readOnly, documentName, onError }: Options): AssemblyState {
  const { document, epoch } = useConnection(store)
  const documentId = document?.id ?? null
  const mine = tabKind === 'assembly' ? tab : null
  const [loaded, setLoaded] = useState<AssemblyOutputs | null>(null)
  const [loader] = useState(() => new AssemblyLoader(fetchAssembly, setLoaded))
  useEffect(() => {
    if (mine && documentId) loader.reset(documentId, mine)
  }, [loader, mine, documentId, epoch])
  useRefetchOnRevision(store, mine ?? '', () => mine && documentId && loader.load(documentId, mine))
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
      insert: (studio, part) => act('Could not insert the parts', part === null ? { op: 'insert', tab: studio } : { op: 'insert', tab: studio, part }),
      undo: () => void act('Could not undo', { op: 'undo' }),
      redo: () => void act('Could not redo', { op: 'redo' }),
      setFixedAll: async (instances, fixed) => {
        for (const instance of instances) {
          if (!(await act(fixed ? 'Could not fix the instance' : 'Could not free the instance', { op: 'set_fixed', instance, value: fixed }))) return
        }
      },
      rename: async (id, name) => {
        if (mine === null || disabled) return
        if (inFlight.current) return 'Another change is still being saved. Try again in a moment.'
        inFlight.current = true
        setBusy(true)
        try {
          await store.edit(mine, { op: 'rename', id, name })
        } catch (e) {
          return explainError(e, documentName)
        } finally {
          inFlight.current = false
          setBusy(false)
        }
      },
      addOriginConnector: (instance) => act('Could not add the mate connector', { op: 'add_origin_connector', instance }),
      deleteInstance: (instance) => act('Could not delete the instance', { op: 'delete_instances', ids: [instance] }),
    }),
    [busy, disabled, act, drag, store, mine, documentName],
  )
  return { outputs, edits }
}
