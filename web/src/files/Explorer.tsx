import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { formatModified, rowStatus } from '../home/format'
import { listDocuments } from '../net/api'
import type { DocumentRow } from '../net/api'
import { DialogArea } from '../shell/DialogArea'
import { Icon, paths } from '../shell/icons'
import type { Mode } from '../shell/mode'
import { TextPrompt } from '../shell/TextPrompt'
import { fetchFiles, fetchFolder, importEntry } from './filesApi'
import type { FileEntry, FileRoot, FolderRef, ImportReply } from './filesApi'
import './files.css'

/** The root that lists the Documents (a file root's name is lower case, so none can be called this). */
export const DOCUMENTS_ROOT = 'Documents'

/** How the explorer was opened: `add` and `new` make that import the main button of a STEP entry; `browse` is for looking. */
export type ExplorerMode = 'browse' | 'add' | 'new'

/** Where the explorer sits: over the left column (Tablet mode in a Document), floating over the view (Desktop mode), or over the whole home screen. */
export type ExplorerPlacement = 'column' | 'float' | 'page'

/** The Document the explorer can import into; absent on the home screen, where there is nothing to import into. */
export interface ImportTarget {
  document: string
  /** The tab on screen: the import route's path names it, `new_tab` or not. Null when the Document has no tab. */
  tab: string | null
  /** The tab on screen is a Part Studio, so Add to this Part Studio applies. */
  studio: boolean
  readOnly: boolean
}

export interface ExplorerProps {
  mode: ExplorerMode
  placement: ExplorerPlacement
  /** Desktop mode types a search into the box; Tablet mode asks for it in a prompt, as a tab's name is asked. */
  uiMode: Mode
  target: ImportTarget | null
  /** A Document row was chosen: the home screen's open flow, or leaving this Document for it. */
  onOpenDocument: (row: DocumentRow) => void | Promise<unknown>
  /** An import went through; `newTab` says it made a new Part Studio, which the shell switches to. */
  onImported: (reply: ImportReply, newTab: boolean) => void
  onClose: () => void
  /** The Document this explorer sits in, so its row says so instead of opening it again. */
  currentDocument?: string
  now?: () => number
}

type Status = { kind: 'busy' | 'ok' | 'error'; text: string }

/** A folder or search as one request sees it, with the entries loaded so far. */
interface Listing {
  key: string
  dirs: FolderRef[]
  entries: FileEntry[]
  total: number
  error: string | null
}

export function formatSize(bytes: number): string {
  if (bytes < 1000) return `${bytes} B`
  if (bytes < 1e6) return `${Math.round(bytes / 1e3)} kB`
  return `${(bytes / 1e6).toFixed(bytes < 1e7 ? 1 : 0)} MB`
}

const folderOf = (path: string): string => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')
const message = (e: unknown, fallback: string): string => (e instanceof Error ? e.message : fallback)
const aborted = (e: unknown): boolean => e instanceof DOMException && e.name === 'AbortError'
/** Where the dialogs over the whole page go: the shell's root, so they carry its styles and are not clipped by the panel. */
const overlayHost = (): HTMLElement => document.querySelector<HTMLElement>('.shell') ?? document.body
const safeUrl = (u: string): boolean => /^https?:\/\//i.test(u)

/**
 * The file explorer (card I11.7): a column of roots (Documents, then each file root the server lists), a breadcrumb, a search box
 * and the folders and entries of the chosen root, 200 at a time. Everything it shows is what GET /files and GET /files/<root> say:
 * paging, search and the licence fields are the server's. A Documents row opens that Document; a STEP entry can be added to the
 * Part Studio on screen or opened as a new Part Studio, and the explorer says `Imported <name>` or the server's reason.
 * It is the desktop's Component library (freeshape/ui/step_library_ui.py) reached over serve; thumbnails are left out.
 */
