import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from 'react'
import { checkExpression } from '../pad/editor'
import './dialog.css'

/*
 * Desktop mode's typed fields (card I9.16). In Tablet mode a number is edited on the pad and nothing here is used.
 *
 * Keys inside a focused field are the field's own and stop at it, so whatever listens on the page (input/keys.ts, the
 * Desktop bindings) never sees them: Enter commits the field and then accepts, Esc cancels, Tab and Shift+Tab move to the
 * next and the previous field of the same group (an element marked `data-dlg-fields`; each field in it is marked
 * `data-dlg-field`). Without a group Tab is left to the browser.
 */

/** The fields of the group `from` is in, in document order. */
function groupFields(from: HTMLElement): HTMLElement[] {
  const group = from.closest('[data-dlg-fields]')
  if (group === null) return []
  return [...group.querySelectorAll<HTMLElement>('[data-dlg-field]')].filter((el) => !(el as HTMLInputElement).disabled)
}

/** Moves the focus to the next (or previous) field of `from`'s group, round to the first; false when there is no group. */
export function moveToField(from: HTMLElement, back: boolean): boolean {
  const all = groupFields(from)
  if (all.length === 0) return false
  const at = all.indexOf(from)
  const field = all[(at + (back ? -1 : 1) + all.length) % all.length]
  // a field may be a wrapper (the dropdown): the focus goes to its button
  const next = field?.matches('input, button, select, textarea') ? field : field?.querySelector<HTMLElement>('button, input')
  next?.focus()
  if (next instanceof HTMLInputElement && next.type === 'text') next.select()
  return true
}

/** What a field does with Enter, Esc and Tab. `commit` runs first and returns false when the field cannot take its text. */
export interface FieldKeys {
  commit?: () => boolean
  onEnter?: () => void
  onEscape?: () => void
}

/**
 * The key handler of a typed field (a number, a text, a dropdown's button, a checkbox): Enter, Esc and Tab are handled
 * here and stopped; any other key is left alone. Returns true when it took the key.
 */
export function fieldKeyDown(e: ReactKeyboardEvent<HTMLElement>, keys: FieldKeys): boolean {
  if (e.nativeEvent.isComposing || e.altKey || e.ctrlKey || e.metaKey) return false
  const stop = () => {
    e.preventDefault()
    e.stopPropagation()
  }
  if (e.key === 'Enter') {
    stop()
    if (keys.commit && !keys.commit()) return true // an invalid text: nothing is sent and the dialog stays
    keys.onEnter?.()
    return true
  }
  if (e.key === 'Escape') {
    stop()
    keys.onEscape?.()
    return true
  }
  if (e.key === 'Tab') {
    keys.commit?.()
    if (moveToField(e.currentTarget, e.shiftKey)) stop()
    return true
  }
  return false
}

export interface InlineNumberProps {
  /** The text shown while the field is not being typed in, and the text typing starts from: "12.5", "#wall * 2". */
  value: string
  /** The field's accessible name (its row label). */
  label: string
  /** Variables in scope for #name, by name (without "#"): a name not among them marks the text invalid. */
  variables?: readonly string[]
  /** The unit: the field's placeholder and, unless `unitLabel` is false, a 24 px label after the field. */
  unit?: string | null
  unitLabel?: boolean
  /** Marked invalid from outside: the server refused the value (a min, a zero, a division by zero). */
  invalid?: boolean
  /** A changed, valid text, on Enter, Tab or leaving the field (unless `commitOnBlur` is false). The text as typed: the server evaluates it as it does the pad's. */
  onCommit: (text: string) => void
  /** Enter, after the commit: the dialog's Accept. Not called while the text is invalid. */
  onEnter?: () => void
  /** Esc: the typed text is dropped, then this runs (the dialog's Cancel). */
  onEscape?: () => void
  /** The field lost the focus (after the commit, if any). */
  onBlur?: () => void
  /** Leaving the field commits (default true). The sketch's dimension box sets false: leaving it gives the value up. */
  commitOnBlur?: boolean
  /** A pen or touch press: called instead of focusing the field, to open the number pad. Absent: every pointer focuses it. */
  onPad?: () => void
  /** Focus the field on mount with its text selected (the dimension box). */
  autoFocus?: boolean
  /** Width of the field in px (default 110, the desktop's). */
  width?: number
  /** The field's key in its dialog (data-field-key), so a caller can focus it. */
  fieldKey?: string
  testId?: string
  className?: string
}

