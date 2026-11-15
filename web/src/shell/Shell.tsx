import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { ConnectionStore } from '../net/connection'
import { ConnectionPanel, useConnection } from '../net/react'
import { StudioViewport } from '../viewport/StudioViewport'
import { DialogArea } from './DialogArea'
import { FeaturePanel } from './FeaturePanel'
import { ShellContext } from './ShellContext'
import type { ShellApi } from './ShellContext'
import { ToolStrip } from './ToolStrip'
import { TopBar } from './TopBar'
import { columnWidth } from './layout'
import { tokenVars } from './vars'
import './shell.css'

function useWindowWidth(): number {
  const [width, setWidth] = useState(() => window.innerWidth)
  useEffect(() => {
    const on = () => setWidth(window.innerWidth)
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  return width
}

interface DialogState {
  title: string
  content: ReactNode
}

declare global {
  interface Window {
    /** Hands tests (and the screenshot script) the shell's API; nothing in the client reads it. */
    __freeshape?: { shell: ShellApi }
  }
}

/**
 * The app shell, built to web/design/layout.html: top bar, tool strip, and under them the feature panel column
 * (with the dialog area over it) beside the viewport. Regions carry the layout's `data-region` names.
 * The connection state in the top bar opens the debug panel (round trips) over the viewport.
 */
export function Shell({ store, version }: { store: ConnectionStore; version?: string }) {
  const { tabs } = useConnection(store)
  const [selected, setSelected] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [dialog, setDialog] = useState<DialogState | null>(null)
  const [debugOpen, setDebugOpen] = useState(false)
  const width = useWindowWidth()

  const currentTab = tabs.some((t) => t.id === selected) ? selected : (tabs[0]?.id ?? null)
  const column = columnWidth(width, collapsed)

  const api = useMemo<ShellApi>(
    () => ({
      openDialog: (title, content) => {
        setCollapsed(false)
        setDialog({ title, content })
      },
      closeDialog: () => setDialog(null),
      dialogOpen: dialog !== null,
      panelCollapsed: collapsed,
      setPanelCollapsed: setCollapsed,
    }),
    [dialog, collapsed],
  )
  useEffect(() => {
    window.__freeshape = { shell: api }
    return () => {
      delete window.__freeshape
    }
  }, [api])
  useEffect(() => {
    if (!debugOpen) return
    const on = (e: KeyboardEvent) => e.key === 'Escape' && setDebugOpen(false)
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [debugOpen])
  const toggleDebug = useCallback(() => setDebugOpen((o) => !o), [])

  return (
    <ShellContext.Provider value={api}>
      <div className="shell" style={tokenVars()} data-testid="shell">
        <TopBar store={store} currentTab={currentTab} onSelectTab={setSelected} debugOpen={debugOpen} onToggleDebug={toggleDebug} />
        <ToolStrip />
        <div className="shell-main">
          <div className="left-column" data-region="left-column" data-testid="left-column" style={{ width: column }}>
            <FeaturePanel
              store={store}
              collapsed={collapsed}
              locked={dialog !== null}
              currentTab={currentTab}
              onCollapsedChange={setCollapsed}
            />
            {dialog && (
              <DialogArea title={dialog.title} onClose={() => setDialog(null)}>
                {dialog.content}
              </DialogArea>
            )}
          </div>
          <div className="shell-viewport" data-region="viewport" data-testid="shell-viewport">
            <StudioViewport store={store} tab={currentTab} />
            {/* The selection chip (S4) is placed here, top left. */}
            <div className="chip-slot" data-testid="chip-slot" />
          </div>
        </div>
        {debugOpen && (
          <div className="debug-popover" data-testid="debug-panel">
            <ConnectionPanel store={store} />
            {version && <p className="debug-version" data-testid="version">Server version {version}</p>}
          </div>
        )}
      </div>
    </ShellContext.Provider>
  )
}
