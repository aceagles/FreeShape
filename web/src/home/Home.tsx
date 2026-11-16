import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError, closeDocument, createDocument, discardAutosave, listDocuments, openDocument, recoverDocument } from '../net/api'
import type { DocumentReply, DocumentRow } from '../net/api'
import type { ConnectionStore } from '../net/connection'
import { Icon, paths } from '../shell/icons'
import { DebugPopover, LinkStatusButton, useDebugPanel } from '../shell/LinkStatus'
import { ConfirmDialog } from '../shell/Modal'
import { TextPrompt } from '../shell/TextPrompt'
import { tokenVars } from '../shell/vars'
import { AutosaveDialog } from './AutosaveDialog'
import { formatModified, recoverMark, rowStatus } from './format'
import '../shell/shell.css'
import './home.css'

type Modal =
  | { kind: 'new' }
  | { kind: 'other'; blocker: { id: string; name: string }; wanted: { id: string; name: string } }
  | { kind: 'message'; title: string; text: string }
  | { kind: 'autosave'; reply: DocumentReply }

/** Newest first; a Document with no known time last. */
function byModified(a: DocumentRow, b: DocumentRow): number {
  return (b.modified ?? -1) - (a.modified ?? -1) || a.name.localeCompare(b.name)
}

/**
 * The home screen: the Documents the server knows, with when each was changed and who holds it, a New Document
 * action, and opening. `onOpened` gets the server's answer to the open call (read-only and the holder included).
 */
