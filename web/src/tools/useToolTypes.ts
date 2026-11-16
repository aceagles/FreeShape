import { useEffect, useState } from 'react'
import type { FeatureType } from './toolsApi'
import { fetchFeatureTypes } from './toolsApi'
import type { Pick } from './picks'

/** What a tool can do now. 'ok' starts; 'dimmed' cannot start with this selection; 'desktop' has a dialog the client cannot draw; 'off' is not available (no answer yet, or the server does not have it). */
export type ToolState = 'ok' | 'dimmed' | 'desktop' | 'off'

export type ToolTypes = ReadonlyMap<string, FeatureType>

export const stateOf = (types: ToolTypes | null, id: string): ToolState => {
  const t = types?.get(id)
  if (t === undefined) return 'off'
  if (!t.standard) return 'desktop'
  return t.can_start ? 'ok' : 'dimmed'
}

/**
 * The feature types and what each can do with `picks`, asked of the server again when the Part Studio, its revision or
 * the picks change. `tab` null (no Part Studio on screen) or `paused` (a dialog is open and uses the selection) asks
 * nothing; an answer that fails leaves the last one. null until the first answer.
 */
export function useToolTypes(documentId: string | null, tab: string | null, revision: number, picks: readonly Pick[], paused: boolean): ToolTypes | null {
  const [types, setTypes] = useState<{ key: string; map: ToolTypes } | null>(null)
  const query = JSON.stringify(picks)
  const key = `${documentId}/${tab}`
  useEffect(() => {
    if (documentId === null || tab === null || paused) return
    const ctl = new AbortController()
    fetchFeatureTypes(documentId, tab, JSON.parse(query) as Pick[], ctl.signal).then(
      (r) => setTypes({ key, map: new Map(r.types.map((t) => [t.type, t])) }),
      () => {
        // no answer: what was known stays
      },
    )
    return () => ctl.abort()
  }, [documentId, tab, revision, query, paused, key])
  return types !== null && types.key === key ? types.map : null
}
