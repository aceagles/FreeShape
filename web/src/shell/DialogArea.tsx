import { createContext, useContext, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Icon, paths } from './icons'
import { useMode } from './mode'

const DockContext = createContext<HTMLElement | null>(null)

/** The element at the foot of the dialog column where the pad docks, or null outside a dialog area (or before it mounts). */
export function useDialogDock(): HTMLElement | null {
  return useContext(DockContext)
}

const HostContext = createContext<HTMLElement | null>(null)

/** The viewport element a dialog floats over in Desktop mode. The shell supplies it. */
export const DialogHost = HostContext.Provider

/** Where a floating dialog sits in the viewport, and how wide it is (Desktop mode). */
export const FLOAT = { left: 12, top: 12, width: 300 } as const

/**
 * The dialog area. Tablet mode: it covers the feature panel exactly (the panel column's own box, so the viewport never
 * changes width). Desktop mode: it floats over the viewport at its top left (12, 12), 300 px wide, and the left column stays
 * visible and usable beside it (`width` widens a floating one: the file explorer). It is empty; feature dialogs (S3), the pad (S5) and the rest fill `children`. Opened through useShell().
 */
export function DialogArea({ title, onClose, actions, children, width }: { title: string; onClose: () => void; actions?: ReactNode; children?: ReactNode; width?: number }) {
  const [dock, setDock] = useState<HTMLElement | null>(null)
  const host = useContext(HostContext)
  const floating = useMode() === 'desktop' && host !== null
  const area = (
    <div
      className={floating ? 'dialog-area dialog-floating' : 'dialog-area'}
      data-region="dialog"
      role="dialog"
      aria-label={title || 'Dialog'}
      style={floating ? { left: FLOAT.left, top: FLOAT.top, width: width ?? FLOAT.width } : undefined}
    >
      <div className="dialog-header">
        <div className="dialog-title">{title}</div>
        {/* a dialog with its own buttons (a feature dialog: Cancel and Accept) replaces the close button */}
        {actions ?? (
          <button type="button" className="icon-button dialog-close" aria-label="Close dialog" onClick={onClose}>
            <Icon d={paths.close} />
          </button>
        )}
      </div>
      <DockContext.Provider value={dock}>
        <div className="dialog-body">{children}</div>
      </DockContext.Provider>
      {/* the numeric and expression pad (src/pad) docks here, at the foot of the column */}
      <div className="dialog-dock" data-region="dock" ref={setDock} />
    </div>
  )
  return floating ? createPortal(area, host) : area
}
