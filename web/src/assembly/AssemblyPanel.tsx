import { useState } from 'react'
import { Icon, paths } from '../shell/icons'
import { ConfirmDialog } from '../shell/Modal'
import { size } from '../theme/tokens'
import { cssColor } from '../features/partPalette'
import type { AssemblyOutputs, InstanceEntry, MateEntry } from '../wire'
import type { AssemblyEdits } from './useAssembly'
import '../features/features.css'
import './assembly.css'

interface Props {
  collapsed: boolean
  /** Dialog open: the dialog covers the panel, so collapsing it is off. */
  locked: boolean
  /** The Assembly's outputs, or null while they load. */
  outputs: AssemblyOutputs | null
  /** The instance selected (alone) in the view or the list, or null. */
  selected: string | null
  edits: AssemblyEdits
  onSelect: (instance: InstanceEntry) => void
  onCollapsedChange: (collapsed: boolean) => void
}

/** Mate types as the desktop spells them in its list ("revolute" -> "Revolute"). */
const typeLabel = (t: string): string => (t.length === 0 ? t : t[0]!.toUpperCase() + t.slice(1).replace(/_/g, ' '))

function InstanceRow({ inst, error, selected, edits, onSelect }: { inst: InstanceEntry; error: string | undefined; selected: boolean; edits: AssemblyEdits; onSelect: () => void }) {
  const state = error !== undefined ? 'failed' : undefined
  const off = edits.disabled || edits.busy
  return (
    <li className="part-row instance-row" data-instance-row={inst.id} data-hidden={inst.hidden ? '' : undefined} data-fixed={inst.fixed ? '' : undefined} data-selected={selected ? '' : undefined}>
      <button type="button" className="panel-row part-select" data-instance={inst.id} data-state={state} aria-current={selected ? 'true' : undefined} onClick={onSelect}>
        <Icon d={paths.cube} px={size.iconSmall} stroke="var(--c-inkSoft)" />
        <span className="row-text">
          <span className="row-name">{inst.name}</span>
          {error !== undefined && <span className="row-message">{error}</span>}
        </span>
        <span className="part-swatch" style={{ background: cssColor(inst.color) }} />
      </button>
      <button
        type="button"
        className="icon-button part-eye instance-fixed"
        aria-label={inst.fixed ? `Unfix ${inst.name}` : `Fix ${inst.name}`}
        aria-pressed={inst.fixed}
        data-instance-fixed={inst.id}
        disabled={off}
        onClick={() => edits.setFixed(inst.id, !inst.fixed)}
      >
        <Icon d={paths.lock} px={size.iconSmall} />
      </button>
      <button
        type="button"
        className="icon-button part-eye"
        aria-label={inst.hidden ? `Show ${inst.name}` : `Hide ${inst.name}`}
        data-instance-eye={inst.id}
        disabled={off}
        onClick={() => edits.setHidden(inst.id, !inst.hidden)}
      >
        <Icon d={inst.hidden ? paths.eyeOff : paths.eye} px={size.iconSmall} />
      </button>
    </li>
  )
}

function MateRow({ mate, names, error }: { mate: MateEntry; names: ReadonlyMap<string, string>; error: string | undefined }) {
  return (
    <li className="mate-row" data-mate-row={mate.id} data-state={error !== undefined ? 'failed' : mate.suppressed ? 'suppressed' : undefined}>
      <span className="row-text">
        <span className="row-name">{mate.name}</span>
        <span className="row-message">
          {typeLabel(mate.type)}: {names.get(mate.a) ?? mate.a} and {names.get(mate.b) ?? mate.b}
          {mate.suppressed ? ', suppressed' : ''}
        </span>
        {error !== undefined && <span className="row-message mate-error">{error}</span>}
      </span>
    </li>
  )
}

/**
 * The Assembly panel (card I8.1), in the place of the feature panel on an Assembly tab: "Instances (n)", one 44 px row per
 * instance (a tap selects it, with the same cube, name and colour swatch as the Parts list), a fixed toggle and the eye at the right end; under the
 * selected instance a Delete button (after a confirmation); then "Mates (n)", read-only, each with its type and any error.
 */
export function AssemblyPanel({ collapsed, locked, outputs, selected, edits, onSelect, onCollapsedChange }: Props) {
  const [deleting, setDeleting] = useState<InstanceEntry | null>(null)
  if (collapsed) {
    return (
      <div className="panel-rail" data-testid="panel-rail">
        <button type="button" className="icon-button" aria-label="Expand feature panel" onClick={() => onCollapsedChange(false)}>
          <Icon d={paths.expand} />
        </button>
      </div>
    )
  }
  const names = new Map(outputs?.instances.map((i) => [i.id, i.name]) ?? [])
  const errors = outputs?.errors ?? {}
  return (
    <section className="panel" data-testid="assembly-panel" aria-label="Assembly panel" data-selected-instance={selected ?? undefined}>
      <div className="panel-header">
        <div className="panel-title">Assembly</div>
        <button type="button" className="icon-button" aria-label="Collapse feature panel" disabled={locked} onClick={() => onCollapsedChange(true)}>
          <Icon d={paths.collapse} />
        </button>
      </div>
      <div className="panel-body" data-testid="panel-body">
        {outputs === null ? (
          <p className="panel-empty">Loading the Assembly</p>
        ) : (
          <>
            {errors[''] !== undefined && <p className="panel-empty assembly-error">{errors['']}</p>}
            <div className="parts-heading" id="instances-heading">
              Instances ({outputs.instances.length})
            </div>
            <ul className="parts-list" aria-labelledby="instances-heading" data-testid="instance-list">
              {outputs.instances.map((inst) => (
                <InstanceRow key={inst.id} inst={inst} error={errors[inst.id]} selected={inst.id === selected} edits={edits} onSelect={() => onSelect(inst)} />
              ))}
            </ul>
            {selected !== null && !edits.disabled && (
              <div className="instance-actions">
                <button
                  type="button"
                  className="instance-delete"
                  disabled={edits.busy}
                  aria-label={`Delete ${names.get(selected) ?? selected}`}
                  onClick={() => setDeleting(outputs.instances.find((i) => i.id === selected) ?? null)}
                >
                  <Icon d={paths.trash} px={size.iconSmall} />
                  Delete
                </button>
              </div>
            )}
            <div className="parts-heading" id="mates-heading">
              Mates ({outputs.mates.length})
            </div>
            <ul className="parts-list" aria-labelledby="mates-heading" data-testid="mate-list">
              {outputs.mates.map((m) => (
                <MateRow key={m.id} mate={m} names={names} error={errors[m.id]} />
              ))}
            </ul>
          </>
        )}
      </div>
      {deleting && (
        <ConfirmDialog
          title={`Delete "${deleting.name}"?`}
          message="The instance and the mates on it are removed from the Assembly. Undo brings them back."
          confirm={{ label: 'Delete', danger: true }}
          onConfirm={() => {
            const id = deleting.id
            setDeleting(null)
            void edits.deleteInstance(id)
          }}
          onCancel={() => setDeleting(null)}
        />
      )}
    </section>
  )
}
