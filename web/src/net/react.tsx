import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { ConnectionState, ConnectionStore } from './connection'
import { describeRtt } from './rtt'
import './net.css'

/** The connection's state, re-rendering the component whenever it changes. */
export function useConnection(store: ConnectionStore): ConnectionState {
  return useSyncExternalStore(store.subscribe, store.getState)
}

/** Call `refetch` when `tab`'s revision changes (not on the first value). The latest `refetch` is always used. */
export function useRefetchOnRevision(store: ConnectionStore, tab: string, refetch: (revision: number) => void): void {
  const latest = useRef(refetch)
  useEffect(() => {
    latest.current = refetch
  })
  useEffect(() => store.onRevision(tab, (rev) => latest.current(rev)), [store, tab])
}

/** The connection state and the debug panel: median and worst round trip, with a button that copies them. */
export function ConnectionPanel({ store }: { store: ConnectionStore }) {
  const state = useConnection(store)
  const [copied, setCopied] = useState<'idle' | 'done' | 'failed'>('idle')
  const summary = store.rttSummary()
  const text = describeRtt(summary)
  const copy = () => {
    navigator.clipboard.writeText(text).then(
      () => setCopied('done'),
      () => setCopied('failed'),
    )
  }
  return (
    <section className="net-panel" aria-label="Connection">
      <p data-testid="connection" className={state.connected ? 'net-up' : 'net-down'}>
        {state.connected ? `Connected to ${state.document?.name ?? 'the server'}` : 'Not connected, retrying'}
      </p>
      <dl className="net-rtt">
        <dt>Median</dt>
        <dd data-testid="rtt-median">{summary.median === null ? '-' : `${summary.median.toFixed(1)} ms`}</dd>
        <dt>Worst</dt>
        <dd data-testid="rtt-worst">{summary.worst === null ? '-' : `${summary.worst.toFixed(1)} ms`}</dd>
        <dt>Samples</dt>
        <dd>{summary.count}</dd>
      </dl>
      <button type="button" onClick={copy}>
        Copy round trips
      </button>
      <span role="status" className="net-copied">
        {copied === 'done' ? 'Copied' : copied === 'failed' ? 'Could not copy' : ''}
      </span>
    </section>
  )
}
