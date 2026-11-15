import { useEffect, useState } from 'react'
import { fetchHealth } from './health'
import type { ConnectionStore } from './net/connection'
import { ConnectionPanel } from './net/react'
import { StudioViewport } from './viewport/StudioViewport'

type State = { kind: 'loading' } | { kind: 'ready'; version: string } | { kind: 'failed' }

/** Placeholder first page: shows the server version from /health. The app shell replaces it. */
export function App({ store }: { store?: ConnectionStore }) {
  const [state, setState] = useState<State>({ kind: 'loading' })
  useEffect(() => {
    let live = true
    fetchHealth().then(
      (h) => live && setState({ kind: 'ready', version: h.version }),
      () => live && setState({ kind: 'failed' }),
    )
    return () => {
      live = false
    }
  }, [])
  return (
    <main className="placeholder">
      {store && (
        <div className="placeholder-viewport">
          <StudioViewport store={store} />
        </div>
      )}
      <h1>FreeShape</h1>
      <p data-testid="version">
        {state.kind === 'loading' && 'Connecting to the server...'}
        {state.kind === 'ready' && `Server version ${state.version}`}
        {state.kind === 'failed' && 'The server did not answer.'}
      </p>
      {store && <ConnectionPanel store={store} />}
    </main>
  )
}
