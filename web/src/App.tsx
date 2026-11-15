import { useEffect, useState } from 'react'
import { fetchHealth } from './health'
import type { ConnectionStore } from './net/connection'
import { Shell } from './shell/Shell'

/** The client: the app shell (src/shell) over the live connection. The server version from /health shows in the debug panel. */
export function App({ store }: { store: ConnectionStore }) {
  const [version, setVersion] = useState<string | undefined>()
  useEffect(() => {
    let live = true
    fetchHealth().then(
      (h) => live && setVersion(h.version),
      () => {},
    )
    return () => {
      live = false
    }
  }, [])
  return <Shell store={store} version={version} />
}
