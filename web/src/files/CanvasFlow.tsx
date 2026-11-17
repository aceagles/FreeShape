import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { UploadTooLarge } from '../net/upload'
import { ConfirmDialog } from '../shell/Modal'
import { UploadProgress } from './UploadProgress'
import type { UploadState } from './UploadProgress'
import { CANVAS_ACCEPT, uploadCanvasImage } from './canvasApi'
import type { CanvasUpload } from './canvasApi'

/** What to tell the person about an image that did not go up: the server's own text (it says what the file is), or the size. */
export function canvasUploadFailure(e: unknown, file: string): string {
  if (e instanceof UploadTooLarge) return `${file} is ${(e.size / 1048576).toFixed(1)} MB; an image can be at most ${(e.limit / 1048576).toFixed(0)} MB.`
  return e instanceof Error ? e.message : `${file} could not be uploaded.`
}

/**
 * Picking an image for a Canvas (card I12.5): `pick` opens the device's file picker for a PNG or JPEG; the chosen file goes up
 * (I11.8's `uploadFile` through `uploadCanvasImage`) with the progress bar and Cancel, and `onUploaded` gets the reply to open the
 * dialog with. A refusal is a message with the server's reason. `element` is the progress and the message: render it once.
 *
 * The file input is made when `pick` is called and is never put on the page: in Tablet mode nothing native may sit in the page while
 * the number pad is (web/e2e/s5_pad.spec.ts), and a hidden input that is always mounted fails that check.
 */
export function useCanvasImage(target: { document: string; tab: string; client: string } | null, onUploaded: (up: CanvasUpload) => void): { pick: () => void; busy: boolean; element: ReactNode } {
  const [upload, setUpload] = useState<UploadState | null>(null)
  const [message, setMessage] = useState<{ title: string; text: string } | null>(null)
  const ctl = useRef<AbortController | null>(null)
  useEffect(() => () => ctl.current?.abort(), [])

  const send = async (file: File, to: NonNullable<typeof target>) => {
    const c = new AbortController()
    ctl.current = c
    setUpload({ name: file.name, sent: 0, total: file.size })
    try {
      const up = await uploadCanvasImage(to.document, to.tab, file, to.client, { signal: c.signal, onProgress: (sent, total) => setUpload({ name: file.name, sent, total }) })
      setUpload(null)
      onUploaded(up)
    } catch (e) {
      setUpload(null)
      if (e instanceof DOMException && e.name === 'AbortError') return
      setMessage({ title: `Could not use "${file.name}"`, text: canvasUploadFailure(e, file.name) })
    } finally {
      ctl.current = null
    }
  }

  const pick = () => {
    if (target === null || ctl.current !== null) return
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = CANVAS_ACCEPT
    input.addEventListener('change', () => {
      const file = input.files?.[0]
      if (file !== undefined) void send(file, target)
    })
    input.click()
  }

  const element = (
    <>
      {upload !== null && (
        <div className="export-saved up-note" data-testid="canvas-upload">
          <UploadProgress upload={upload} onCancel={() => ctl.current?.abort()} />
        </div>
      )}
      {message !== null && <ConfirmDialog title={message.title} message={message.text} confirm={{ label: 'OK' }} cancelLabel={null} onConfirm={() => setMessage(null)} onCancel={() => setMessage(null)} />}
    </>
  )
  return { pick, busy: upload !== null, element }
}