export function Home({
  store,
  version,
  notice,
  onOpened,
  now = Date.now,
}: {
  store: ConnectionStore
  version?: string
  notice?: string | null
  onOpened: (reply: DocumentReply) => void
  now?: () => number
}) {
  const [rows, setRows] = useState<DocumentRow[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [modal, setModal] = useState<Modal | null>(null)
  const debug = useDebugPanel()

  const load = useCallback(() => {
    listDocuments().then(
      (r) => {
        setRows(r)
        setLoadError(null)
      },
      (e: unknown) => setLoadError(e instanceof Error ? e.message : 'The Documents could not be listed.'),
    )
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const sorted = useMemo(() => (rows ? [...rows].sort(byModified) : null), [rows])

  const open = async (target: { id: string; name: string }) => {
    setBusy(target.id)
    try {
      const reply = await openDocument(target.id)
      // an autosave the server kept: ask, unless another program holds the lock (nothing can be recovered then)
      if (reply.autosave && !reply.read_only) setModal({ kind: 'autosave', reply })
      else onOpened(reply)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.openDocument) {
        setModal({ kind: 'other', blocker: e.openDocument, wanted: target })
      } else {
        setModal({ kind: 'message', title: `Could not open "${target.name}"`, text: e instanceof Error ? e.message : 'Something went wrong.' })
      }
      load()
    } finally {
      setBusy(null)
    }
  }

  /** Recover or discard the autosave of the Document the dialog is about, then go in (or say why not). */
  const answerAutosave = async (reply: DocumentReply, what: 'recover' | 'discard') => {
    const id = reply.document.id
    setModal(null)
    setBusy(id)
    try {
      if (what === 'recover') onOpened({ ...reply, tabs: (await recoverDocument(id)).tabs, autosave: null })
      else {
        await discardAutosave(id)
        onOpened({ ...reply, autosave: null })
      }
    } catch (e) {
      const text = e instanceof Error ? e.message : 'Something went wrong.'
      setModal({ kind: 'message', title: what === 'recover' ? `Could not recover "${reply.document.name}"` : `Could not discard the changes to "${reply.document.name}"`, text })
      load()
    } finally {
      setBusy(null)
    }
  }

  const create = async (name: string): Promise<string | void> => {
    let made: { id: string; name: string }
    try {
      made = await createDocument(name)
    } catch (e) {
      return e instanceof Error ? e.message : 'The Document could not be created.'
    }
    setModal(null)
    await open(made)
  }

  const closeOtherAndOpen = async (blocker: { id: string; name: string }, wanted: { id: string; name: string }) => {
    setModal(null)
    try {
      await closeDocument(blocker.id)
    } catch (e) {
      setModal({ kind: 'message', title: `Could not close "${blocker.name}"`, text: e instanceof Error ? e.message : 'Something went wrong.' })
      return
    }
    await open(wanted)
  }

  return (
    <div className="shell home" style={tokenVars()} data-testid="home">
      <header className="top-bar" data-region="top-bar">
        <div className="top-bar-name home-title">Documents</div>
        <div className="top-bar-gap" />
        <button type="button" className="icon-button" aria-label="Refresh the list" onClick={load}>
          <Icon d={paths.refresh} />
        </button>
        <button type="button" className="primary-button" onClick={() => setModal({ kind: 'new' })}>
          <Icon d={paths.plus} />
          <span>New Document</span>
        </button>
        <LinkStatusButton store={store} open={debug.open} onToggle={debug.toggle} />
      </header>
      <main className="home-body" data-region="home">
        <div className="home-column">
          {notice && (
            <p className="home-notice" role="status" data-testid="home-notice">
              {notice}
            </p>
          )}
          {loadError && (
            <div className="home-empty" role="alert">
              <p>The Documents could not be listed: {loadError}</p>
              <button type="button" className="modal-button" onClick={load}>
                Try again
              </button>
            </div>
          )}
          {sorted && sorted.length === 0 && (
            <div className="home-empty" data-testid="home-empty">
              <p>There are no Documents yet.</p>
              <p className="home-hint">Tap New Document to make one.</p>
            </div>
          )}
          {sorted && sorted.length > 0 && (
            <ul className="doc-list" aria-label="Documents">
              {sorted.map((row) => {
                const status = rowStatus(row)
                const recover = recoverMark(row)
                return (
                  <li key={row.id}>
                    <button type="button" className="doc-row" disabled={row.missing || busy !== null} aria-busy={busy === row.id} onClick={() => void open(row)}>
                      <span className="doc-tile">
                        <Icon d={paths.document} />
                      </span>
                      <span className="doc-text">
                        <span className="doc-name">{row.name}</span>
                        <span className="doc-meta">
                          {busy === row.id ? 'Opening' : formatModified(row.modified, now())}
                          {status?.readOnly && busy !== row.id ? ', opens read-only here' : ''}
                        </span>
                      </span>
                      {(status || recover) && (
                        <span className="doc-badges">
                          {recover && (
                            <span className="doc-badge" data-tone="recover" data-testid="recover-mark">
                              {recover}
                            </span>
                          )}
                          {status && (
                            <span className="doc-badge" data-tone={status.tone}>
                              {status.label}
                            </span>
                          )}
                        </span>
                      )}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </main>
      {debug.open && <DebugPopover store={store} version={version} />}
      {modal?.kind === 'new' && (
        <TextPrompt title="New Document" label="Name" confirmLabel="Create" onSubmit={create} onCancel={() => setModal(null)} />
      )}
      {modal?.kind === 'autosave' && modal.reply.autosave && (
        <AutosaveDialog
          name={modal.reply.document.name}
          offer={modal.reply.autosave}
          now={now()}
          onRecover={() => void answerAutosave(modal.reply, 'recover')}
          onDiscard={() => void answerAutosave(modal.reply, 'discard')}
          onLater={() => {
            setModal(null)
            onOpened(modal.reply)
          }}
        />
      )}
      {modal?.kind === 'other' && (
        <ConfirmDialog
          title="Another Document is open"
          message={`"${modal.blocker.name}" is still open on the server, and only one Document can be open at a time. Close it to open "${modal.wanted.name}". Changes in "${modal.blocker.name}" that are not saved are kept as an autosave you can recover when you open it again.`}
          confirm={{ label: `Close "${modal.blocker.name}"`, danger: true }}
          onConfirm={() => void closeOtherAndOpen(modal.blocker, modal.wanted)}
          onCancel={() => setModal(null)}
        />
      )}
      {modal?.kind === 'message' && (
        <ConfirmDialog title={modal.title} message={modal.text} confirm={{ label: 'OK' }} cancelLabel={null} onConfirm={() => setModal(null)} onCancel={() => setModal(null)} />
      )}
    </div>
  )
}
