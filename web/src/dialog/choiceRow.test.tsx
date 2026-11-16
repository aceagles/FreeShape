import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { ChoiceRow } from './fields'
import type { FieldSpec } from './dialogApi'

afterEach(cleanup)

const fillet: FieldSpec = {
  key: 'fillet_type',
  kind: 'choice',
  standard: true,
  visible: true,
  label: 'Type',
  options: [
    ['constant', 'Constant radius'],
    ['variable', 'Variable radius'],
  ],
}

it('a collapsed choice row shows its current label, and the first option when the value is not an option', () => {
  const { rerender } = render(<ChoiceRow spec={fillet} value="variable" error={null} onPick={() => {}} />)
  expect(screen.getByRole('button', { name: 'Type, Variable radius' }).textContent).toContain('Variable radius')
  rerender(<ChoiceRow spec={fillet} value={null} error={null} onPick={() => {}} />)
  expect(screen.getByRole('button', { name: 'Type, Constant radius' }).textContent).toContain('Constant radius')
  rerender(<ChoiceRow spec={fillet} value="bogus" error={null} onPick={() => {}} />)
  expect(screen.getByRole('button', { name: 'Type, Constant radius' }).textContent).toContain('Constant radius')
})
