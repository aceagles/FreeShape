import { useState } from 'react'
import { Icon, paths } from '../shell/icons'
import { ConfirmDialog } from '../shell/Modal'
import { useMode } from '../shell/mode'
import { anchorOf, Popover, PopoverItem } from '../shell/Popover'
import type { Anchor } from '../shell/Popover'
import { useMenuPress } from '../shell/rightClick'
import { size } from '../theme/tokens'
import { cssColor } from '../features/partPalette'
import type { AssemblyOutputs, InstanceEntry, MateEntry } from '../wire'
import type { AssemblyEdits } from './useAssembly'
import { useRowEye } from '../features/useRowEye'
import '../features/features.css'
import './assembly.css'

/** What a mate row does (card I10.6): the double click and the Tablet popover; the Desktop menu is the shell's (`onContext`). */
export interface MateRowActions {
  edit: (mate: MateEntry) => void
  rename: (mate: MateEntry) => void
  setSuppressed: (mate: MateEntry, suppressed: boolean) => void
  remove: (mate: MateEntry) => void
}

interface Props {
  collapsed: boolean
  /** Dialog open: the dialog covers the panel, so collapsing it is off. */
  locked: boolean
  /** The Assembly's outputs, or null while they load. */
  outputs: AssemblyOutputs | null
  /** The instance selected (alone) in the view or the list, or null. */
  selected: string | null
  edits: AssemblyEdits
  /** Edit, Rename, Suppress and Delete of a mate; without it the Mates list is read-only. */
  mates?: MateRowActions
  onSelect: (instance: InstanceEntry) => void
  /** Desktop mode: a right click on an instance row or a mate row (x, y: where, in window coordinates); the shell opens the menu. */
  onContext?: (target: { kind: 'instance'; instance: InstanceEntry } | { kind: 'mate'; mate: MateEntry }, x: number, y: number) => void
  onCollapsedChange: (collapsed: boolean) => void
}

/** Mate types as the desktop spells them in its list ("revolute" -> "Revolute"). */
const typeLabel = (t: string): string => (t.length === 0 ? t : t[0]!.toUpperCase() + t.slice(1).replace(/_/g, ' '))

function InstanceRow({ inst, error, selected, edits, onSelect, onMenu, onContext }: { inst: InstanceEntry; error: string | undefined; selected: boolean; edits: AssemblyEdits; onSelect: () => void; onMenu: (el: HTMLElement) => void; onContext: (x: number, y: number) => void }) {
  const state = error !== undefined ? 'failed' : undefined
  const off = edits.disabled || edits.busy
  const eyeOf = useRowEye(inst.hidden)
  // Tablet mode: a long press opens the popover; Desktop mode: a right click opens the context menu (the shell's)
  const right = useMenuPress(!edits.disabled, onMenu, onContext)
  return (
    <li {...eyeOf.bind} className="part-row instance-row" data-instance-row={inst.id} data-hidden={inst.hidden ? '' : undefined} data-fixed={inst.fixed ? '' : undefined} data-selected={selected ? '' : undefined}>
      <button type="button" className="panel-row part-select" data-instance={inst.id} data-state={state} aria-current={selected ? 'true' : undefined} aria-haspopup={edits.disabled ? undefined : 'menu'} onClick={onSelect} {...right}>
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
        data-eye={eyeOf.eyeFor(inst.fixed)}
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
        data-eye={eyeOf.eye}
        disabled={off}
        onClick={() => edits.setHidden(inst.id, !inst.hidden)}
      >
        <Icon d={inst.hidden ? paths.eyeOff : paths.eye} px={size.iconSmall} />
      </button>
    </li>
  )
}

function MateRow({ mate, names, error, canEdit, onEdit, onMenu, onContext }: { mate: MateEntry; names: ReadonlyMap<string, string>; error: string | undefined; canEdit: boolean; onEdit: () => void; onMenu: (el: HTMLElement) => void; onContext: (x: number, y: number) => void }) {
  // Tablet mode: a long press opens the popover; Desktop mode: a right click opens the context menu (the shell's) and a double click edits
  const press = useMenuPress(canEdit, onMenu, onContext)
  const detail = `${typeLabel(mate.type)}: ${names.get(mate.a) ?? mate.a} and ${names.get(mate.b) ?? mate.b}${mate.suppressed ? ', suppressed' : ''}`
  // Desktop mode shows the name alone (assembly.css); the type and the instances are the row's tooltip there
  return (
    <li className="mate-row" data-mate-row={mate.id} data-state={error !== undefined ? 'failed' : mate.suppressed ? 'suppressed' : undefined} title={detail} aria-haspopup={canEdit ? 'menu' : undefined} {...press} onDoubleClick={() => canEdit && onEdit()}>
      <span className="row-text">
        <span className="row-name">{mate.name}</span>
        <span className="row-message mate-detail">{detail}</span>
        {error !== undefined && <span className="row-message mate-error">{error}</span>}
      </span>
    </li>
  )
}

