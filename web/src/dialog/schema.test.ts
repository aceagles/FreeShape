import { expect, it } from 'vitest'
import { fixture } from '../test/fixtures'
import { tintFeature } from '../viewport/previewTint'
import type { FieldSpec } from './dialogApi'
import { isSegmented, labelOf, numberText, visibleRows } from './schema'

const spec = (o: Partial<FieldSpec>): FieldSpec => ({ key: 'k', kind: 'num', standard: true, visible: true, ...o })

it('names an ops row that has no label, and writes a plain number with its unit', () => {
  expect(labelOf(spec({ kind: 'ops', key: 'op' }))).toBe('Result')
  expect(labelOf(spec({ key: 'draft_angle' }))).toBe('Draft angle')
  expect(numberText(spec({ unit: 'mm' }), { value: 40, text: '40' })).toBe('40 mm')
  expect(numberText(spec({ unit: 'mm' }), { value: 10, text: '#wall*2' })).toBe('#wall*2')
  expect(numberText(spec({ unit: 'mm' }), { value: 40, text: '40' }, '7')).toBe('7 mm')
})

it('puts short choices in segments and long ones in a list; Flip rides beside the number before it', () => {
  expect(isSegmented(spec({ kind: 'ops', options: [['new', 'New'], ['add', 'Add'], ['remove', 'Remove']] }))).toBe(true)
  expect(isSegmented(spec({ kind: 'choice', options: [['a', 'Blind'], ['b', 'Symmetric'], ['c', 'Through all'], ['d', 'Up to face'], ['e', 'Up to part']] }))).toBe(false)
  const rows = visibleRows([spec({ key: 'depth' }), spec({ key: 'flip', kind: 'check' }), spec({ key: 'second', kind: 'check', visible: false }), spec({ key: 'draft', kind: 'check' })])
  expect(rows.map((r) => [r.spec.key, r.flip?.key ?? null])).toEqual([['depth', 'flip'], ['draft', null]])
})

it('tints the parts a feature made and leaves the others', () => {
  const { outputs } = fixture('bracket')
  const id = outputs.parts[0]!.id
  const feature = id.split('/')[0]!
  const t = tintFeature(outputs, feature)
  expect(t.tinted).toBeGreaterThan(0)
  expect(t.outputs.parts[0]!.color).not.toEqual(outputs.parts[0]!.color)
  expect(tintFeature(outputs, 'F99')).toEqual({ outputs, tinted: 0 })
})
