import { useEffect, useRef, useState } from 'react'
import { checkExpression, formatNumber } from '../pad/editor'
import { NumberPad, PadField } from '../pad/NumberPad'
import { usePadEditor } from '../pad/usePadEditor'
import { Icon, paths } from '../shell/icons'
import { MenuButton } from '../shell/Menu'
import type { MenuItem } from '../shell/menus'
import type { FieldSpec, FieldValue, SelItem } from './dialogApi'
import { fieldKeyDown, InlineNumber } from './InlineNumber'
import { isSegmented, labelOf, numberText } from './schema'
import { countOf } from './selectionFields'

/**
 * A row's label, and under it the row, its message and its info text. `inline` (Desktop mode) puts the label at the left,
 * taking the spare width, and the field at its right, as the desktop's FeatureDialog rows.
 */
function Row({ label, active, children, message, info, inline = false }: { label?: string; active?: boolean; children: React.ReactNode; message?: string | null; info?: string | null; inline?: boolean }) {
  const head = label !== undefined && label !== '' && (
    <div className="dlg-label" data-active={active ? '' : undefined}>
      {label}
    </div>
  )
  return (
    <div className={inline ? 'dlg-row dlg-row-inline' : 'dlg-row'}>
      {inline ? (
        <div className="dlg-row-line">
          {head}
          {children}
        </div>
      ) : (
        <>
          {head}
          {children}
        </>
      )}
      {message && (
        <div className="dlg-message" role="alert">
          {message}
        </div>
      )}
      {info && <div className="dlg-info">{info}</div>}
    </div>
  )
}

/** The number being edited: its text on the pad's field, the pad itself in the dock. Enter sends the text; Cancel puts the old one back. */
function PadNumber({ spec, text, variables, flip, onCommit, onDone }: { spec: FieldSpec; text: string; variables: readonly string[]; flip: React.ReactNode; onCommit: (text: string) => void; onDone: () => void }) {
  const editor = usePadEditor({
    value: text,
    variables,
    onCommit: (t) => {
      if (t.trim() !== text.trim()) onCommit(t)
      onDone()
    },
    onCancel: onDone,
  })
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    box.current?.scrollIntoView?.({ block: 'nearest' })
  }, [])
  return (
    <div ref={box} className="dlg-number">
      <div className="dlg-number-field">
        <PadField editor={editor} label={labelOf(spec)} />
      </div>
      {flip}
      <NumberPad editor={editor} />
    </div>
  )
}

export function NumberRow({
  spec,
  value,
  sent,
  error,
  info,
  editing,
  variables,
  flip,
  onEdit,
  onCommit,
  onDone,
}: {
  spec: FieldSpec
  value: FieldValue | undefined
  sent: unknown
  error: string | null
  info: string | null
  editing: boolean
  variables: readonly string[]
  /** The flip button drawn beside the field, or null. */
  flip: React.ReactNode
  onEdit: () => void
  onCommit: (text: string) => void
  onDone: () => void
}) {
  const text = numberText(spec, value, sent)
  return (
    <Row message={error} info={info} {...(editing ? {} : { label: labelOf(spec) })}>
      {editing ? (
        <PadNumber spec={spec} text={text} variables={variables} flip={flip} onCommit={onCommit} onDone={onDone} />
      ) : (
        <div className="dlg-number">
          <button type="button" className="dlg-field" data-invalid={error ? '' : undefined} aria-label={`${labelOf(spec)}, ${text}. Tap to edit on the pad`} onClick={onEdit}>
            <span>{text}</span>
          </button>
          {flip}
        </div>
      )}
    </Row>
  )
}

/** The Extrude's Flip: a toggle button beside the Depth field, as in the layout. */
export function FlipButton({ spec, on, onToggle }: { spec: FieldSpec; on: boolean; onToggle: () => void }) {
  return (
    <button type="button" className="dlg-flip" aria-label={labelOf(spec)} aria-pressed={on} onClick={onToggle}>
      <Icon d={paths.flip} sw={1.8} />
    </button>
  )
}

