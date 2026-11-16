import { useCallback, useEffect, useRef, useState } from 'react'
import type { ConnectionStore } from '../net/connection'
import { fetchOutputsBytes } from '../net/api'
import { useConnection, useRefetchOnRevision } from '../net/react'
import { decodeOutputs, isStudio } from '../wire'
import type { StudioOutputs } from '../wire'

/** Fetch and decode a tab's outputs from `/documents/<document>/tabs/<tab>/outputs`. */
export async function fetchStudio(document: string, tab: string, signal?: AbortSignal): Promise<StudioOutputs | null> {
  const out = decodeOutputs(await fetchOutputsBytes(document, tab, signal))
  return isStudio(out) ? out : null
}

/**
 * The outputs of the Part Studio `tab`, loaded again whenever the tab's revision rises (or `epoch` does: a reconnect
 * or take-over). The viewport and the feature panel both read this one copy. A tab that is not a Part Studio leaves
 * the last outputs in place (see `outputs.tab`: compare it with the tab you mean). A slower, older answer never
 * replaces a newer one.
 */
export function useStudioOutputs(store: ConnectionStore, tab: string | null): StudioOutputs | null {
  const { document, epoch } = useConnection(store)
  const documentId = document?.id ?? null
  const [outputs, setOutputs] = useState<StudioOutputs | null>(null)
  const latest = useRef(0)
  const load = useCallback(
    (id: string) => {
      if (!documentId) return
      const mine = ++latest.current
      fetchStudio(documentId, id).then(
        (o) => {
          if (o && mine === latest.current) setOutputs(o)
        },
        () => {},
      )
    },
    [documentId],
  )
  useEffect(() => {
    if (tab) load(tab)
  }, [tab, load, epoch])
  useRefetchOnRevision(store, tab ?? '', () => tab && load(tab))
  return outputs
}