/**
 * The Assembly panel (card I8.1), in the place of the feature panel on an Assembly tab: "Instances (n)", one 44 px row per
 * instance (a tap selects it, with the same cube, name and colour swatch as the Parts list), a fixed toggle and the eye at the right end; under the
 * selected instance a Delete button (after a confirmation); then "Mates (n)", read-only, each with its type and any error.
 * Desktop mode (card F18), as the desktop's panel: "Instances (n)" is the title row itself, Delete is a small icon in it
 * (and the Delete key), and a mate row shows its name only.
 */
export function AssemblyPanel({ collapsed, locked, outputs, selected, edits, mates, onSelect, onContext, onCollapsedChange }: Props) {
  const [deleting, setDeleting] = useState<InstanceEntry | null>(null)
  const [menu, setMenu] = useState<{ instance: InstanceEntry; anchor: Anchor } | null>(null)
  const [mateMenu, setMateMenu] = useState<{ mate: MateEntry; anchor: Anchor } | null>(null)
  const desktop = useMode() === 'desktop'
  if (collapsed) {
    return (
      <div className="panel-rail" data-testid="panel-rail">
        <button type="button" className="icon-button" aria-label="Expand feature panel" onClick={() => onCollapsedChange(false)}>
          <Icon d={paths.expand} />
        </button>
      </div>
    )
  }
  const run = (action: () => void) => {
    setMateMenu(null)
    action()
  }
  const names = new Map(outputs?.instances.map((i) => [i.id, i.name]) ?? [])
  const errors = outputs?.errors ?? {}
  const canDelete = outputs !== null && selected !== null && !edits.disabled
  const askDelete = () => setDeleting(outputs?.instances.find((i) => i.id === selected) ?? null)
  const deleteLabel = `Delete ${(selected !== null && names.get(selected)) || selected}`
  return (
    <section className="panel" data-testid="assembly-panel" aria-label="Assembly panel" data-selected-instance={selected ?? undefined}>
      <div className="panel-header">
        {desktop ? (
          <div className="panel-title" id="instances-heading">
            Instances{outputs !== null ? ` (${outputs.instances.length})` : ''}
          </div>
        ) : (
          <div className="panel-title">Assembly</div>
        )}
        {desktop && canDelete && (
          <button type="button" className="icon-button instance-delete-icon" aria-label={deleteLabel} title="Delete (Del)" disabled={edits.busy} onClick={askDelete}>
            <Icon d={paths.trash} />
          </button>
        )}
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
            {!desktop && (
              <div className="parts-heading" id="instances-heading">
                Instances ({outputs.instances.length})
              </div>
            )}
            <ul className="parts-list" aria-labelledby="instances-heading" data-testid="instance-list">
              {outputs.instances.map((inst) => (
                <InstanceRow key={inst.id} inst={inst} error={errors[inst.id]} selected={inst.id === selected} edits={edits} onSelect={() => onSelect(inst)} onMenu={(el) => setMenu({ instance: inst, anchor: anchorOf(el) })} onContext={(x, y) => onContext?.({ kind: 'instance', instance: inst }, x, y)} />
              ))}
            </ul>
            {!desktop && canDelete && (
              <div className="instance-actions">
                <button
                  type="button"
                  className="instance-delete"
                  disabled={edits.busy}
                  aria-label={deleteLabel}
                  onClick={askDelete}
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
                <MateRow key={m.id} mate={m} names={names} error={errors[m.id]} canEdit={mates !== undefined && !edits.disabled} onEdit={() => mates?.edit(m)} onMenu={(el) => setMateMenu({ mate: m, anchor: anchorOf(el) })} onContext={(x, y) => onContext?.({ kind: 'mate', mate: m }, x, y)} />
              ))}
            </ul>
          </>
        )}
      </div>
      {menu && (
        <Popover anchor={menu.anchor} label={`${menu.instance.name} menu`} onClose={() => setMenu(null)}>
          <PopoverItem
            label="Origin connector"
            icon={<Icon d={paths.plus} />}
            disabled={edits.busy}
            onClick={() => {
              const id = menu.instance.id
              setMenu(null)
              void edits.addOriginConnector(id)
            }}
          />
        </Popover>
      )}
      {mateMenu && mates && (
        <Popover anchor={mateMenu.anchor} label={`${mateMenu.mate.name} menu`} onClose={() => setMateMenu(null)}>
          <PopoverItem label="Edit…" icon={<Icon d={paths.sketch} />} disabled={edits.busy} onClick={() => run(() => mates.edit(mateMenu.mate))} />
          <PopoverItem label="Rename…" icon={<Icon d={paths.tab} />} disabled={edits.busy} onClick={() => run(() => mates.rename(mateMenu.mate))} />
          <PopoverItem label={mateMenu.mate.suppressed ? 'Unsuppress' : 'Suppress'} icon={<Icon d={mateMenu.mate.suppressed ? paths.eye : paths.eyeOff} />} disabled={edits.busy} onClick={() => run(() => mates.setSuppressed(mateMenu.mate, !mateMenu.mate.suppressed))} />
          <PopoverItem label="Delete" icon={<Icon d={paths.trash} />} danger disabled={edits.busy} onClick={() => run(() => mates.remove(mateMenu.mate))} />
        </Popover>
      )}
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
