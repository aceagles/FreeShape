import type { FieldSpec, FieldValue } from './dialogApi'

/** The label of an ops row, which the schema leaves without one: the op of a feature is its Result, the kind of body Create. */
const OPS_LABELS: Record<string, string> = { op: 'Result', creation: 'Create' }

export function labelOf(spec: FieldSpec): string {
  if (spec.label !== undefined && spec.label !== '') return spec.label
  const key = spec.key ?? ''
  const known = OPS_LABELS[key]
  if (known !== undefined) return known
  const words = key.replace(/^_+/, '').replace(/_/g, ' ')
  return words === '' ? '' : words[0]!.toUpperCase() + words.slice(1)
}

const PLAIN_NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)$/

/** What a number field shows and the pad opens with: the text the person typed or the stored expression, with the unit after a plain number. */
export function numberText(spec: FieldSpec, value: FieldValue | undefined, sent?: unknown): string {
  let text: string
  if (sent !== undefined && sent !== null) text = String(sent)
  else if (value?.text !== undefined && value.text !== '') text = value.text
  else if (typeof value?.value === 'number') text = String(value.value)
  else text = ''
  const unit = spec.unit ?? ''
  return unit !== '' && PLAIN_NUMBER.test(text.trim()) ? `${text.trim()} ${unit}` : text
}

/** A choice of at most this many options with short labels is one row of segmented buttons; any other is a list. */
const SEGMENT_MAX_OPTIONS = 4
const SEGMENT_MAX_CHARS = 26

export function isSegmented(spec: FieldSpec): boolean {
  const options = spec.options ?? []
  return options.length <= SEGMENT_MAX_OPTIONS && options.reduce((n, o) => n + o[1].length, 0) <= SEGMENT_MAX_CHARS
}

/** The rows to draw, in the schema's order: hidden rows are left out, and an Extrude's Flip rides beside the number it follows (the layout's flip button). */
export interface Row {
  spec: FieldSpec
  /** The check row drawn as a flip button beside this number row. */
  flip: FieldSpec | null
}

export function visibleRows(fields: readonly FieldSpec[]): Row[] {
  const shown = fields.filter((f) => f.visible)
  const rows: Row[] = []
  for (let i = 0; i < shown.length; i++) {
    const spec = shown[i]!
    const next = shown[i + 1]
    if (spec.kind === 'num' && next?.kind === 'check' && next.key === 'flip') {
      rows.push({ spec, flip: next })
      i++
    } else rows.push({ spec, flip: null })
  }
  return rows
}
