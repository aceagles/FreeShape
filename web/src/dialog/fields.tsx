import { useEffect, useRef, useState } from 'react'
import { NumberPad, PadField } from '../pad/NumberPad'
import { usePadEditor } from '../pad/usePadEditor'
import { Icon, paths } from '../shell/icons'
import type { FieldSpec, FieldValue, SelItem } from './dialogApi'
import { isSegmented, labelOf, numberText } from './schema'

/** A row's label, and under it the row, its message and its info text. */
function Row({ label, active, children, message, info }: { label?: string; active?: boolean; children: React.ReactNode; message?: string | null; info?: string | null }) {
  return (
    <div className="dlg-row">
      {label !== undefined && label !== '' && (
        <div className="dlg-label" data-active={active ? '' : undefined}>
          {label}
        </div>
      )}
      {children}
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

export function ChoiceRow({ spec, value, error, onPick }: { spec: FieldSpec; value: unknown; error: string | null; onPick: (v: string) => void }) {
  const options = spec.options ?? []
  const [open, setOpen] = useState(false)
  const label = labelOf(spec)
  if (isSegmented(spec)) {
    return (
      <Row label={label} message={error}>
        <div className="dlg-segments" role="radiogroup" aria-label={label}>
          {options.map(([v, text]) => (
            <button key={v} type="button" role="radio" aria-checked={value === v} className="dlg-segment" onClick={() => value !== v && onPick(v)}>
              {text}
            </button>
          ))}
        </div>
      </Row>
    )
  }
  const current = options.find(([v]) => v === value)
  return (
    <Row label={label} message={error}>
      <button type="button" className="dlg-field dlg-field-choice" aria-haspopup="listbox" aria-expanded={open} aria-label={`${label}, ${current?.[1] ?? 'none'}`} onClick={() => setOpen(!open)}>
        <span>{current?.[1] ?? ''}</span>
        <Icon d={open ? paths.chevronDown : paths.chevronRight} px={20} stroke="var(--c-muted)" />
      </button>
      {open && (
        <div className="dlg-list" role="radiogroup" aria-label={label}>
          {options.map(([v, text]) => (
            <button
              key={v}
              type="button"
              role="radio"
              aria-checked={value === v}
              className="dlg-list-item"
              onClick={() => {
                setOpen(false)
                if (value !== v) onPick(v)
              }}
            >
              <span>{text}</span>
              {value === v && <Icon d={paths.check} px={20} sw={2.2} />}
            </button>
          ))}
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

/** A selection row: read-only in this card. It names what the feature holds and says how many; picking is a later card. */
export function SelectionRow({ spec, items }: { spec: FieldSpec; items: readonly SelItem[] }) {
  const names = items.map((i) => (i.name ? i.name : (i.kind ?? 'item')))
  return (
    <Row label={labelOf(spec)} info={spec.tip ?? null}>
      <div className="dlg-field dlg-field-readonly" data-empty={items.length === 0 ? '' : undefined}>
        <span className="dlg-sel-names">{items.length === 0 ? 'Nothing selected' : names.join(', ')}</span>
        {items.length > 0 && <span className="dlg-sel-count">{items.length === 1 ? '1 item' : `${items.length} items`}</span>}
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