/**
 * One typed number field (Desktop mode): a native text input, right aligned, 110 px wide, with the unit as placeholder
 * and after it, as the desktop's FeatureDialog row (freeshape/ui/dialogs.py `_make_row`). Units, expressions and
 * variables are typed as on the pad and sent as the pad sends them, as text the server evaluates; a text that is not well
 * formed or names a variable not in scope (pad/editor.ts `checkExpression`) is marked invalid as the desktop marks it,
 * and nothing is sent. What the value is, and whether it is in range, the server says. A mouse press focuses it; a pen or touch press opens the pad
 * instead (`onPad`). The mate dialog and the file and canvas dialogs reuse it.
 */
export function InlineNumber({
  value,
  label,
  variables = [],
  unit = null,
  unitLabel = true,
  invalid = false,
  onCommit,
  onEnter,
  onEscape,
  onBlur,
  commitOnBlur = true,
  onPad,
  autoFocus = false,
  width = 110,
  fieldKey,
  testId = 'inline-number',
  className,
}: InlineNumberProps) {
  // the text being typed, or null while the field shows `value`
  const [draft, setDraft] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  // a pen or touch press opens the pad: the focus that follows it is given back
  const padPress = useRef(false)
  const latest = useRef({ draft, value, onCommit, variables })
  useLayoutEffect(() => {
    latest.current = { draft, value, onCommit, variables }
  })

  useEffect(() => {
    if (!autoFocus) return
    const el = input.current
    if (el === null) return
    el.focus()
    el.select()
  }, [autoFocus])

  const reading = draft === null ? null : checkExpression(draft, variables)
  const bad = reading !== null ? !reading.ok && draft!.trim() !== '' : invalid
  const empty = draft !== null && draft.trim() === ''

  /** Sends a changed, valid draft; false when the draft is invalid (it is kept, marked). */
  const commit = (): boolean => {
    const { draft: d, value: v, onCommit: send, variables: vars } = latest.current
    if (d === null) return true
    const t = d.trim()
    if (t === '' || t === v.trim()) {
      setDraft(null)
      return true
    }
    if (!checkExpression(t, vars).ok) return false
    setDraft(null)
    latest.current.draft = null
    send(t)
    return true
  }

  const onPointerDown = (e: ReactPointerEvent<HTMLInputElement>) => {
    padPress.current = false
    const type: string = e.pointerType
    if (onPad === undefined || (type !== 'pen' && type !== 'touch')) return
    // a pen or a finger: the pad, never the iPad's keyboard or handwriting
    padPress.current = true
    e.preventDefault()
    onPad()
  }

  return (
    <span className={`inline-number${className ? ` ${className}` : ''}`} data-testid={testId}>
      <input
        ref={input}
        type="text"
        className="inline-number-field"
        style={{ width }}
        aria-label={label}
        aria-invalid={bad ? true : undefined}
        data-invalid={bad ? '' : undefined}
        data-dlg-field=""
        data-field-key={fieldKey}
        placeholder={unit ?? ''}
        value={draft ?? value}
        autoComplete="off"
        spellCheck={false}
        onPointerDown={onPointerDown}
        onMouseDown={(e) => {
          if (padPress.current) e.preventDefault()
        }}
        onFocus={(e) => {
          if (padPress.current) {
            padPress.current = false
            e.currentTarget.blur()
            return
          }
          if (draft === null) setDraft(value)
        }}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          if (commitOnBlur && !empty) commit()
          setDraft(null)
          latest.current.draft = null
          onBlur?.()
        }}
        onKeyDown={(e) =>
          fieldKeyDown(e, {
            commit,
            onEnter,
            onEscape: () => {
              setDraft(null)
              latest.current.draft = null
              onEscape?.()
            },
          })
        }
      />
      {unit && unitLabel && (
        <span className="inline-number-unit" aria-hidden="true">
          {unit}
        </span>
      )}
    </span>
  )
}
