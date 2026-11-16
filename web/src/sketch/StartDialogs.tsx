import { Modal, ModalButtons } from '../shell/Modal'
import { DEFAULT_PLANE_NAMES } from './start'

/** What the Sketch tool asks with nothing selected: the plane for the new sketch, the three default planes as buttons. */
export function PlaneChooser({ onPlane, onCancel }: { onPlane: (name: (typeof DEFAULT_PLANE_NAMES)[number]) => void; onCancel: () => void }) {
  return (
    <Modal title="Sketch on which plane?" onClose={onCancel}>
      <p className="modal-message">Or cancel, pick a plane or a flat face in the view, and tap Sketch again.</p>
      <ModalButtons buttons={[{ label: 'Cancel', onClick: onCancel }, ...DEFAULT_PLANE_NAMES.map((n) => ({ label: n, onClick: () => onPlane(n), kind: 'primary' as const }))]} />
    </Modal>
  )
}

/** Cancel on a sketch with changes: asks before the changes are thrown away. */
export function DiscardSketch({ onDiscard, onKeep }: { onDiscard: () => void; onKeep: () => void }) {
  return (
    <Modal title="Discard this sketch?" onClose={onKeep}>
      <p className="modal-message">The changes made in this sketch are thrown away and cannot be recovered.</p>
      <ModalButtons buttons={[{ label: 'Keep sketching', onClick: onKeep }, { label: 'Discard', onClick: onDiscard, kind: 'danger' }]} />
    </Modal>
  )
}
