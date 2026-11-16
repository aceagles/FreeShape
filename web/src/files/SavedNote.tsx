import { useEffect } from 'react'
import './files.css'

/** Says where a saved export went ("Saved bracket.stl to shared"). It goes after a while, or when tapped away. */
export function SavedNote({ text, onDismiss }: { text: string; onDismiss: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 10_000)
    return () => clearTimeout(t)
  }, [text, onDismiss])
  return (
    <div className="export-saved" role="status" data-testid="export-saved">
      <span>{text}</span>
      <button type="button" aria-label="Dismiss" onClick={onDismiss}>
        OK
      </button>
    </div>
  )
}
