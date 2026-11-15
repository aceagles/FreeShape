import { useEffect, useState } from 'react'
import type { ConnectionStore } from '../net/connection'
import { useConnection } from '../net/react'

export type LinkStatus = 'connected' | 'reconnecting' | 'lost'

/** The store keeps retrying for ever; the top bar calls the link lost once it has been down this long. */
export const LOST_AFTER_MS = 15_000

export const statusLabel: Record<LinkStatus, string> = {
  connected: 'Connected',
  reconnecting: 'Reconnecting',
  lost: 'Connection lost',
}

/** Connected, reconnecting (the socket is down, the store is retrying) or lost (down for LOST_AFTER_MS). */
export function useLinkStatus(store: ConnectionStore, lostAfterMs: number = LOST_AFTER_MS): LinkStatus {
  const { connected } = useConnection(store)
  const [timedOut, setTimedOut] = useState(false)
  useEffect(() => {
    if (connected) return
    const timer = setTimeout(() => setTimedOut(true), lostAfterMs)
    return () => {
      clearTimeout(timer)
      setTimedOut(false)
    }
  }, [connected, lostAfterMs])
  return connected ? 'connected' : timedOut ? 'lost' : 'reconnecting'
}
