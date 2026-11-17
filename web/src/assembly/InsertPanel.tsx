import { useEffect, useRef, useState } from 'react'
import { STEP_ACCEPT, fetchUploadLimit, importFailure, insertDevice, insertedText } from '../files/filesApi'
import type { StepInsertReply } from '../files/filesApi'
import type { ConnectionStore } from '../net/connection'
import type { TabInfo } from '../net/connection'
import { DialogArea } from '../shell/DialogArea'
import { useMode } from '../shell/mode'
import { PartPicker, useStudioParts } from './PartPicker'
import type { PickerChoice, PickerStudio } from './PartPicker'
import type { AssemblyEdits } from './useAssembly'
import './insert.css'

/** The sources the desktop's Insert panel has that the client does not, each with its own button: they are shown off. From file… and Component library… work since I11.11. */
export const DESKTOP_ONLY_SOURCES = ['Other Document…', 'Standard parts'] as const
export const DESKTOP_ONLY_NOTE = 'Desktop only for now'

interface Props {
  store: ConnectionStore
  documentId: string
  /** The Document's tabs; the Part Studios among them are listed. */
  tabs: readonly TabInfo[]
  /** The Assembly tab this panel is for: a STEP from the device goes into it. */
  tab: string
  /** Component library…: the shell opens the explorer in its Assembly mode and comes back here when it closes. */
  onLibrary: () => void
  /** A STEP from the device went in (the shell takes the new tab list and keeps what to say, for a panel that is closed and opened again). */
  onInserted: (reply: StepInsertReply, said: string) => void
  /** What the last insert from the explorer said, shown until the next action. */
  note?: string | null
  edits: AssemblyEdits
  onClose: () => void
}

/** What the panel says after an insert: the part's name, or the Part Studio's for all its parts. */
export function insertedName(studio: PickerStudio | undefined, choice: PickerChoice): string {
  if (studio === undefined) return choice.part ?? 'the parts'
  if (choice.part === null) return studio.name
  return studio.parts?.find((p) => p.id === choice.part)?.name ?? choice.part
}

/**
 * The Insert panel (card I10.1), as the desktop's InsertPanel: this Document's Part Studios with their parts; a part inserts that
 * part, a Part Studio inserts all its parts where they sit relative to each other. It stays open, so several can go in, and says
 * what went in. A double-click inserts (Desktop mode); a tap selects and Insert inserts (both modes). The desktop's other sources
 * are shown off. In the dialog area: floating over the view in Desktop mode, over the left column in Tablet mode.
 */
export function InsertPanel({ store, documentId, tabs, tab, onLibrary, onInserted, note = null, edits, onClose }: Props) {
  const desktop = useMode() === 'desktop'
  const studios = useStudioParts(store, documentId, tabs)
  const [choice, setChoice] = useState<PickerChoice | null>(null)
  const [said, setSaid] = useState<string | null>(note)
  // From file… (I11.11): the picker's input is made when the button is pressed and removed when the file is chosen, the picker is
  // cancelled or the panel closes, so no input stays on the page (Tablet mode's number pad needs none to be there)
  const picker = useRef<HTMLInputElement | null>(null)
  const sending = useRef<AbortController | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyFile, setBusyFile] = useState<string | null>(null)
  const dropPicker = () => {
    picker.current?.remove()
    picker.current = null
  }
  useEffect(
    () => () => {
      picker.current?.remove()
      sending.current?.abort()
    },
    [],
  )
  const sendFile = async (file: File): Promise<void> => {
    const ctl = new AbortController()
    sending.current = ctl
    setBusyFile(file.name)
    setSaid(null)
    setError(null)
    try {
      const r = await insertDevice(documentId, tab, file, { limit: await fetchUploadLimit(), signal: ctl.signal })
      const text = insertedText(file.name, r.instances)
      setSaid(text)
      onInserted(r, text)
    } catch (x) {
      if (!(x instanceof DOMException && x.name === 'AbortError')) setError(importFailure(x, 'The STEP could not be inserted.'))
    } finally {
      if (sending.current === ctl) sending.current = null
      setBusyFile(null)
    }
  }
  const pickFile = (): void => {
    dropPicker()
    const el = document.createElement('input')
    el.type = 'file'
    el.accept = STEP_ACCEPT
    el.hidden = true
    el.setAttribute('aria-hidden', 'true')
    el.addEventListener('change', () => {
      const f = el.files?.[0]
      dropPicker()
      if (f !== undefined) void sendFile(f)
    })
    el.addEventListener('cancel', dropPicker)
    document.body.appendChild(el)
    picker.current = el
    el.click()
  }
  const chosen = choice === null ? undefined : studios.find((s) => s.tab === choice.tab)
  const insertable = choice !== null && chosen !== undefined && !edits.disabled && !edits.busy && (choice.part !== null || (chosen.parts?.length ?? 0) > 0)
  const insert = async (c: PickerChoice | null): Promise<void> => {
    const s = c === null ? undefined : studios.find((x) => x.tab === c.tab)
    if (c === null || s === undefined || edits.disabled || edits.busy) return
    setSaid(null)
    setError(null)
    if (await edits.insert(c.tab, c.part)) setSaid(`Inserted ${insertedName(s, c)}`)
  }
  return (
    <DialogArea title="Insert parts" onClose={onClose}>
      <div className="insert-body" data-testid="insert-panel">
        <p className="insert-hint">
          {desktop ? 'Double-click a part to insert it.' : 'Tap a part, then Insert.'} Pick a Part Studio to insert all its parts as they sit there. Inserted parts stay linked: edit them in their tab and the Assembly updates.
        </p>
        <div className="insert-list">
          <PartPicker studios={studios} choice={choice} onChoose={(c) => (setChoice(c), setSaid(null))} onActivate={(c) => void insert(c)} />
        </div>
        <div className="insert-actions">
          <button type="button" className="insert-button insert-primary" data-insert-go="" disabled={!insertable} onClick={() => void insert(choice)}>
            Insert
          </button>
          <button type="button" className="insert-button" data-insert-source="From file…" disabled={edits.disabled || edits.busy || busyFile !== null} onClick={pickFile}>
            From file…
          </button>
          <button type="button" className="insert-button" data-insert-source="Component library…" disabled={edits.disabled || edits.busy || busyFile !== null} onClick={onLibrary}>
            Component library…
          </button>
          {DESKTOP_ONLY_SOURCES.map((label) => (
            <button key={label} type="button" className="insert-button" data-insert-source={label} disabled title={DESKTOP_ONLY_NOTE}>
              {label}
            </button>
          ))}
        </div>
        <p className="insert-note">{DESKTOP_ONLY_NOTE}: the other Document and the Standard parts. From file… takes a STEP; Component library… opens the Files panel.</p>
        <p className="dlg-status insert-said" role="status" data-testid="insert-said">
          {busyFile !== null ? `Inserting ${busyFile}…` : (said ?? '')}
        </p>
        {error !== null && (
          <p className="dlg-status insert-error" role="alert" data-testid="insert-error">
            {error}
          </p>
        )}
      </div>
    </DialogArea>
  )
}
