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

/*
 * The number a field's text stands for, worked out on the client exactly as the server works it out
 * (freeshape/expr.py `parse_number`, which the pad's text goes to): "25/2", "1 in", "3*4 mm", "#wall * 2 + 1". The server
 * stays the judge of what is stored; an inline field (Desktop mode) uses this to mark a text it cannot take before
 * sending anything, as the desktop's dialog marks it.
 */

/** Unit factors to mm (or to degrees), as expr.py's _UNITS. */
const UNIT_FACTORS: Record<string, number> = { mm: 1, cm: 10, m: 1000, in: 25.4, inch: 25.4, '"': 25.4, ft: 304.8, deg: 1, '°': 1, rad: 180 / Math.PI, um: 0.001 }
const escapeRe = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const UNITS_ALT = Object.keys(UNIT_FACTORS)
  .sort((a, b) => b.length - a.length)
  .map(escapeRe)
  .join('|')
const TRAILING_UNIT = new RegExp(`^(.*?)\\s*(${UNITS_ALT})$`, 's')
const DIGIT_UNIT = new RegExp(`\\d\\s*(${UNITS_ALT})(?![A-Za-z])`)
const NUMBER_UNIT = new RegExp(`(\\d+(?:\\.\\d*)?|\\.\\d+)\\s*(${UNITS_ALT})(?![A-Za-z])`, 'g')
const VARIABLE = /#([A-Za-z_][A-Za-z0-9_]*)/g
const MAX_EXPONENT = 1000
const MAX_MAGNITUDE = 1e12

/** Why a text is not a number: `runtime` errors depend on the values (a division by zero), the others on the text alone. */
class ExprError extends Error {
  constructor(
    message: string,
    readonly runtime = false,
  ) {
    super(message)
  }
}

const cap = (v: number): number => {
  if (Number.isNaN(v) || Math.abs(v) > MAX_MAGNITUDE) throw new ExprError(`result is too large (over ${MAX_MAGNITUDE})`, true)
  return v
}
const RAD = Math.PI / 180
const domain = (name: string, fn: (x: number) => number, ok: (x: number) => boolean) => (x: number) => {
  if (!ok(x)) throw new ExprError(`${name}() is not defined for ${x}`, true)
  return fn(x)
}
/** Python's round(): halves go to the even neighbour. */
function pyRound(x: number, digits = 0): number {
  const m = 10 ** digits
  const y = x * m
  const r = Math.round(y)
  const half = Math.abs(y % 1) === 0.5
  return (half && r % 2 !== 0 ? r - 1 : r) / m
}
/** expr.py's _FUNCS, each with the number of arguments Python accepts. */
const FUNCS: Record<string, { arity: [number, number]; fn: (...a: number[]) => number }> = {
  sqrt: { arity: [1, 1], fn: domain('sqrt', Math.sqrt, (x) => x >= 0) },
  sin: { arity: [1, 1], fn: (d) => Math.sin(d * RAD) },
  cos: { arity: [1, 1], fn: (d) => Math.cos(d * RAD) },
  tan: { arity: [1, 1], fn: (d) => Math.tan(d * RAD) },
  abs: { arity: [1, 1], fn: Math.abs },
  min: { arity: [2, Infinity], fn: Math.min },
  max: { arity: [2, Infinity], fn: Math.max },
  round: { arity: [1, 2], fn: (x, n) => pyRound(x, n === undefined ? 0 : Math.trunc(n)) },
  asin: { arity: [1, 1], fn: domain('asin', (x) => Math.asin(x) / RAD, (x) => x >= -1 && x <= 1) },
  acos: { arity: [1, 1], fn: domain('acos', (x) => Math.acos(x) / RAD, (x) => x >= -1 && x <= 1) },
  atan: { arity: [1, 1], fn: (x) => Math.atan(x) / RAD },
  atan2: { arity: [2, 2], fn: (y, x) => Math.atan2(y, x) / RAD },
  floor: { arity: [1, 1], fn: Math.floor },
  ceil: { arity: [1, 1], fn: Math.ceil },
  log: { arity: [1, 1], fn: domain('log', Math.log, (x) => x > 0) },
  log10: { arity: [1, 1], fn: domain('log10', Math.log10, (x) => x > 0) },
  exp: { arity: [1, 1], fn: Math.exp },
}
const CONSTS: Record<string, number> = { pi: Math.PI, e: Math.E }

