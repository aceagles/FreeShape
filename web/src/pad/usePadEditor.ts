import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { suspendKeys } from '../input/keys'
import { actionForKey, openText, reduce } from './editor'
import type { PadAction, PadText } from './editor'

/** What opens the pad on one number field; the shape the dialogs of I5.6, dimensions and later number entry share. */
export interface PadOptions {
  /** The field's text when the pad opens, for example "25 mm". Cancel gives it back. */
  value: string
  /** The variable names in scope for the # key, without the "#". */
  variables?: readonly string[]
  /** Enter: called once with the field's text. The caller evaluates it (the server owns the expression syntax). */
  onCommit: (text: string) => void
  /** Cancel (or Escape): called once; the field text is back to `value`. */
  onCancel: () => void
  /** Called with the text after every edit, so a dialog can show it live. */
  onChange?: (text: string) => void
  /** A key pressed while the opening value is untouched; true when it took the key (a new dimension's pad hands a tool key on, I9.7). */
  onUntouchedKey?: (e: KeyboardEvent) => boolean
  /** False pauses the hardware keyboard listener (default true while mounted). */
  active?: boolean
}

export interface PadEditor {
  text: string
  caret: number
  /** True while the opening value is still "selected" (nothing typed yet). */
  fresh: boolean
  variables: readonly string[]
  variablesOpen: boolean
  apply: (a: PadAction) => void
  toggleVariables: () => void
  pickVariable: (name: string) => void
  commit: () => void
  cancel: () => void
}

/**
 * State and behaviour of one pad session. The hardware keyboard types into the same field through a window key listener
 * (there is no input element to type into). Render the field with <PadField editor> and the keys with <NumberPad editor>.
 */
export function usePadEditor(opts: PadOptions): PadEditor {
  const [state, setState] = useState<PadText>(() => openText(opts.value))
  const [variablesOpen, setVariablesOpen] = useState(false)
  // the newest options and text, for handlers that must not go stale (a key listener, two taps in one frame)
  const latest = useRef({ opts, state })
  useLayoutEffect(() => {
    latest.current.opts = opts
  })
  const done = useRef(false)

  const set = useCallback((next: PadText) => {
    latest.current.state = next
    setState(next)
    latest.current.opts.onChange?.(next.text)
  }, [])
  const apply = useCallback(
    (a: PadAction) => {
      done.current = false
      set(reduce(latest.current.state, a))
    },
    [set],
  )
  const commit = useCallback(() => {
    const { opts: o, state: s } = latest.current
    if (done.current || s.text.trim() === '') return
    done.current = true
    o.onCommit(s.text)
  }, [])
  const cancel = useCallback(() => {
    if (done.current) return
    done.current = true
    const { opts: o } = latest.current
    set(openText(o.value))
    o.onCancel()
  }, [set])
  const pickVariable = useCallback(
    (name: string) => {
      apply({ kind: 'variable', name })
      setVariablesOpen(false)
    },
    [apply],
  )
  const toggleVariables = useCallback(() => setVariablesOpen((v) => !v), [])

  const active = opts.active ?? true
  useEffect(() => {
    if (!active) return
    const resume = suspendKeys() // the pad reads the keys itself while it is on
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return
      if (e.key === 'Enter') commit()
      else if (e.key === 'Escape') cancel()
      else if (latest.current.state.fresh && latest.current.opts.onUntouchedKey?.(e) === true) {
        // handed on: the pad's session is over
      } else {
        const a = actionForKey(e)
        if (a === null) return
        apply(a)
      }
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      resume()
    }
  }, [active, apply, commit, cancel])

  return { text: state.text, caret: state.caret, fresh: state.fresh, variables: opts.variables ?? [], variablesOpen, apply, toggleVariables, pickVariable, commit, cancel }
}
