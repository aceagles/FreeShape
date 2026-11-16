import { useState } from 'react'
import { Modal, ModalButtons } from './Modal'

interface Props {
  title: string
  label: string
  initial?: string
  confirmLabel: string
  maxLength?: number
  /** Called with the trimmed text. Return a message to show under the field (the dialog stays), or nothing to close. */
  onSubmit: (value: string) => Promise<string | void> | string | void
  onCancel: () => void
}

/**
 * A one-line text dialog (the new Document's name, a tab's new name). The field is a plain text input: it sits in
 * the top half of the page, away from the view, so the iPad keyboard does not cover it. Enter submits.
 */
export function TextPrompt({ title, label, initial = '', confirmLabel, maxLength = 100, onSubmit, onCancel }: Props) {
  const [value, setValue] = useState(initial)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const trimmed = value.trim()
  const submit = async () => {
    if (!trimmed || busy) return
    setBusy(true)
    setError(null)
    try {
      const problem = await onSubmit(trimmed)
      if (typeof problem === 'string') setError(problem)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={title} onClose={onCancel}>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <label className="modal-field">
          <span className="modal-label">{label}</span>
          <input
            className="modal-input"
            type="text"
            value={value}
            maxLength={maxLength}
            autoFocus
            autoCapitalize="words"
            autoCorrect="off"
            spellCheck={false}
            enterKeyHint="done"
            onChange={(e) => setValue(e.target.value)}
            onFocus={(e) => e.target.select()}
          />
        </label>
        {error && (
          <p className="modal-error" role="alert">
            {error}
          </p>
        )}
        <ModalButtons
          buttons={[
            { label: 'Cancel', onClick: onCancel },
            // the real submit is the form's: Enter and this button both go through onSubmit above
            { label: confirmLabel, onClick: () => void submit(), kind: 'primary', disabled: !trimmed || busy },
          ]}
        />
      </form>
    </Modal>
  )
}
