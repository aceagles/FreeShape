import { useState } from 'react'
import { Icon, paths } from '../shell/icons'
import { Modal, ModalButtons } from '../shell/Modal'
import { useLongPress } from '../shell/longPress'
import { anchorOf, Popover, PopoverItem } from '../shell/Popover'
import type { Anchor } from '../shell/Popover'
import { TextPrompt } from '../shell/TextPrompt'
import { size } from '../theme/tokens'
import type { Part, StudioOutputs } from '../wire'
import { cssColor, PART_PALETTE, sameColor } from './partPalette'
import type { FeatureEdits } from './useFeatureEdits'

interface Props {
  outputs: StudioOutputs
  selected: string | null
  edits: FeatureEdits
  onSelect: (id: string) => void
}

type Dialog = { kind: 'rename' | 'colour'; part: Part }

function PartButton({ part, selected, canMenu, onSelect, onMenu }: { part: Part; selected: boolean; canMenu: boolean; onSelect: () => void; onMenu: (el: HTMLElement) => void }) {
  const press = useLongPress((el) => canMenu && onMenu(el))
  return (
    <button
      type="button"
      className="panel-row part-select"
      data-part={part.id}
      data-hidden={part.hidden ? '' : undefined}
      aria-current={selected ? 'true' : undefined}
      aria-haspopup={canMenu ? 'menu' : undefined}
      onClick={onSelect}
      {...press}
    >
      <Icon d={paths.cube} px={size.iconSmall} stroke="var(--c-inkSoft)" />
      <span className="row-text">
        <span className="row-name">{part.name}</span>
      </span>
      <span className="part-swatch" data-testid="part-swatch" style={{ background: cssColor(part.color) }} />
    </button>
  )
}

function ColourDialog({ part, onPick, onCancel }: { part: Part; onPick: (rgb: readonly number[] | null) => void; onCancel: () => void }) {
  return (
    <Modal title="Part colour" onClose={onCancel}>
      <p className="modal-message">Colour for {part.name}.</p>
      <div className="colour-grid" role="group" aria-label="Colours">
        {PART_PALETTE.map((c) => (
          <button
            key={c.name}
            type="button"
            className="colour-choice"
            aria-label={c.name}
            aria-pressed={sameColor(part.color, c.rgb)}
            style={{ background: cssColor(c.rgb) }}
            onClick={() => onPick(c.rgb)}
          />
        ))}
      </div>
      <ModalButtons
        buttons={[
          { label: 'Cancel', onClick: onCancel },
          { label: 'Default colour', onClick: () => onPick(null) },
        ]}
      />
    </Modal>
  )
}

/**
 * The Parts list of a Part Studio, under the feature list: the heading "Parts (n)" and one 44 px row per part with a
 * cube, its name and a swatch of its colour, and the eye as a 44 px button at the right end. A tap on the row selects the
 * part; a press of 500 ms opens its menu (Rename, Colour, Hide or Show). The eye, rename and colour are
 * set_part_override. Context bodies are other tabs' bodies, not parts of this studio: they are not listed.
 * The desktop's split into Parts and Surfaces is left out on purpose.
 */
export function PartsList({ outputs, selected, edits, onSelect }: Props) {
  const [menu, setMenu] = useState<{ part: Part; anchor: Anchor } | null>(null)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const parts = outputs.parts
  const close = () => setMenu(null)
  return (
    <>
      <div className="parts-heading" id="parts-heading">
        Parts ({parts.length})
      </div>
      <ul className="parts-list" aria-labelledby="parts-heading" data-testid="parts-list">
        {parts.map((p) => (
          <li key={p.id} className="part-row" data-part-row={p.id} data-hidden={p.hidden ? '' : undefined} data-selected={p.id === selected ? '' : undefined}>
            <PartButton part={p} selected={p.id === selected} canMenu={!edits.disabled} onSelect={() => onSelect(p.id)} onMenu={(el) => setMenu({ part: p, anchor: anchorOf(el) })} />
            <button
              type="button"
              className="icon-button part-eye"
              aria-label={p.hidden ? `Show ${p.name}` : `Hide ${p.name}`}
              data-part-eye={p.id}
              disabled={edits.disabled || edits.busy}
              onClick={() => edits.setPartHidden(p.id, !p.hidden)}
            >
              <Icon d={p.hidden ? paths.eyeOff : paths.eye} px={size.iconSmall} />
            </button>
          </li>
        ))}
      </ul>
      {menu && (
        <Popover anchor={menu.anchor} label={`${menu.part.name} menu`} onClose={close}>
          <PopoverItem
            label="Rename"
            icon={<Icon d={paths.sketch} />}
            disabled={edits.busy}
            onClick={() => {
              close()
              setDialog({ kind: 'rename', part: menu.part })
            }}
          />
          <PopoverItem
            label="Colour"
            icon={<Icon d={paths.drop} />}
            disabled={edits.busy}
            onClick={() => {
              close()
              setDialog({ kind: 'colour', part: menu.part })
            }}
          />
          <PopoverItem
            label={menu.part.hidden ? 'Show' : 'Hide'}
            icon={<Icon d={menu.part.hidden ? paths.eye : paths.eyeOff} />}
            disabled={edits.busy}
            onClick={() => {
              close()
              edits.setPartHidden(menu.part.id, !menu.part.hidden)
            }}
          />
        </Popover>
      )}
      {dialog?.kind === 'rename' && (
        <TextPrompt
          title="Rename part"
          label="Name"
          initial={dialog.part.name}
          confirmLabel="Rename"
          onSubmit={async (value) => {
            const problem = await edits.renamePart(dialog.part.id, value)
            if (typeof problem === 'string') return problem
            setDialog(null)
          }}
          onCancel={() => setDialog(null)}
        />
      )}
      {dialog?.kind === 'colour' && (
        <ColourDialog
          part={dialog.part}
          onPick={(rgb) => {
            const part = dialog.part
            setDialog(null)
            void edits.setPartColor(part.id, rgb)
          }}
          onCancel={() => setDialog(null)}
        />
      )}
    </>
  )
}
