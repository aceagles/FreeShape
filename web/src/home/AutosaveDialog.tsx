import type { AutosaveOffer } from '../net/api'
import { Modal, ModalButtons } from '../shell/Modal'
import { formatAgo } from './format'

const list = (names: string[]): string => (names.length <= 2 ? names.join(' and ') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`)

/**
 * Opening a Document that has an autosave the server kept (it was closed, or its server stopped, with edits not saved):
 * Recover, Discard, or decide later. The words say when it was written and what each choice does; when the saved
 * Document has changed since (`moved_on`), they say that Recover replaces that saved work in the open Document, and that
 * nothing reaches the disk until Save.
 */
export function AutosaveDialog({
  name,
  offer,
  now,
  onRecover,
  onDiscard,
  onLater,
}: {
  name: string
  offer: AutosaveOffer
  now: number
  onRecover: () => void
  onDiscard: () => void
  onLater: () => void
}) {
  const names = new Map(offer.tabs.map((t) => [t.id, t.name]))
  const moved = offer.moved_on.map((id) => `"${names.get(id) ?? id}"`)
  const when = formatAgo(offer.time, now)
  return (
    <Modal title={`Recover unsaved changes to "${name}"?`} onClose={onLater}>
      <p className="modal-message tight" data-testid="autosave-when">
        {offer.count > 1 ? `The server kept ${offer.count} sets of changes that were not saved, the last ` : 'The server kept changes that were not saved, '}
        written {when}.
      </p>
      <p className="modal-message tight">
        <b>Recover</b> puts them back into the open Document. Nothing is saved until you press Save.
      </p>
      <p className="modal-message tight">
        <b>Discard</b> deletes them. The Document stays as it was last saved.
      </p>
      <p className="modal-message tight">
        <b>Decide later</b> opens the Document as it was last saved and keeps the changes.
      </p>
      {(moved.length > 0 || offer.list_moved_on) && (
        <p className="modal-message modal-warning" role="note" data-testid="autosave-moved">
          {moved.length > 0 ? `${list(moved)} ${moved.length === 1 ? 'was' : 'were'}` : 'The list of tabs was'} saved by another program after
          these changes were written. Recover replaces that saved work in the open Document with the recovered version. The saved work stays
          on disk until you press Save. To keep it, choose Discard, or Recover and then leave without saving.
        </p>
      )}
      <ModalButtons
        buttons={[
          { label: 'Decide later', onClick: onLater },
          { label: 'Discard', onClick: onDiscard, kind: 'danger' },
          { label: 'Recover', onClick: onRecover, kind: 'primary' },
        ]}
      />
    </Modal>
  )
}