/** Why an option of a choice cannot be chosen here ("Desktop only for now"), or undefined when it can. */
const offWhy = (spec: FieldSpec, v: string): string | undefined => spec.disabled?.[v]

export function ChoiceRow({ spec, value, error, onPick }: { spec: FieldSpec; value: unknown; error: string | null; onPick: (v: string) => void }) {
  const options = spec.options ?? []
  const [open, setOpen] = useState(false)
  const label = labelOf(spec)
  if (isSegmented(spec)) {
    return (
      <Row label={label} message={error}>
        <div className="dlg-segments" role="radiogroup" aria-label={label}>
          {options.map(([v, text]) => (
            <button key={v} type="button" role="radio" aria-checked={value === v} className="dlg-segment" disabled={offWhy(spec, v) !== undefined} title={offWhy(spec, v)} onClick={() => value !== v && onPick(v)}>
              {text}
            </button>
          ))}
        </div>
      </Row>
    )
  }
  const current = options.find(([v]) => v === value) ?? options[0]     // an unknown value shows the first option, as the desktop combo does
  return (
    <Row label={label} message={error}>
      <button type="button" className="dlg-field dlg-field-choice" aria-haspopup="listbox" aria-expanded={open} aria-label={`${label}, ${current?.[1] ?? 'none'}`} onClick={() => setOpen(!open)}>
        <span>{current?.[1] ?? ''}</span>
        <Icon d={open ? paths.chevronDown : paths.chevronRight} px={20} stroke="var(--c-muted)" />
      </button>
      {open && (
        <div className="dlg-list" role="radiogroup" aria-label={label}>
          {options.map(([v, text]) => {
            const why = offWhy(spec, v)
            return (
              <button
                key={v}
                type="button"
                role="radio"
                aria-checked={value === v}
                className="dlg-list-item"
                disabled={why !== undefined}
                title={why}
                onClick={() => {
                  setOpen(false)
                  if (value !== v) onPick(v)
                }}
              >
                <span>{text}</span>
                {why !== undefined ? <span className="dlg-list-why">{why}</span> : value === v && <Icon d={paths.check} px={20} sw={2.2} />}
              </button>
            )
          })}
        </div>
      )}
    </Row>
  )
}

export function CheckRow({ spec, on, onToggle }: { spec: FieldSpec; on: boolean; onToggle: () => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} className="dlg-switch" onClick={onToggle}>
      <span>{labelOf(spec)}</span>
      <span className="dlg-track" aria-hidden="true">
        <span className="dlg-thumb" />
      </span>
    </button>
  )
}

export const NUMBER_PER_ITEM_TEXT = 'A number for each item can only be set on the desktop for now.'

/**
 * A selection row (card I5.7). The field shows its items as chips, each with its name and a remove button, and the count
 * ("1 item", as in the layout). Tapping the field makes it the active one: the view picks for it and the field is drawn
 * with the accent border of the layout's active number. A field with a number per item is read-only and says so.
 */
