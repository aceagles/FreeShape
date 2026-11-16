import { useCallback, useMemo, useRef, useState } from 'react'
import type { ConnectionStore } from '../net/connection'
import { explainError } from '../shell/errors'

export interface FeatureEdits {
  /** An edit is on its way to the server. Edits are sent one at a time, each on the revision the last one made. */
  busy: boolean
  /** No edit can be made: the Document is read-only here, or there is no Part Studio tab. */
  disabled: boolean
  /** Rename; resolves with a message in plain words when the server refused (the rename dialog shows it). */
  rename(feature: string, name: string): Promise<string | void>
  setSuppressed(feature: string, value: boolean): void
  deleteFeature(feature: string): Promise<boolean>
  /** `index` is the number of features that build (outputs.rollback). Resolves false when the edit failed. */
  setRollback(index: number): Promise<boolean>
  /** The eye of a sketch, plane or axis row (set_features_hidden: a view setting, no undo step). */
  setFeatureHidden(feature: string, hidden: boolean): void
  /** The eye of a part (set_part_override with `hidden`). */
  setPartHidden(part: string, hidden: boolean): void
  /** Rename a part; resolves with a message in plain words when the server refused (the rename dialog shows it). */
  renamePart(part: string, name: string): Promise<string | void>
  /** `color` is r g b in 0..1, or null for the part's own colour. Resolves false when the edit failed. */
  setPartColor(part: string, color: readonly number[] | null): Promise<boolean>
  undo(): void
  redo(): void
}

interface Options {
  store: ConnectionStore
  /** The Part Studio tab being edited, or null. */
  tab: string | null
  readOnly: boolean
  /** The Document's name, for the words of a refusal. */
  documentName: string
  /** A refusal to show the person. */
  onError: (title: string, message: string) => void
}

/**
 * The feature panel's and the top bar's edits of one Part Studio: rename_feature, set_suppressed, delete_features,
 * set_rollback, undo and redo, and the parts' set_part_override (hidden, name, color), as the engine contract names them. The panel redraws from the outputs the next
 * revision brings, never from what was sent.
 */
export function useFeatureEdits({ store, tab, readOnly, documentName, onError }: Options): FeatureEdits {
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const disabled = readOnly || tab === null

  /** Sends one edit; null when it was not sent, else the refusal (undefined when it went through). */
  const send = useCallback(
    async (edit: Record<string, unknown>): Promise<{ sent: false } | { sent: true; error: unknown }> => {
      if (tab === null || inFlight.current) return { sent: false }
      inFlight.current = true
      setBusy(true)
      try {
        await store.edit(tab, edit)
        return { sent: true, error: undefined }
      } catch (e) {
        return { sent: true, error: e }
      } finally {
        inFlight.current = false
        setBusy(false)
      }
    },
    [store, tab],
  )

  return useMemo<FeatureEdits>(() => {
    const act = async (title: string, edit: Record<string, unknown>): Promise<boolean> => {
      if (disabled) return false
      const r = await send(edit)
      if (!r.sent) return false
      if (r.error === undefined) return true
      onError(title, explainError(r.error, documentName))
      return false
    }
    return {
      busy,
      disabled,
      async rename(feature, name) {
        if (disabled) return
        const r = await send({ op: 'rename_feature', feature, name })
        if (!r.sent) return 'Another change is still being saved. Try again in a moment.'
        if (r.error !== undefined) return explainError(r.error, documentName)
      },
      setSuppressed: (feature, value) => void act(value ? 'Could not suppress the feature' : 'Could not unsuppress the feature', { op: 'set_suppressed', feature, value }),
      deleteFeature: (feature) => act('Could not delete the feature', { op: 'delete_features', ids: [feature] }),
      setFeatureHidden: (feature, hidden) => void act(hidden ? 'Could not hide the feature' : 'Could not show the feature', { op: 'set_features_hidden', ids: [feature], value: hidden }),
      setPartHidden: (part, hidden) => void act(hidden ? 'Could not hide the part' : 'Could not show the part', { op: 'set_part_override', part, hidden }),
      async renamePart(part, name) {
        if (disabled) return
        const r = await send({ op: 'set_part_override', part, name })
        if (!r.sent) return 'Another change is still being saved. Try again in a moment.'
        if (r.error !== undefined) return explainError(r.error, documentName)
      },
      setPartColor: (part, color) => act('Could not change the colour', { op: 'set_part_override', part, color: color === null ? null : [...color] }),
      setRollback: (index) => act('Could not move the rollback bar', { op: 'set_rollback', index }),
      undo: () => void act('Could not undo', { op: 'undo' }),
      redo: () => void act('Could not redo', { op: 'redo' }),
    }
  }, [busy, disabled, send, onError, documentName])
}
