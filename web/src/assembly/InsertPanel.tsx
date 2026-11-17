import { useState } from 'react'
import type { ConnectionStore } from '../net/connection'
import type { TabInfo } from '../net/connection'
import { DialogArea } from '../shell/DialogArea'
import { useMode } from '../shell/mode'
import { PartPicker, useStudioParts } from './PartPicker'
import type { PickerChoice, PickerStudio } from './PartPicker'
import type { AssemblyEdits } from './useAssembly'
import './insert.css'

/** The sources the desktop's Insert panel has that the client does not, each with its own button: they are shown off. */
export const DESKTOP_ONLY_SOURCES = ['From file…', 'Other Document…', 'Component library…', 'Standard parts'] as const
export const DESKTOP_ONLY_NOTE = 'Desktop only for now'

interface Props {
  store: ConnectionStore
  documentId: string
  /** The Document's tabs; the Part Studios among them are listed. */
  tabs: readonly TabInfo[]
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
export function InsertPanel({ store, documentId, tabs, edits, onClose }: Props) {
  const desktop = useMode() === 'desktop'
  const studios = useStudioParts(store, documentId, tabs)
  const [choice, setChoice] = useState<PickerChoice | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  const chosen = choice === null ? undefined : studios.find((s) => s.tab === choice.tab)
  const insertable = choice !== null && chosen !== undefined && !edits.disabled && !edits.busy && (choice.part !== null || (chosen.parts?.length ?? 0) > 0)
  const insert = async (c: PickerChoice | null): Promise<void> => {
    const s = c === null ? undefined : studios.find((x) => x.tab === c.tab)
    if (c === null || s === undefined || edits.disabled || edits.busy) return
    setSaid(null)
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
          {DESKTOP_ONLY_SOURCES.map((label) => (
            <button key={label} type="button" className="insert-button" data-insert-source={label} disabled title={DESKTOP_ONLY_NOTE}>
              {label}
            </button>
          ))}
        </div>
        <p className="insert-note">{DESKTOP_ONLY_NOTE}: the file, the other Document, the component library and the Standard parts.</p>
        <p className="dlg-status insert-said" role="status" data-testid="insert-said">
          {said ?? ''}
        </p>
      </div>
    </DialogArea>
  )
}
