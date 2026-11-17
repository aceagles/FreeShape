import './files.css'

/** What an upload in flight shows: the file, how far it is, and Cancel. `total` is 0 until the browser reports it. */
export interface UploadState {
  name: string
  sent: number
  total: number
}

/** The percent sent, whole; 0 until a total is known. */
export const percentOf = (u: UploadState): number => (u.total > 0 ? Math.min(100, Math.floor((u.sent / u.total) * 100)) : 0)

/** A progress bar with the file's name and a Cancel button (card I11.8). At 100 % the bytes are sent and the server is still importing. */
export function UploadProgress({ upload, onCancel }: { upload: UploadState; onCancel: () => void }) {
  const pct = percentOf(upload)
  return (
    <div className="up" role="status" data-testid="upload-progress">
      <div className="up-text">
        <span className="up-name">{pct >= 100 ? `Importing ${upload.name}…` : `Uploading ${upload.name}…`}</span>
        <span className="up-pct">{pct} %</span>
      </div>
      <div className="up-track" role="progressbar" aria-label={`Uploading ${upload.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <div className="up-bar" style={{ width: `${pct}%` }} />
      </div>
      <button type="button" className="up-cancel" onClick={onCancel}>
        Cancel
      </button>
    </div>
  )
}
