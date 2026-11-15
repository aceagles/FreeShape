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
 * The viewport for the first Part Studio tab of the document: loads its outputs, and loads them again whenever the
 * tab's revision rises. Tab selection comes with the app shell; until then the first Part Studio is shown.
 */
export function StudioViewport({ store }: { store: ConnectionStore }) {
  const { tabs } = useConnection(store)
  const tab = tabs.find((t) => t.kind === 'studio')?.id ?? null
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
