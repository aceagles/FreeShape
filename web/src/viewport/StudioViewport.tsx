import { useCallback, useEffect, useState } from 'react'
import type { ConnectionStore } from '../net/connection'
import { fetchOutputsBytes } from '../net/api'
import { useConnection, useRefetchOnRevision } from '../net/react'
import { decodeOutputs, isStudio } from '../wire'
import type { StudioOutputs } from '../wire'
import { Viewport } from './Viewport'

/** Fetch and decode a tab's outputs from `/documents/<document>/tabs/<tab>/outputs`. */
export async function fetchStudio(document: string, tab: string, signal?: AbortSignal): Promise<StudioOutputs | null> {
  const out = decodeOutputs(await fetchOutputsBytes(document, tab, signal))
  return isStudio(out) ? out : null
}

/**
 * The viewport for one tab of the document: loads its outputs, and loads them again whenever the tab's revision
 * rises. `tab` is the app shell's current tab; without it the first Part Studio is shown. A tab that is not a Part
 * Studio (an Assembly) leaves the last view in place until the Assembly view exists.
 */
export function StudioViewport({ store, tab: chosen }: { store: ConnectionStore; tab?: string | null }) {
  const { tabs, document, epoch } = useConnection(store)
  const documentId = document?.id ?? null
  const tab = chosen === undefined ? (tabs.find((t) => t.kind === 'studio')?.id ?? null) : chosen
  const [outputs, setOutputs] = useState<StudioOutputs | null>(null)
  const load = useCallback(
    (id: string) => {
      if (!documentId) return
      fetchStudio(documentId, id).then(
        (o) => o && setOutputs(o),
        () => {},
      )
    },
    [documentId],
  )
  // `epoch` rises when a reconnect or a take-over may have changed what the tab holds
  useEffect(() => {
    if (tab) load(tab)
  }, [tab, load, epoch])
  useRefetchOnRevision(store, tab ?? '', () => tab && load(tab))
  return <Viewport outputs={outputs} />
}
