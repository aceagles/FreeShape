import type { ReactNode } from 'react'
import { Icon, paths } from './icons'

/**
 * The dialog area: covers the feature panel exactly (the panel column's own box, so the viewport never changes
 * width). It is empty; feature dialogs (S3), the pad (S5) and the rest fill `children`. Opened through useShell().
 */
export function DialogArea({ title, onClose, children }: { title: string; onClose: () => void; children?: ReactNode }) {
  return (
    <div className="dialog-area" data-region="dialog" role="dialog" aria-label={title || 'Dialog'}>
      <div className="dialog-header">
        <div className="dialog-title">{title}</div>
        <button type="button" className="icon-button dialog-close" aria-label="Close dialog" onClick={onClose}>
          <Icon d={paths.close} />
        </button>
      </div>
      <div className="dialog-body">{children}</div>
    </div>
  )
}
