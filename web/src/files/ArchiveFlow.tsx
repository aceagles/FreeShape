import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ConfirmDialog } from '../shell/Modal'
import { UploadProgress } from './UploadProgress'
import type { UploadState } from './UploadProgress'
import { ARCHIVE_EXTENSION, archiveDownloadFailure, archiveUploadFailure, archiveUploadSupported, downloadArchive, isUnsaved, noteArchiveUnsupported, uploadArchive } from './filesApi'
import type { ArchiveReply } from './filesApi'
import { ApiError } from '../net/api'

type Message = { title: string; text: string }

/**
 * Downloading a Document as one `.fsdoc` (card I11.10), for the home screen's row menu and the Document's own menus. `start` asks the
 * server for the saved folder; a Document open here with unsaved edits answers 409, and the person is asked "Save and download?"
 * before the request is made again with `save=1` (as `client`). A refusal is a message. `readOnly` says the Document opens read-only
 * here: it is downloaded as it was last saved, with a note, and `save=1` is never sent for it (the server would answer 423).
 * `element` is what the flow draws (the question and the messages): render it once.
 */
export function useDocumentDownload(client: string): { start: (doc: { id: string; name: string }, readOnly?: boolean) => void; busy: boolean; element: ReactNode } {
  const [ask, setAsk] = useState<{ doc: { id: string; name: string }; readOnly: boolean } | null>(null)
  const [message, setMessage] = useState<Message | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async (doc: { id: string; name: string }, readOnly: boolean, save: boolean) => {
    setBusy(true)
    try {
      const got = await downloadArchive(doc.id, { save, client })
      if (readOnly) {
        setMessage({ title: `Downloaded ${got.name}`, text: `"${doc.name}" is open read-only here, so the file holds what was last saved. Edits another program has not saved yet are not in it.` })
      }
    } catch (e) {
      if (!save && isUnsaved(e)) setAsk({ doc, readOnly })
      else setMessage({ title: `Could not download "${doc.name}"`, text: archiveDownloadFailure(e, doc.name) })
    } finally {
      setBusy(false)
    }
  }

  const element = (
    <>
      {ask !== null && (
        <ConfirmDialog
          title="Save and download?"
          message={`"${ask.doc.name}" has changes that are not saved. The file holds only what is saved. Save the Document, then download it?`}
          confirm={{ label: 'Save and download' }}
          onConfirm={() => {
            const a = ask
            setAsk(null)
            void run(a.doc, a.readOnly, true)
          }}
          onCancel={() => setAsk(null)}
        />
      )}
      {message !== null && <ConfirmDialog title={message.title} message={message.text} confirm={{ label: 'OK' }} cancelLabel={null} onConfirm={() => setMessage(null)} onCancel={() => setMessage(null)} />}
    </>
  )
  return { start: (doc, readOnly = false) => void run(doc, readOnly, false), busy, element }
}

/**
 * Opening a `.fsdoc` from the device (card I11.10): `pick` opens the file picker; the chosen file goes up with the progress bar and
 * Cancel, and `onCreated` gets the new Document (the upload does not open it). A refusal is a message with the server's reason.
 * `supported` is false on a server that serves one Document (it cannot take an archive), null until that is known: hide the action
 * unless it is true. With `probe` false it is not asked (nothing is sent), so `supported` stays null. `element` holds the hidden file input, the progress and the messages: render it once.
 */
export function useArchiveOpen(onCreated: (made: ArchiveReply) => void, probe = true): { pick: () => void; supported: boolean | null; busy: boolean; element: ReactNode } {
  const [supported, setSupported] = useState<boolean | null>(null)
  const [upload, setUpload] = useState<UploadState | null>(null)
  const [message, setMessage] = useState<Message | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const ctl = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!probe) return
    let live = true
    void archiveUploadSupported().then((ok) => live && setSupported(ok))
    return () => {
      live = false
    }
  }, [probe])
  useEffect(() => () => ctl.current?.abort(), [])

  const send = async (file: File) => {
    const c = new AbortController()
    ctl.current = c
    setUpload({ name: file.name, sent: 0, total: file.size })
    try {
      const made = await uploadArchive(file, { signal: c.signal, onProgress: (sent, total) => setUpload({ name: file.name, sent, total }) })
      setUpload(null)
      onCreated(made)
    } catch (e) {
      setUpload(null)
      if (e instanceof DOMException && e.name === 'AbortError') return
      if (e instanceof ApiError && e.status === 404) {
        noteArchiveUnsupported()
        setSupported(false)
        setMessage({ title: 'This server cannot open an archive', text: 'It serves a single Document, not a folder of Documents, so a file cannot be added to it.' })
        return
      }
      setMessage({ title: `Could not open "${file.name}"`, text: archiveUploadFailure(e, file.name) })
    } finally {
      ctl.current = null
    }
  }

  const element = (
    <>
      {/* only while the action is offered: in Tablet mode inside a Document nothing native may sit on the page (the number pad's rule) */}
      {supported === true && (
        <input
          ref={input}
          type="file"
          hidden
          accept={ARCHIVE_EXTENSION}
          data-testid="archive-input"
          onChange={(e) => {
            const file = e.currentTarget.files?.[0]
            e.currentTarget.value = ''
            if (file !== undefined) void send(file)
          }}
        />
      )}
      {upload !== null && (
        <div className="export-saved up-note" data-testid="archive-upload">
          <UploadProgress upload={upload} onCancel={() => ctl.current?.abort()} />
        </div>
      )}
      {message !== null && <ConfirmDialog title={message.title} message={message.text} confirm={{ label: 'OK' }} cancelLabel={null} onConfirm={() => setMessage(null)} onCancel={() => setMessage(null)} />}
    </>
  )
  return { pick: () => input.current?.click(), supported, busy: upload !== null, element }
}