type Token = { t: 'num'; v: number } | { t: 'name'; v: string } | { t: 'op'; v: string }

function tokens(s: string): Token[] {
  const out: Token[] = []
  const re = /\s*(?:((?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)|([A-Za-z_]\w*)|(\*\*|\/\/|[-+*/%(),]))/y
  let i = 0
  while (i < s.length) {
    if (/^\s*$/.test(s.slice(i))) break
    re.lastIndex = i
    const m = re.exec(s)
    if (m === null) throw new ExprError('bad expression')
    i = re.lastIndex
    if (m[1] !== undefined) {
      // Python refuses a whole number with leading zeros ("07")
      if (/^0\d/.test(m[1]) && /^\d+$/.test(m[1]) && /[1-9]/.test(m[1])) throw new ExprError('bad expression')
      out.push({ t: 'num', v: Number(m[1]) })
    } else if (m[2] !== undefined) out.push({ t: 'name', v: m[2] })
    else out.push({ t: 'op', v: m[3]! })
  }
  return out
}

/** A Python expression of numbers, + - * / % **, brackets, pi, e and the functions above (expr.py `_eval` on `ast.parse`). */
function evalPython(s: string): number {
  const ts = tokens(s)
  let i = 0
  const peek = (v: string): boolean => ts[i]?.t === 'op' && ts[i]!.v === v
  const take = (v: string): boolean => (peek(v) ? (i++, true) : false)
  const expr = (): number => {
    let v = term()
    for (;;) {
      if (take('+')) v = cap(v + term())
      else if (take('-')) v = cap(v - term())
      else return v
    }
  }
  const term = (): number => {
    let v = unary()
    for (;;) {
      if (take('*')) v = cap(v * unary())
      else if (take('/')) {
        const d = unary()
        if (d === 0) throw new ExprError('division by zero', true)
        v = cap(v / d)
      } else if (take('%')) {
        const d = unary()
        if (d === 0) throw new ExprError('division by zero', true)
        v = cap(v - d * Math.floor(v / d))
      } else if (peek('//')) throw new ExprError('bad expression')
      else return v
    }
  }
  const unary = (): number => {
    if (take('-')) return cap(-unary())
    if (take('+')) return cap(unary())
    return power()
  }
  const power = (): number => {
    const base = atom()
    if (!take('**')) return base
    const exp = unary()
    if (Math.abs(exp) > MAX_EXPONENT) throw new ExprError(`exponent is too large (over ${MAX_EXPONENT})`, true)
    if (base === 0 && exp < 0) throw new ExprError('division by zero', true)
    return cap(base ** exp)
  }
  const atom = (): number => {
    const tok = ts[i++]
    if (tok === undefined) throw new ExprError('bad expression')
    if (tok.t === 'num') return cap(tok.v)
    if (tok.t === 'op') {
      if (tok.v !== '(') throw new ExprError('bad expression')
      const v = expr()
      if (!take(')')) throw new ExprError('bad expression')
      return v
    }
    if (take('(')) {
      const args: number[] = []
      if (!take(')')) {
        do args.push(expr())
        while (take(','))
        if (!take(')')) throw new ExprError('bad expression')
      }
      const f = FUNCS[tok.v]
      if (f === undefined || args.length < f.arity[0] || args.length > f.arity[1]) throw new ExprError('bad expression')
      return cap(f.fn(...args))
    }
    const c = CONSTS[tok.v]
    if (c === undefined) throw new ExprError('bad expression')
    return c
  }
  const v = expr()
  if (i !== ts.length) throw new ExprError('bad expression')
  return v
}

/** expr.py `_split_terms`: the text split at + and - outside brackets (not an exponent's sign). */
function splitTerms(t: string): Array<[number, string]> {
  const out: Array<[number, string]> = []
  let depth = 0
  let start = 0
  let sign = 1
  for (let i = 0; i < t.length; i++) {
    const ch = t[i]!
    if (ch === '(') depth++
    else if (ch === ')') depth--
    else if ((ch === '+' || ch === '-') && depth === 0) {
      const prev = t.slice(0, i).trimEnd()
      if (prev !== '' && !'*/(+-eE'.includes(prev[prev.length - 1]!)) {
        out.push([sign, t.slice(start, i)])
        sign = ch === '+' ? 1 : -1
        start = i + 1
      }
    }
  }
  out.push([sign, t.slice(start)])
  if (out.length <= 1) return [[1, t]]
  return out.map(([sg, term]): [number, string] => [sg, term.trim()]).filter(([, term]) => term !== '')
}

/** expr.py `_decimal_commas`: a comma is a decimal point except between a function's brackets. */
function decimalCommas(t: string): string {
  const stack: boolean[] = []
  let out = ''
  for (let i = 0; i < t.length; i++) {
    const ch = t[i]!
    if (ch === '(') {
      const prev = t.slice(0, i).trimEnd()
      stack.push(prev !== '' && /[A-Za-z0-9_]/.test(prev[prev.length - 1]!))
    } else if (ch === ')' && stack.length > 0) stack.pop()
    out += ch === ',' && !stack.some(Boolean) ? '.' : ch
  }
  return out
}

function parseExpr(text: string, variables: (name: string) => number | undefined): number {
  if (text.trim() === '') throw new ExprError('enter a value or expression')
  let t = decimalCommas(text.trim()).replace(/×/g, '*')
  if (t.includes('#')) {
    const missing: string[] = []
    t = t.replace(VARIABLE, (_m, name: string) => {
      const v = variables(name)
      if (v === undefined) {
        missing.push(name)
        return '0'
      }
      return `(${v})`
    })
    if (missing.length > 0) throw new ExprError(`unknown variable #${missing[0]}`)
  }
  const terms = splitTerms(t)
  if (terms.length > 1) return terms.reduce((sum, [sign, term]) => sum + sign * parseExpr(term, variables), 0)
  const m = TRAILING_UNIT.exec(t)
  if (m !== null && m[1] !== '' && !DIGIT_UNIT.test(m[1]!)) return parseExpr(m[1]!, variables) * UNIT_FACTORS[m[2]!]!
  return evalPython(t.replace(NUMBER_UNIT, (_m, num: string, unit: string) => `(${num}*${UNIT_FACTORS[unit]})`))
}

/** The outcome of reading a text with the port: the number it stands for, or why there is none. */
export type NumberReading = { ok: true; value: number } | { ok: false; error: string; runtime: boolean }

/**
 * The port of expr.py `parse_number` with the variables' values: what the text stands for, in mm (or degrees). It exists
 * for `checkExpression` and for the test that holds it to expr.py's own cases (editor.expr.test.ts); the client takes no
 * value from it: the server evaluates every text it is sent, and that value is the one shown.
 */
export function evaluateNumber(text: string, variables: Readonly<Record<string, number>> = {}): NumberReading {
  try {
    const v = parseExpr(text, (name) => variables[name])
    if (!Number.isFinite(v)) return { ok: false, error: 'the value is not a finite number', runtime: true }
    return { ok: true, value: v }
  } catch (e) {
    if (e instanceof ExprError) return { ok: false, error: e.message, runtime: e.runtime }
    throw e
  }
}

/**
 * Whether a number field's text is well formed and names only variables in scope (`variables`: names without "#"): the
 * invalid style of a typed field (card I9.16). What depends on the values (a division by zero, a domain, a size cap, a
 * field's min) is left to the server, which answers with the field's message.
 */
export function checkExpression(text: string, variables: readonly string[] = []): { ok: true } | { ok: false; error: string } {
  if (text.trim() === '') return { ok: false, error: 'enter a value or expression' }
  // every name in scope stands in as 1: only the form and the names are judged here
  const r = evaluateNumber(text, Object.fromEntries(variables.map((n) => [n, 1])))
  return r.ok || r.runtime ? { ok: true } : { ok: false, error: r.error }
}

/** A number as the desktop shows it in a field (expr.py `fmt`): four decimals at most, trailing zeros dropped. */
export function formatNumber(v: number, digits = 4): string {
  const s = v.toFixed(digits).replace(/0+$/, '').replace(/\.$/, '')
  return s === '-0' || s === '' ? '0' : s
}