export function Explorer({ mode, placement, uiMode, target, onOpenDocument, onImported, onClose, currentDocument, now = Date.now }: ExplorerProps) {
  const desktop = uiMode === 'desktop'
  const [files, setFiles] = useState<{ roots: FileRoot[] } | { error: string } | null>(null)
  const [root, setRoot] = useState<string | null>(null)
  const [dir, setDir] = useState('')
  const [q, setQ] = useState('')
  const [asking, setAsking] = useState(false)
  const [listing, setListing] = useState<Listing | null>(null)
  const [paging, setPaging] = useState(false)
  const [chosen, setChosen] = useState<string | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [docs, setDocs] = useState<{ rows: DocumentRow[] } | { error: string } | null>(null)
  const [opening, setOpening] = useState<string | null>(null)
  const top = useRef<HTMLDivElement>(null)
  // the key of the request whose next page is being fetched, so a scroll does not start the same page twice
  const pageKey = useRef<string | null>(null)

  const loadFiles = useCallback(() => {
    fetchFiles().then(
      (r) => {
        setFiles({ roots: r.roots })
        // a file root first when there is one: that is what the explorer is for; else the Documents
        setRoot((was) => was ?? (r.roots[0]?.name ?? DOCUMENTS_ROOT))
      },
      (e: unknown) => {
        setFiles({ error: message(e, 'The file roots could not be listed.') })
        setRoot((was) => was ?? DOCUMENTS_ROOT)
      },
    )
  }, [])
  useEffect(() => {
    loadFiles()
  }, [loadFiles])
  useEffect(() => top.current?.focus(), [])

  const isFiles = root !== null && root !== DOCUMENTS_ROOT
  const key = `${root}|${dir}|${q}`

  // the first page of the folder or search; a new folder or word abandons the one before
  useEffect(() => {
    if (!isFiles || root === null) return
    const ctl = new AbortController()
    fetchFolder(root, { dir, q, signal: ctl.signal }).then(
      (r) => {
        pageKey.current = null
        setPaging(false)
        setListing({ key, dirs: r.dirs, entries: r.entries, total: r.total, error: null })
      },
      (e: unknown) => {
        if (!aborted(e)) setListing({ key, dirs: [], entries: [], total: 0, error: message(e, 'The folder could not be read.') })
      },
    )
    return () => ctl.abort()
  }, [isFiles, root, dir, q, key])

  useEffect(() => {
    if (root !== DOCUMENTS_ROOT) return
    let live = true
    listDocuments().then(
      (rows) => live && setDocs({ rows }),
      (e: unknown) => live && setDocs({ error: message(e, 'The Documents could not be listed.') }),
    )
    return () => {
      live = false
    }
  }, [root])

  const shown = listing !== null && listing.key === key ? listing : null
  const entry = shown?.entries.find((e) => e.id === chosen) ?? null

  const choose = (r: string) => {
    if (r === root) return
    setRoot(r)
    setDir('')
    setQ('')
    setChosen(null)
    setStatus(null)
  }
  const goTo = (path: string) => {
    setDir(path)
    setQ('')
    setChosen(null)
    setStatus(null)
  }
  const search = (text: string) => {
    setQ(text)
    setChosen(null)
    setStatus(null)
  }

  // scrolling to the end asks for the next page, from where the loaded entries stop
  const onScroll = (el: HTMLElement) => {
    if (!isFiles || root === null || shown === null || shown.error !== null || shown.entries.length >= shown.total) return
    if (el.scrollTop + el.clientHeight < el.scrollHeight - 48 || pageKey.current === key) return
    pageKey.current = key
    setPaging(true)
    fetchFolder(root, { dir, q, offset: shown.entries.length }).then(
      (r) => {
        setListing((was) => (was !== null && was.key === key ? { ...was, entries: [...was.entries, ...r.entries], total: r.total } : was))
        setPaging(false)
        if (pageKey.current === key) pageKey.current = null
      },
      (e: unknown) => {
        setPaging(false)
        if (pageKey.current === key) pageKey.current = null
        setStatus({ kind: 'error', text: message(e, 'The next page could not be read.') })
      },
    )
  }

  const run = async (e: FileEntry, newTab: boolean) => {
    if (target === null || target.tab === null || root === null) return
    setStatus({ kind: 'busy', text: `Importing ${e.name}…` })
    try {
      const r = await importEntry(target.document, target.tab, root, e.id, newTab)
      const why = r.feature?.error
      setStatus(why ? { kind: 'error', text: `${e.name}: ${why}` } : { kind: 'ok', text: `Imported ${e.name}` })
      onImported(r, newTab)
    } catch (x) {
      setStatus({ kind: 'error', text: message(x, 'The import failed.') })
    }
  }

  const openDocument = (row: DocumentRow) => {
    if (row.id === currentDocument) return onClose()
    setOpening(row.id)
    void Promise.resolve(onOpenDocument(row)).finally(() => setOpening(null))
  }

  const canAdd = target !== null && target.tab !== null && target.studio && !target.readOnly
  const canNew = target !== null && target.tab !== null && !target.readOnly
  const busy = status?.kind === 'busy'
  const why =
    target === null
      ? 'Open a Document to add parts to it.'
      : target.readOnly
        ? 'This Document is open read-only here, so nothing can be added.'
        : target.tab === null
          ? 'This Document has no tab to add to.'
          : !target.studio
            ? 'Select a Part Studio tab to add to it; a new Part Studio works from any tab.'
            : null

  const segments = dir === '' ? [] : dir.split('/')
  const crumb = (
    <nav className="ex-crumbs" aria-label="Folder">
      <button type="button" className="ex-crumb" aria-current={dir === '' ? 'location' : undefined} onClick={() => goTo('')}>
        {root}
      </button>
      {segments.map((s, i) => (
        <span key={i} className="ex-crumb-part">
          <Icon d={paths.chevronRight} px={14} stroke="var(--c-muted)" />
          <button type="button" className="ex-crumb" aria-current={i === segments.length - 1 ? 'location' : undefined} onClick={() => goTo(segments.slice(0, i + 1).join('/'))}>
            {s}
          </button>
        </span>
      ))}
    </nav>
  )

  const searchBox = desktop ? (
    <div className="ex-search">
      <Icon d={paths.search} px={18} stroke="var(--c-muted)" />
      <input
        type="text"
        className="ex-search-input"
        aria-label="Search"
        placeholder={dir === '' ? `Search ${root}` : `Search in ${segments[segments.length - 1]}`}
        value={q}
        spellCheck={false}
        autoComplete="off"
        onChange={(e) => search(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation()
            if (q !== '') search('')
            else top.current?.focus()
          }
        }}
      />
      {q !== '' && (
        <button type="button" className="ex-clear" aria-label="Clear the search" onClick={() => search('')}>
          <Icon d={paths.close} px={16} />
        </button>
      )}
    </div>
  ) : (
    <div className="ex-search">
      <button type="button" className="ex-search-button" aria-label={q === '' ? 'Search' : `Search, ${q}`} onClick={() => setAsking(true)}>
        <Icon d={paths.search} px={20} stroke="var(--c-muted)" />
        <span className="ex-search-text" data-empty={q === '' ? '' : undefined}>
          {q === '' ? (dir === '' ? `Search ${root}` : `Search in ${segments[segments.length - 1]}`) : q}
        </span>
      </button>
      {q !== '' && (
        <button type="button" className="ex-clear" aria-label="Clear the search" onClick={() => search('')}>
          <Icon d={paths.close} px={18} />
        </button>
      )}
    </div>
  )

  const fileList = (() => {
    if (shown === null) return <p className="ex-note" role="status">Loading…</p>
    if (shown.error !== null)
      return (
        <p className="ex-note" role="alert">
          {shown.error}
        </p>
      )
    if (shown.dirs.length === 0 && shown.entries.length === 0)
      return (
        <p className="ex-note" data-testid="ex-empty">
          {q === '' ? 'This folder has no STEP files.' : `Nothing matches "${q}".`}
        </p>
      )
    return (
      <ul className="ex-rows" aria-label="Files">
        {shown.dirs.map((d) => (
          <li key={`d:${d.path}`}>
            <button type="button" className="ex-row ex-folder" onClick={() => goTo(d.path)}>
              <Icon d={paths.folder} px={22} stroke="var(--c-muted)" />
              <span className="ex-row-text">
                <span className="ex-name">{d.name}</span>
                <span className="ex-meta">
                  <span>{d.files === 1 ? '1 file' : `${d.files} files`}</span>
                </span>
              </span>
              <Icon d={paths.chevronRight} px={18} stroke="var(--c-muted)" />
            </button>
          </li>
        ))}
        {shown.entries.map((e) => {
          const folder = folderOf(e.path)
          return (
            <li key={e.id}>
              <button type="button" className="ex-row" aria-current={e.id === chosen ? 'true' : undefined} onClick={() => {
                  setChosen(e.id)
                  setStatus(null)
                }}>
                <Icon d={paths.cube} px={22} stroke="var(--c-muted)" />
                <span className="ex-row-text">
                  <span className="ex-name">{e.name}</span>
                  <span className="ex-meta">
                    {folder !== '' && <span className="ex-folder-name">{folder}</span>}
                    <span>{formatSize(e.size)}</span>
                    {e.licence !== null && <span className="ex-licence">{e.licence}</span>}
                  </span>
                </span>
              </button>
            </li>
          )
        })}
        {paging && (
          <li className="ex-note" role="status">
            Loading more…
          </li>
        )}
      </ul>
    )
  })()

  const detail = entry !== null && (
    <div className="ex-detail" data-testid="ex-detail">
      <div className="ex-detail-name">{entry.name}</div>
      <div className="ex-detail-line">
        {entry.file} · {formatSize(entry.size)}
        {entry.licence !== null && ` · ${entry.licence}`}
      </div>
      {entry.authors !== null && <div className="ex-detail-line">By {entry.authors}</div>}
      {entry.description !== '' && <div className="ex-detail-line">{entry.description}</div>}
      {entry.url !== null && (
        <div className="ex-detail-line">
          {safeUrl(entry.url) ? (
            <a href={entry.url} target="_blank" rel="noreferrer noopener">
              {entry.url}
            </a>
          ) : (
            entry.url
          )}
        </div>
      )}
      <div className="ex-actions">
        <button type="button" className="ex-action" data-kind={mode === 'add' ? 'primary' : 'plain'} disabled={!canAdd || busy} onClick={() => void run(entry, false)}>
          Add to this Part Studio
        </button>
        <button type="button" className="ex-action" data-kind={mode === 'new' ? 'primary' : 'plain'} disabled={!canNew || busy} onClick={() => void run(entry, true)}>
          Open in a new Part Studio
        </button>
      </div>
      {why !== null && <div className="ex-detail-line ex-why">{why}</div>}
    </div>
  )

  const documents = (() => {
    if (docs === null) return <p className="ex-note" role="status">Loading…</p>
    if ('error' in docs)
      return (
        <p className="ex-note" role="alert">
          {docs.error}
        </p>
      )
    if (docs.rows.length === 0) return <p className="ex-note">There are no Documents yet.</p>
    const rows = [...docs.rows].sort((a, b) => (b.modified ?? -1) - (a.modified ?? -1) || a.name.localeCompare(b.name))
    return (
      <ul className="ex-rows" aria-label="Documents">
        {rows.map((r) => {
          const st = rowStatus(r)
          return (
            <li key={r.id}>
              <button type="button" className="ex-row" disabled={r.missing || opening !== null} aria-busy={opening === r.id} onClick={() => openDocument(r)}>
                <Icon d={paths.document} px={22} stroke="var(--c-muted)" />
                <span className="ex-row-text">
                  <span className="ex-name">{r.name}</span>
                  <span className="ex-meta">
                    <span>{opening === r.id ? 'Opening' : formatModified(r.modified, now())}</span>
                    {r.id === currentDocument ? <span>Open here</span> : st !== null && <span>{st.label}</span>}
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    )
  })()

  const roots = files !== null && 'roots' in files ? files.roots : []
  return (
    <DialogArea title="Files" onClose={onClose} width={placement === 'float' ? 620 : undefined}>
      <div
        className="explorer"
        data-testid="explorer"
        data-placement={placement}
        data-mode={uiMode}
        data-open-mode={mode}
        ref={top}
        tabIndex={-1}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && !asking) {
            e.stopPropagation()
            onClose()
          }
        }}
      >
        <nav className="ex-roots" aria-label="Roots">
          {[DOCUMENTS_ROOT, ...roots.map((r) => r.name)].map((name) => (
            <button key={name} type="button" className="ex-root" aria-current={name === root ? 'true' : undefined} onClick={() => choose(name)}>
              <Icon d={name === DOCUMENTS_ROOT ? paths.document : paths.folder} px={20} />
              <span className="ex-root-name">{name}</span>
              {name !== DOCUMENTS_ROOT && <span className="ex-root-count">{roots.find((r) => r.name === name)?.files}</span>}
            </button>
          ))}
        </nav>
        <div className="ex-main">
          {files !== null && 'error' in files && (
            <p className="ex-note" role="alert">
              {files.error}{' '}
              <button type="button" className="ex-link" onClick={loadFiles}>
                Try again
              </button>
            </p>
          )}
          {isFiles && (
            <>
              {crumb}
              {searchBox}
              {q !== '' && shown !== null && shown.error === null && (
                <div className="ex-count" role="status">
                  {shown.total === 1 ? '1 match' : `${shown.total} matches`}
                </div>
              )}
              <div className="ex-list" data-testid="ex-list" onScroll={(e) => onScroll(e.currentTarget)}>
                {fileList}
              </div>
              {detail}
              {status !== null && (
                <div className="ex-status" role={status.kind === 'error' ? 'alert' : 'status'} data-kind={status.kind} data-testid="ex-status">
                  {status.text}
                </div>
              )}
            </>
          )}
          {root === DOCUMENTS_ROOT && <div className="ex-list" data-testid="ex-list">{documents}</div>}
        </div>
      </div>
      {asking &&
        createPortal(
          <TextPrompt
            title="Search"
            label="Search"
            initial={q}
            confirmLabel="Search"
            onSubmit={(v) => {
              search(v)
              setAsking(false)
            }}
            onCancel={() => setAsking(false)}
          />,
          overlayHost(),
        )}
    </DialogArea>
  )
}
