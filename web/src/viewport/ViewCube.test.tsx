import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ViewCube } from './ViewCube'

afterEach(cleanup)

describe('ViewCube', () => {
  it('opens the standard views and Normal to, and closes after a pick', () => {
    const onView = vi.fn()
    const onNormalTo = vi.fn()
    render(<ViewCube onView={onView} onNormalTo={onNormalTo} />)
    expect(screen.queryByRole('menu')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /view cube/i }))
    const labels = screen.getAllByRole('menuitem').map((b) => b.textContent)
    expect(labels).toEqual(['Isometric', 'Top', 'Bottom', 'Front', 'Back', 'Left', 'Right', 'Normal to'])
    fireEvent.click(screen.getByRole('menuitem', { name: 'Front' }))
    expect(onView).toHaveBeenCalledWith('front')
    expect(screen.queryByRole('menu')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /view cube/i }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Normal to' }))
    expect(onNormalTo).toHaveBeenCalledTimes(1)
  })

  it('closes when the backdrop is tapped', () => {
    render(<ViewCube onView={vi.fn()} onNormalTo={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /view cube/i }))
    fireEvent.click(screen.getByTestId('view-cube-backdrop'))
    expect(screen.queryByRole('menu')).toBeNull()
  })
})
