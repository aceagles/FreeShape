import { describe, expect, it } from 'vitest'
import { checkExpression, evaluateNumber, formatNumber } from './editor'

// The client's port of freeshape/expr.py (card I9.16) held to expr.py's own cases: every input and expected result below
// is carried over from the Python tests named above each block, so a difference between the two shows here. The client
// takes no value from the port (the server evaluates what it is sent); checkExpression only marks a text invalid.

const value = (text: string, vars: Record<string, number> = {}): number => {
  const r = evaluateNumber(text, vars)
  if (!r.ok) throw new Error(`${text}: ${r.error}`)
  return r.value
}
const fails = (text: string, vars: Record<string, number> = {}): string => {
  const r = evaluateNumber(text, vars)
  if (r.ok) throw new Error(`${text} gave ${r.value}`)
  return r.error
}

describe('tests/test_expr_functions.py', () => {
  it.each([
    ['atan2(1, 1)', 45], ['asin(0.5)', 30], ['acos(0)', 90], ['atan(1)', 45],
    ['floor(2.7)', 2], ['ceil(2.1)', 3], ['exp(log(5))', 5], ['log10(1000)', 3],
    ['500 um', 0.5], ['500um', 0.5], ['1 m', 1000], ['2 mm', 2], ['1m + 500 um', 1000.5],
    ['log10(1000) mm', 3],
  ] as const)('test_values: %s is %d', (text, want) => {
    expect(value(text)).toBeCloseTo(want, 9)
  })

  it.each(['asin(2)', 'acos(-2)', 'log(0)', 'log10(-1)', 'log(-3)'])('test_domain_errors: %s', (text) => {
    fails(text)
  })

  it('test_variables_with_functions', () => {
    expect(value('atan2(#h, #w)', { h: 3, w: 4 })).toBeCloseTo(36.8699, 4)
  })

  it('test_comma_decimal_outside_calls_argument_separator_inside', () => {
    expect(value('1,5 mm')).toBe(1.5)
    expect(value('max(1,5) + 1,5')).toBeCloseTo(6.5, 12)
    expect(value('(1,5)*2')).toBe(3)
  })
})

describe('tests/test_expr_limits.py', () => {
  it('test_huge_exponent_chain_fails_fast_with_a_clear_error', () => {
    const start = Date.now()
    expect(fails('9 ** 9 ** 9')).toContain('exponent')
    expect(Date.now() - start).toBeLessThan(1000)
  })
  it('test_huge_power_of_ten_fails_fast_with_a_clear_error', () => {
    fails('10 ** 100000')
  })
  it('test_small_exponent_still_evaluates', () => {
    expect(value('2 ** 10')).toBe(1024)
  })
  it('test_non_strict_mode_still_just_returns_none', () => {
    fails('9 ** 9 ** 9')
    fails('10 ** 100000')
  })
  it('test_huge_result_from_a_safe_exponent_is_also_rejected', () => {
    fails('1000 ** 300')
  })
})

describe('tests/test_core.py test_number_parser', () => {
  it('reads expressions and units', () => {
    expect(value('25/2')).toBe(12.5)
    expect(Math.abs(value('1/2 in') - 12.7)).toBeLessThan(1e-9)
    expect(value('1in + 5mm')).toBe(30.4)
    fails('nonsense')
  })
})

describe('tests/test_sketch_toggle_normal.py', () => {
  it('a radius made from a variable, doubled and halved', () => {
    expect(value('(#r)*2', { r: 7 })).toBe(14)
    expect(value('((#r)*2)/2', { r: 7 })).toBe(7)
  })
})

describe('checkExpression: the invalid style of a typed field', () => {
  it('takes what the card names: 25/2, 1 in, #wall * 2 with wall in scope', () => {
    expect(checkExpression('25/2').ok).toBe(true)
    expect(checkExpression('1 in').ok).toBe(true)
    expect(checkExpression('#wall * 2', ['wall']).ok).toBe(true)
  })
  it('marks text that is not well formed, or names a variable not in scope', () => {
    expect(checkExpression('abc').ok).toBe(false)
    expect(checkExpression('2 +* 3').ok).toBe(false)
    expect(checkExpression('(1 + 2').ok).toBe(false)
    expect(checkExpression('').ok).toBe(false)
    expect(checkExpression('#nope * 2', ['wall'])).toEqual({ ok: false, error: 'unknown variable #nope' })
  })
  it('leaves what depends on the values to the server', () => {
    expect(checkExpression('1/0').ok).toBe(true)
    expect(checkExpression('10 / (#wall - 1)', ['wall']).ok).toBe(true)
    expect(checkExpression('sqrt(-1)').ok).toBe(true)
  })
})

it('formatNumber shows a server value as the desktop does (expr.py fmt)', () => {
  expect(formatNumber(12.5)).toBe('12.5')
  expect(formatNumber(25.4)).toBe('25.4')
  expect(formatNumber(100)).toBe('100')
  expect(formatNumber(1 / 3)).toBe('0.3333')
  expect(formatNumber(-0.00001)).toBe('0')
})
