import { useEffect } from 'react'
import type { ReactNode } from 'react'
import './modal.css'

/**
 * A small dialog over the whole page, in the top half so the iPad keyboard never covers it. Escape and a tap on the
 * scrim call `onClose`. Used for confirmations, messages and one-line text prompts (see ConfirmDialog, TextPrompt).
 */
export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [onClose])
  return (
    <div className="modal-scrim" data-testid="modal-scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <h2 className="modal-title">{title}</h2>
        {children}
      </div>
    </div>
  )
}

export interface ModalButton {
  label: string
  onClick: () => void
  /** `primary` is the filled accent button; `danger` is red; the rest are plain. */
  kind?: 'primary' | 'danger' | 'plain'
  disabled?: boolean
}

export function ModalButtons({ buttons }: { buttons: ModalButton[] }) {
  return (
    <div className="modal-buttons">
      {buttons.map((b) => (
        <button key={b.label} type="button" className="modal-button" data-kind={b.kind ?? 'plain'} disabled={b.disabled} onClick={b.onClick}>
          {b.label}
        </button>
      ))}
    </div>
  )
}

interface ConfirmProps {
  title: string
  message: ReactNode
  /** The button that goes ahead. */
  confirm: { label: string; danger?: boolean }
  /** A third choice between the two (Discard, when leaving with unsaved changes). */
  alternative?: { label: string; onClick: () => void; danger?: boolean }
  /** The label of the button that does nothing; null for a message with only the confirm button (OK). */
  cancelLabel?: string | null
  onConfirm: () => void
  onCancel: () => void
}

/** The one confirmation dialog: delete a tab, take over, leave with unsaved changes, and plain error messages. */
export function ConfirmDialog({ title, message, confirm, alternative, cancelLabel = 'Cancel', onConfirm, onCancel }: ConfirmProps) {
  const buttons: ModalButton[] = []
  if (cancelLabel !== null) buttons.push({ label: cancelLabel, onClick: onCancel })
  if (alternative) buttons.push({ label: alternative.label, onClick: alternative.onClick, kind: alternative.danger ? 'danger' : 'plain' })
  buttons.push({ label: confirm.label, onClick: onConfirm, kind: confirm.danger ? 'danger' : 'primary' })
  return (
    <Modal title={title} onClose={onCancel}>
      <p className="modal-message">{message}</p>
      <ModalButtons buttons={buttons} />
    </Modal>
  )
}
