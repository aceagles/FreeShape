import { expect, it } from 'vitest'
import { notDrawnText } from './notDrawn'

it('names each kind of tab the view does not draw, and has a line for a kind it does not know', () => {
  expect(notDrawnText('assembly')).toBe('Assemblies are not drawn here yet.')
  expect(notDrawnText('drawing')).toBe('Drawings are not drawn here yet.')
  expect(notDrawnText('variables')).toBe('Variable Studios are not drawn here yet.')
  expect(notDrawnText('x')).toBe('This kind of tab is not drawn here yet.')
})
