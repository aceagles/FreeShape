import { createContext, useContext, useState } from 'react'
import type { ReactNode } from 'react'
import { Icon, paths } from './icons'

const DockContext = createContext<HTMLElement | null>(null)

/** The element at the foot of the dialog column where the pad docks, or null outside a dialog area (or before it mounts). */
export function useDialogDock(): HTMLElement | null {
  return useContext(DockContext)
}

/**
 * The dialog area: covers the feature panel exactly (the panel column's own box, so the viewport never changes
 * width). It is empty; feature dialogs (S3), the pad (S5) and the rest fill `children`. Opened through useShell().
 */
export function DialogArea({ title, onClose, children }: { title: string; onClose: () => void; children?: ReactNode }) {
  const [dock, setDock] = useState<HTMLElement | null>(null)
  return (
    <div className="dialog-area" data-region="dialog" role="dialog" aria-label={title || 'Dialog'}>
      <div className="dialog-header">
        <div className="dialog-title">{title}</div>
        <button type="button" className="icon-button dialog-close" aria-label="Close dialog" onClick={onClose}>
          <Icon d={paths.close} />
        </button>
      </div>
      <DockContext.Provider value={dock}>
        <div className="dialog-body">{children}</div>
      </DockContext.Provider>
      {/* the numeric and expression pad (src/pad) docks here, at the foot of the column */}
      <div className="dialog-dock" data-region="dock" ref={setDock} />
    </div>
  )
}