export function SelectionRow({
  spec,
  items,
  label,
  active,
  readOnly,
  message,
  onActivate,
  onRemove,
}: {
  spec: FieldSpec
  items: readonly SelItem[]
  /** What a chip says for each item. */
  label: (item: SelItem) => string
  active: boolean
  readOnly: boolean
  message: string | null
  onActivate: () => void
  onRemove: (index: number) => void
}) {
  const name = labelOf(spec)
  const hint = readOnly ? NUMBER_PER_ITEM_TEXT : (spec.tip ?? null)
  return (
    <Row label={name} active={active} message={message} info={hint}>
      <div className="dlg-sel" role="group" aria-label={name} data-active={active ? '' : undefined} data-readonly={readOnly ? '' : undefined} data-empty={items.length === 0 ? '' : undefined} data-testid={`sel-${spec.key}`}>
        <div className="dlg-chips">
          {items.map((item, i) => {
            const text = label(item)
            return (
              <span key={`${i}:${text}`} className="dlg-chip" data-testid="sel-chip">
                <span className="dlg-chip-name">{text}</span>
                {!readOnly && (
                  <button type="button" className="dlg-chip-x" aria-label={`Remove ${text}`} onClick={() => onRemove(i)}>
                    <Icon d={paths.close} px={16} sw={2} />
                  </button>
                )}
              </span>
            )
          })}
        </div>
        <button type="button" className="dlg-sel-tap" disabled={readOnly} aria-pressed={active} aria-label={`${name}, ${countOf(items.length)}. ${active ? 'Picking in the view: tap to stop' : 'Tap, then pick in the view'}`} onClick={onActivate}>
          <span className="dlg-sel-hint">{items.length === 0 ? (active ? 'Pick in the view' : 'Nothing selected') : active ? 'Picking in the view' : ''}</span>
          {items.length > 0 && <span className="dlg-sel-count">{countOf(items.length)}</span>}
        </button>
      </div>
    </Row>
  )
}

export function InfoRow({ text }: { text: string }) {
  return text === '' ? null : <div className="dlg-info dlg-info-row">{text}</div>
}

/** A text row. It is the one native input of a dialog (a name is words, not a number); the change is sent when the person is done. */
export function TextRow({ spec, value, error, onCommit }: { spec: FieldSpec; value: string; error: string | null; onCommit: (text: string) => void }) {
  const [draft, setDraft] = useState<string | null>(null)
  const label = labelOf(spec)
  const done = () => {
    if (draft !== null && draft !== value) onCommit(draft)
    setDraft(null)
  }
  return (
    <Row label={label} message={error}>
      <input
        className="dlg-field dlg-text"
        aria-label={label}
        placeholder={spec.placeholder ?? ''}
        value={draft ?? value}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={done}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
      />
    </Row>
  )
}

/* ------------------------------------------------------------------------------------------------------------------
 * Desktop mode (card I9.16): the rows of the desktop's FeatureDialog (freeshape/ui/dialogs.py `_make_row`). A number is
 * typed in an InlineNumber, a choice is a dropdown drawn with the Menu component, a check is a checkbox with its label.
 * Enter in a field commits it and accepts, Esc cancels, Tab and Shift+Tab move between the fields of the form.
 */

/** What Enter and Esc in a Desktop field do: the dialog's Accept and Cancel. */
export interface RowKeys {
  onEnter: () => void
  onEscape: () => void
}

/**
 * What a Desktop number field shows: the text as typed while the server's answer is awaited; then the number the server
 * evaluated it to, as the desktop shows it after editing (`_show`), or the text as typed where it names a variable (it
 * follows the variable) or the server refused it. The server is the only source of the value.
 */
