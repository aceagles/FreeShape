import { Fragment, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { Icon, paths } from '../shell/icons'
import { ConfirmDialog } from '../shell/Modal'
import { useLongPress } from '../shell/longPress'
import { anchorOf, Popover, PopoverItem } from '../shell/Popover'
import type { Anchor } from '../shell/Popover'
import { TextPrompt } from '../shell/TextPrompt'
import { size } from '../theme/tokens'
import type { FeatureEntry, StudioOutputs } from '../wire'
import { featureIcon } from './featureIcons'
import { useRowEye } from './useRowEye'
import { barPosition, buildRows, nearestSlot, slotBefore } from './tree'
import type { Row } from './tree'
import type { FeatureEdits } from './useFeatureEdits'

interface Props {
  outputs: StudioOutputs
  selected: string | null
  edits: FeatureEdits
  onEdit?: (feature: FeatureEntry) => void
  onSelect: (id: string) => void
  /** Desktop mode: the Origin rows (Top, Front, Right) with their eyes, as the desktop's list has them; absent in Tablet mode. */
  origin?: { hidden: ReadonlySet<string>; toggle: (name: string) => void }
}

type Dialog = { kind: 'rename' | 'delete'; feature: FeatureEntry }

/** What a row shows about its feature, as data attributes the stylesheet reads. */
function stateOf(f: FeatureEntry, rolledBack: boolean): 'failed' | 'suppressed' | 'rolled-back' | undefined {
  if (f.error !== null) return 'failed'
  if (f.suppressed) return 'suppressed'
  return rolledBack ? 'rolled-back' : undefined
}

/** The row's title: a failed feature reads "<name> failed", as in the layout. */
export const rowTitle = (f: FeatureEntry): string => (f.error !== null ? `${f.name} failed` : f.name)

/** Two taps on a row this close together (ms) are a double tap. */
const DOUBLE_TAP_MS = 400

/** Sketches, planes and axes have an eye; the other features do not. */
export const hasEye = (f: FeatureEntry): boolean => f.type === 'sketch' || f.type === 'plane' || f.type === 'axis'

function FeatureButton({ feature, index, indent, selected, rolledBack, slot, canMenu, eyeLocked, onSelect, onEdit, onMenu, onEye }: {
  feature: FeatureEntry
  index: number
  indent: boolean
  selected: boolean
  rolledBack: boolean
  slot: number | null
  canMenu: boolean
  eyeLocked: boolean
  onSelect: () => void
  onEdit?: () => void
  onMenu: (el: HTMLElement) => void
  onEye: () => void
}) {
  const press = useLongPress((el) => canMenu && onMenu(el))
  const lastTap = useRef<number | null>(null)
  const state = stateOf(feature, rolledBack)
  const { eye, bind } = useRowEye(feature.hidden)
  return (
    <li data-row="" data-slot={slot ?? undefined} {...bind} className={hasEye(feature) ? 'feature-eyed' : undefined} data-hidden={hasEye(feature) && feature.hidden ? '' : undefined}>
      <button
        type="button"
        className="panel-row"
        data-feature={feature.id}
        data-index={index}
        data-type={feature.type}
        data-state={state}
        data-indent={indent ? '' : undefined}
        aria-current={selected ? 'true' : undefined}
        aria-haspopup={canMenu ? 'menu' : undefined}
        onClick={(e) => {
          onSelect()
          // a double tap opens the feature's dialog (the click's own time stamp: no timer)
          const before = lastTap.current
          lastTap.current = e.timeStamp
          if (onEdit && before !== null && e.timeStamp - before <= DOUBLE_TAP_MS) {
            lastTap.current = null
            onEdit()
          }
        }}
        {...press}
      >
        <Icon d={featureIcon(feature.type)} px={size.iconSmall} stroke={state === 'failed' ? 'var(--c-danger)' : 'var(--c-inkSoft)'} />
        <span className="row-text">
          <span className="row-name">{rowTitle(feature)}</span>
          {feature.error !== null && <span className="row-message">{feature.error}</span>}
        </span>
      </button>
      {hasEye(feature) && (
        <button
          type="button"
          className="icon-button feature-eye"
          aria-label={`${feature.hidden ? 'Show' : 'Hide'} ${feature.type}`}
          title={`${feature.hidden ? 'Show' : 'Hide'} ${feature.name}`}
          data-feature-eye={feature.id}
          data-eye={eye}
          disabled={eyeLocked}
          onClick={onEye}
        >
          <Icon d={feature.hidden ? paths.eyeOff : paths.eye} px={size.iconSmall} />
        </button>
      )}
    </li>
  )
}

const ORIGIN_PLANES = ['Top', 'Front', 'Right'] as const

/** One of the default planes: a row with an eye. Their state is the tab's view state (viewport/visibility.ts), not the server's. */
function OriginRow({ name, hidden, onEye }: { name: string; hidden: boolean; onEye: () => void }) {
  const { eye, bind } = useRowEye(hidden)
  return (
    <li {...bind} className="feature-eyed" data-origin-row={name} data-hidden={hidden ? '' : undefined}>
      <span className="panel-row" data-origin={name}>
        <Icon d={featureIcon('plane')} px={size.iconSmall} stroke="var(--c-inkSoft)" />
        <span className="row-text">
          <span className="row-name">{name}</span>
        </span>
      </span>
      <button
        type="button"
        className="icon-button feature-eye"
        aria-label={`${hidden ? 'Show' : 'Hide'} ${name} plane`}
        title={`${hidden ? 'Show' : 'Hide'} ${name}`}
        data-origin-eye={name}
        data-eye={eye}
        onClick={onEye}
      >
        <Icon d={hidden ? paths.eyeOff : paths.eye} px={size.iconSmall} />
      </button>
    </li>
  )
}

function FolderButton({ row, rolledBack, slot, onToggle }: { row: Extract<Row, { kind: 'folder' }>; rolledBack: boolean; slot: number; onToggle: () => void }) {
  const { folder, open } = row
  const failed = !open && folder.failed > 0
  return (
    <li data-row="" data-slot={slot}>
      <button
        type="button"
        className="panel-row"
        data-folder={folder.id}
        data-state={failed ? 'failed' : folder.suppressed ? 'suppressed' : rolledBack ? 'rolled-back' : undefined}
        aria-expanded={open}
        onClick={onToggle}
      >
        <Icon d={open ? paths.chevronDown : paths.chevronRight} px={size.iconSmall} stroke="var(--c-muted)" />
        <Icon d={paths.folder} px={size.iconSmall} stroke={failed ? 'var(--c-danger)' : 'var(--c-inkSoft)'} />
        <span className="row-text">
          <span className="row-name">{folder.name}</span>
          {failed && <span className="row-message">{folder.failed === 1 ? '1 feature failed' : `${folder.failed} features failed`}</span>}
        </span>
      </button>
    </li>
  )
}

interface Drag {
  pointer: number
  /** the slot (0..n) the bar is over now */
  slot: number
  /** how far the bar is moved from where it is drawn, px */
  shift: number
  /** y (content coordinates of the scrolling body) of each slot's line, and of the bar where it stands */
  lines: Array<{ slot: number; y: number }>
  centre: number
}

/**
 * The feature list of a Part Studio: rows in file order with a type icon, the suppressed, failed and rolled-back
 * states, folders as collapsible groups, and the rollback bar. A tap selects; a press of 500 ms opens the row's menu
 * (Rename, Hide or Show for a sketch, plane or axis, Suppress or Unsuppress, Delete, Roll to here); the eye at the right of such a row does the same as Hide or Show. The bar moves only by its Drag handle, and one set_rollback
 * is sent when the handle is released. Delete asks first; Rename asks for the new name.
 */
export function FeatureList({ outputs, selected, edits, onEdit, onSelect, origin }: Props) {
  const [menu, setMenu] = useState<{ feature: FeatureEntry; index: number; anchor: Anchor } | null>(null)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [openChoice, setOpenChoice] = useState<Record<string, boolean>>({})
  const [drag, setDrag] = useState<Drag | null>(null)
  const [pending, setPending] = useState<{ index: number; revision: number } | null>(null)
  const body = useRef<HTMLUListElement>(null)

  const total = outputs.features.length
  // what was just sent shows at once, until the outputs of the edit arrive
  const rollback = pending !== null && pending.revision === outputs.revision ? pending.index : outputs.rollback
  const shownRollback = drag ? drag.slot : rollback
  const rows = buildRows({ ...outputs, rollback }, openChoice)
  const barAt = barPosition(rows, rollback, total)
  const locked = edits.disabled || edits.busy

  /** Y of each place the bar can sit, from where the rows are now, in the scrolling body's own coordinates. */
  const measure = (): { lines: Drag['lines']; centre: number; top: number; scroll: number } | null => {
    const list = body.current
    const scroller = list?.parentElement
    if (!list || !scroller) return null
    const top = scroller.getBoundingClientRect().top
    const scroll = scroller.scrollTop
    const y = (r: DOMRect, at: 'top' | 'bottom' | 'mid') => (at === 'top' ? r.top : at === 'bottom' ? r.bottom : r.top + r.height / 2) - top + scroll
    const bar = list.querySelector('[data-rollback-bar]')
    const centre = bar ? y(bar.getBoundingClientRect(), 'mid') : 0
    const lines: Drag['lines'] = []
    for (const el of list.querySelectorAll<HTMLElement>('[data-slot]')) {
      const slot = Number(el.dataset.slot)
      lines.push({ slot, y: slot === rollback ? centre : y(el.getBoundingClientRect(), 'top') })
    }
    // the end of the list is the slot after the last feature
    const last = [...list.querySelectorAll('[data-row]')].at(-1)
    lines.push({ slot: total, y: rollback >= total || !last ? centre : y(last.getBoundingClientRect(), 'bottom') })
    return { lines, centre, top, scroll }
  }

  const startDrag = (e: ReactPointerEvent<HTMLElement>) => {
    if (locked || (e.pointerType === 'mouse' && e.button !== 0)) return
    const m = measure()
    if (!m) return
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {
      // a pointer the browser no longer has (or a test's): the drag still follows the moves that reach the handle
    }
    setDrag({ pointer: e.pointerId, slot: rollback, shift: 0, lines: m.lines, centre: m.centre })
  }
  const moveDrag = (e: ReactPointerEvent<HTMLElement>) => {
    if (!drag || e.pointerId !== drag.pointer) return
    const scroller = body.current?.parentElement
    if (!scroller) return
    const box = scroller.getBoundingClientRect()
    // near the top or bottom edge the list scrolls on with the pointer (no timer: it follows the pointer's moves)
    if (e.clientY < box.top + 36) scroller.scrollTop -= 12
    else if (e.clientY > box.bottom - 36) scroller.scrollTop += 12
    const slot = nearestSlot(drag.lines, e.clientY - box.top + scroller.scrollTop)
    const line = drag.lines.find((l) => l.slot === slot)
    setDrag({ ...drag, slot, shift: (line?.y ?? drag.centre) - drag.centre })
  }
  const endDrag = (e: ReactPointerEvent<HTMLElement>, commit: boolean) => {
    if (!drag || e.pointerId !== drag.pointer) return
    try {
      e.currentTarget.releasePointerCapture(e.pointerId)
    } catch {
      // already released
    }
    const slot = drag.slot
    setDrag(null)
    if (!commit || slot === rollback) return
    setPending({ index: slot, revision: outputs.revision })
    void edits.setRollback(slot).then((ok) => {
      if (!ok) setPending(null)
    })
  }

  const bar = (
    <li key="rollback" data-rollback-bar="" data-testid="rollback-bar" data-dragging={drag ? '' : undefined} className="rollback-bar" style={drag ? { transform: `translateY(${drag.shift}px)` } : undefined}>
      <div className="rollback-line" />
      <button
        type="button"
        className="rollback-handle"
        data-testid="rollback-handle"
        aria-label="Rollback bar, drag to move"
        disabled={locked}
        onPointerDown={startDrag}
        onPointerMove={moveDrag}
        onPointerUp={(e) => endDrag(e, true)}
        onPointerCancel={(e) => endDrag(e, false)}
        onContextMenu={(e) => e.preventDefault()}
      >
        <Icon d={paths.handle} sw={2} round={false} />
      </button>
    </li>
  )

  const open = (f: FeatureEntry, index: number, el: HTMLElement) => setMenu({ feature: f, index, anchor: anchorOf(el) })
  const close = () => setMenu(null)

  return (
    <>
      <ul ref={body} className="feature-list" aria-label="Features">
        {origin && ORIGIN_PLANES.map((n) => <OriginRow key={`origin:${n}`} name={n} hidden={origin.hidden.has(n)} onEye={() => origin.toggle(n)} />)}
        {rows.map((row, at) => {
          const slot = slotBefore(row, rows, at)
          const rolledBack = (row.kind === 'folder' ? row.start : row.index) >= shownRollback
          return (
            <Fragment key={row.kind === 'folder' ? `folder:${row.folder.id}` : row.feature.id}>
              {barAt === at && bar}
              {row.kind === 'folder' ? (
                <FolderButton
                  row={row}
                  rolledBack={rolledBack}
                  slot={row.start}
                  onToggle={() => setOpenChoice({ ...openChoice, [row.folder.id]: !row.open })}
                />
              ) : (
                <FeatureButton
                  feature={row.feature}
                  index={row.index}
                  indent={row.inFolder}
                  selected={row.feature.id === selected}
                  rolledBack={rolledBack}
                  slot={slot}
                  canMenu={!edits.disabled}
                  eyeLocked={edits.disabled || edits.busy}
                  onEye={() => edits.setFeatureHidden(row.feature.id, !row.feature.hidden)}
                  onSelect={() => onSelect(row.feature.id)}
                  onEdit={onEdit ? () => onEdit(row.feature) : undefined}
                  onMenu={(el) => open(row.feature, row.index, el)}
                />
              )}
            </Fragment>
          )
        })}
        {barAt >= rows.length && bar}
      </ul>
      {menu && (
        <Popover anchor={menu.anchor} label={`${menu.feature.name} menu`} onClose={close}>
          {onEdit && (
            <PopoverItem
              label="Edit"
              icon={<Icon d={paths.extrude} />}
              disabled={edits.busy}
              onClick={() => {
                close()
                onEdit(menu.feature)
              }}
            />
          )}
          <PopoverItem
            label="Rename"
            icon={<Icon d={paths.sketch} />}
            disabled={edits.busy}
            onClick={() => {
              close()
              setDialog({ kind: 'rename', feature: menu.feature })
            }}
          />
          {hasEye(menu.feature) && (
            <PopoverItem
              label={menu.feature.hidden ? 'Show' : 'Hide'}
              icon={<Icon d={menu.feature.hidden ? paths.eye : paths.eyeOff} />}
              disabled={edits.busy}
              onClick={() => {
                close()
                edits.setFeatureHidden(menu.feature.id, !menu.feature.hidden)
              }}
            />
          )}
          <PopoverItem
            label={menu.feature.suppressed ? 'Unsuppress' : 'Suppress'}
            icon={<Icon d={paths.hole} />}
            disabled={edits.busy}
            onClick={() => {
              close()
              edits.setSuppressed(menu.feature.id, !menu.feature.suppressed)
            }}
          />
          <PopoverItem
            label="Delete"
            icon={<Icon d={paths.trash} />}
            danger
            disabled={edits.busy}
            onClick={() => {
              close()
              setDialog({ kind: 'delete', feature: menu.feature })
            }}
          />
          <PopoverItem
            label="Roll to here"
            icon={<Icon d={paths.handle} />}
            disabled={edits.busy || rollback === menu.index + 1}
            onClick={() => {
              close()
              void edits.setRollback(menu.index + 1)
            }}
          />
        </Popover>
      )}
      {dialog?.kind === 'rename' && (
        <TextPrompt
          title="Rename feature"
          label="Name"
          initial={dialog.feature.name}
          confirmLabel="Rename"
          onSubmit={async (value) => {
            const problem = await edits.rename(dialog.feature.id, value)
            if (typeof problem === 'string') return problem
            setDialog(null)
          }}
          onCancel={() => setDialog(null)}
        />
      )}
      {dialog?.kind === 'delete' && (
        <ConfirmDialog
          title={`Delete "${dialog.feature.name}"?`}
          message="The feature is removed from the Part Studio. Undo brings it back."
          confirm={{ label: 'Delete', danger: true }}
          onConfirm={() => {
            setDialog(null)
            void edits.deleteFeature(dialog.feature.id)
          }}
          onCancel={() => setDialog(null)}
        />
      )}
    </>
  )
}
