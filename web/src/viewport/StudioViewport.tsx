import { useCallback, useEffect, useState } from 'react'
import type { ConnectionStore } from '../net/connection'
import { useConnection, useRefetchOnRevision } from '../net/react'
import { decodeOutputs, isStudio } from '../wire'
import type { StudioOutputs } from '../wire'
import { Viewport } from './Viewport'

/** Fetch and decode a tab's outputs. */
export async function fetchStudio(tab: string, signal?: AbortSignal): Promise<StudioOutputs | null> {
  const res = await fetch(`/tabs/${encodeURIComponent(tab)}/outputs`, { signal })
  if (!res.ok) throw new Error(`outputs of ${tab}: HTTP ${res.status}`)
  const out = decodeOutputs(await res.arrayBuffer())
  return isStudio(out) ? out : null
}

/**
 * The viewport for one tab of the document: loads its outputs, and loads them again whenever the tab's revision
 * rises. `tab` is the app shell's current tab; without it the first Part Studio is shown. A tab that is not a Part
 * Studio (an Assembly) leaves the last view in place until the Assembly view exists.
 */
export function StudioViewport({ store, tab: chosen }: { store: ConnectionStore; tab?: string | null }) {
  const { tabs } = useConnection(store)
  const tab = chosen === undefined ? (tabs.find((t) => t.kind === 'studio')?.id ?? null) : chosen
  const [outputs, setOutputs] = useState<StudioOutputs | null>(null)
  const load = useCallback(
    (id: string) => {
      fetchStudio(id).then(
        (o) => o && setOutputs(o),
        () => {},
      )
    },
    [],
  )
  useEffect(() => {
    if (tab) load(tab)
  }, [tab, load])
  useRefetchOnRevision(store, tab ?? '', () => tab && load(tab))
  return <Viewport outputs={outputs} />
}
