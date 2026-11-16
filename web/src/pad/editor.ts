/**
 * The text of a number field being edited on the pad, as plain data and pure functions. The pad's keys and a hardware
 * keyboard both reduce to the same actions. Nothing here touches the DOM.
 */

export const UNITS = ['mm', 'deg', 'in'] as const

export interface PadText {
  text: string
  /** Caret position, 0..text.length. */
  caret: number
  /** True until the first edit: the opening value is "selected", so a digit, a variable or a bracket replaces it. */
  fresh: boolean
}

export type PadAction =
  | { kind: 'insert'; text: string }
  | { kind: 'variable'; name: string }
  | { kind: 'unit'; unit: string }
  | { kind: 'sign' }
  | { kind: 'backspace' }
  | { kind: 'move'; to: 'left' | 'right' | 'home' | 'end' }

export function openText(value: string): PadText {
  return { text: value, caret: value.length, fresh: value !== '' }
}

/** What typing `ch` into a fresh field does: operators and units carry on from the opening value, anything else replaces it. */
const CONTINUES = /^[+\-*/^]$/

const UNIT_TAIL = /^(.*?)\s*(?<![A-Za-z_#])(mm|deg|in)$/

export function reduce(s: PadText, a: PadAction): PadText {
  switch (a.kind) {
    case 'insert': {
      const base = s.fresh && !CONTINUES.test(a.text) ? { text: '', caret: 0 } : s
      return put(base, a.text)
    }
    case 'variable': {
      const base = s.fresh ? { text: '', caret: 0 } : s
      return put(base, `#${a.name}`)
    }
    case 'unit': {
      // a unit closes the number: it replaces a unit already at the end
      const m = UNIT_TAIL.exec(s.text)
      const body = (m !== null && !m[1]!.endsWith('#') ? m[1]! : s.text).trimEnd()
      const next = body === '' ? a.unit : `${body} ${a.unit}`
      return { text: next, caret: next.length, fresh: false }
    }
    case 'sign': {
      const t = s.text
      let next: string
      if (t === '') next = '-'
      else if (t.startsWith('-(') && t.endsWith(')')) next = t.slice(2, -1)
      else if (t.startsWith('-')) next = t.slice(1)
      else if (/[+\-*/^()]/.test(t)) next = `-(${t})`
      else next = `-${t}`
      return { text: next, caret: next.length, fresh: false }
    }
    case 'backspace': {
      if (s.fresh) return { text: '', caret: 0, fresh: false }
      if (s.caret === 0) return s
      return { text: s.text.slice(0, s.caret - 1) + s.text.slice(s.caret), caret: s.caret - 1, fresh: false }
    }
    case 'move': {
      const caret = a.to === 'left' ? Math.max(0, s.caret - 1) : a.to === 'right' ? Math.min(s.text.length, s.caret + 1) : a.to === 'home' ? 0 : s.text.length
      return { ...s, caret, fresh: false }
    }
  }
}

function put(s: { text: string; caret: number }, ins: string): PadText {
  return { text: s.text.slice(0, s.caret) + ins + s.text.slice(s.caret), caret: s.caret + ins.length, fresh: false }
}

/** The action a hardware key stands for, or null when the pad ignores the key (Enter and Escape are the caller's). */
export function actionForKey(e: { key: string; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean }): PadAction | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null
  switch (e.key) {
    case 'Backspace':
      return { kind: 'backspace' }
    case 'ArrowLeft':
      return { kind: 'move', to: 'left' }
    case 'ArrowRight':
      return { kind: 'move', to: 'right' }
    case 'Home':
      return { kind: 'move', to: 'home' }
    case 'End':
      return { kind: 'move', to: 'end' }
  }
  if (e.key.length === 1 && /[0-9A-Za-z_.,+\-*/^()# ]/.test(e.key)) return { kind: 'insert', text: e.key === ',' ? '.' : e.key }
  return null
}