export function desktopNumberText(value: FieldValue | undefined, sent: unknown): string {
  if (sent !== undefined && sent !== null) return String(sent)
  const text = value?.text ?? ''
  if (value?.error || /#[A-Za-z_]/.test(text)) return text
  if (typeof value?.value === 'number') return formatNumber(value.value)
  return text
}

export function DesktopNumberRow({
  spec,
  value,
  sent,
  error,
  variables,
  keys,
  onCommit,
  onPad,
}: {
  spec: FieldSpec
  value: FieldValue | undefined
  sent: unknown
  error: string | null
  variables: readonly string[]
  keys: RowKeys
  onCommit: (text: string) => void
  /** A pen or touch press on the field: open the pad on it. */
  onPad: () => void
}) {
  const label = labelOf(spec)
  return (
    <Row inline label={label} message={error}>
      <InlineNumber
        value={desktopNumberText(value, sent)}
        label={label}
        variables={variables}
        unit={spec.unit ?? null}
        invalid={error !== null}
        onCommit={onCommit}
        onEnter={keys.onEnter}
        onEscape={keys.onEscape}
        onPad={onPad}
        fieldKey={spec.key ?? undefined}
        testId={`inline-${spec.key}`}
      />
    </Row>
  )
}

/** A choice as the desktop's QComboBox: a button with the current option that drops down the options (I9.2's Menu). */
export function DesktopChoiceRow({ spec, value, error, keys, onPick }: { spec: FieldSpec; value: unknown; error: string | null; keys: RowKeys; onPick: (v: string) => void }) {
  const options = spec.options ?? []
  const label = labelOf(spec)
  const current = options.find(([v]) => v === value) ?? options[0] // an unknown value shows the first option, as the desktop combo does
  // an option that cannot be chosen here has no command: the menu shows it off, with its reason as the tooltip
  const entries: MenuItem[] = options.map(([v, text]) => ({ command: `option:${v}`, label: text, check: true, checked: current?.[0] === v, note: offWhy(spec, v) }))
  const commands = Object.fromEntries(options.filter(([v]) => offWhy(spec, v) === undefined).map(([v]) => [`option:${v}`, () => current?.[0] !== v && onPick(v)]))
  return (
    <Row inline label={label} message={error}>
      <span
        className="dlg-combo"
        data-dlg-field=""
        data-testid={`combo-${spec.key}`}
        onKeyDown={(e) => {
          // the keys of the closed button only: the open list reads its own (arrows, Enter, Esc)
          if (!(e.target instanceof HTMLElement) || e.target.getAttribute('aria-haspopup') !== 'menu') return
          fieldKeyDown(e, keys)
        }}
      >
        <MenuButton label={label} entries={entries} commands={commands} state={{ readOnly: false, tabKind: null }} className="dlg-combo-button" ariaLabel={`${label}, ${current?.[1] ?? 'none'}`}>
          <span className="dlg-combo-text">{current?.[1] ?? ''}</span>
          <Icon d={paths.chevronDown} px={14} stroke="var(--c-muted)" />
        </MenuButton>
      </span>
    </Row>
  )
}

/** A check row as the desktop's: one checkbox with its label. */
export function DesktopCheckRow({ spec, on, keys, onToggle }: { spec: FieldSpec; on: boolean; keys: RowKeys; onToggle: () => void }) {
  return (
    <div className="dlg-row">
      <label className="dlg-check">
        <input type="checkbox" data-dlg-field="" checked={on} onChange={onToggle} onKeyDown={(e) => fieldKeyDown(e, keys)} />
        <span>{labelOf(spec)}</span>
      </label>
    </div>
  )
}

/** A text row as the desktop's: a 150 px field at the right of its label; an expression field is marked when it cannot be read. */
export function DesktopTextRow({ spec, value, error, variables, keys, onCommit }: { spec: FieldSpec; value: string; error: string | null; variables: readonly string[]; keys: RowKeys; onCommit: (text: string) => void }) {
  const [draft, setDraft] = useState<string | null>(null)
  const label = labelOf(spec)
  const shown = draft ?? value
  const bad = error !== null || (spec.expr === true && shown.trim() !== '' && !checkExpression(shown, variables).ok)
  const done = (): boolean => {
    if (draft !== null && draft !== value) onCommit(draft)
    setDraft(null)
    return true
  }
  return (
    <Row inline label={label} message={error}>
      <input
        className="dlg-text-inline"
        type="text"
        aria-label={label}
        aria-invalid={bad ? true : undefined}
        data-invalid={bad ? '' : undefined}
        data-dlg-field=""
        placeholder={spec.placeholder ?? ''}
        value={shown}
        spellCheck={false}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={done}
        onKeyDown={(e) => fieldKeyDown(e, { commit: done, onEnter: keys.onEnter, onEscape: keys.onEscape })}
      />
    </Row>
  )
}
