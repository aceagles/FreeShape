import { useEffect, useState } from 'react'
import type { ConnectionStore } from '../net/connection'
import { ConnectionPanel } from '../net/react'
import { statusLabel, useLinkStatus } from './status'

/** The connection state in the top bar; a tap opens the connection details (round trips). */
export function LinkStatusButton({ store, open, onToggle }: { store: ConnectionStore; open: boolean; onToggle: () => void }) {
  const status = useLinkStatus(store)
  return (
    <button
      type="button"
      className="link-status"
      data-status={status}
      data-testid="link-status"
      aria-label={`${statusLabel[status]}, connection details`}
      aria-expanded={open}
      onClick={onToggle}
    >
      <span className="link-dot" />
      <span>{statusLabel[status]}</span>
    </button>
  )
}

/** The open/closed state of the connection details, closing on Escape. */
export function useDebugPanel(): { open: boolean; toggle: () => void } {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!open) return
    const on = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [open])
  return { open, toggle: () => setOpen((o) => !o) }
}

export function DebugPopover({ store, version }: { store: ConnectionStore; version?: string }) {
  return (
    <div className="debug-popover" data-testid="debug-panel">
      <ConnectionPanel store={store} />
      {version && (
        <p className="debug-version" data-testid="version">
          Server version {version}
        </p>
      )}
    </div>
  )
}
