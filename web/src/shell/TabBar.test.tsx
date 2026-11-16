import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LONG_PRESS_MS } from './longPress'
import { TabBar } from './TabBar'
import { tab } from '../test/fakes'

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const tabs = [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'assembly', 'Assembly 1')]

function setup(readOnly = false) {
  const fns = { onSelect: vi.fn(), onAdd: vi.fn(), onRename: vi.fn(), onDelete: vi.fn() }
  render(<TabBar tabs={tabs} currentTab="T1" readOnly={readOnly} {...fns} />)
  return fns
}

const press = (el: Element, ms: number) => {
  fireEvent.pointerDown(el, { pointerId: 1, pointerType: 'touch', button: 0, clientX: 10, clientY: 10 })
  act(() => {
    vi.advanceTimersByTime(ms)
  })
  fireEvent.pointerUp(el, { pointerId: 1, pointerType: 'touch', button: 0, clientX: 10, clientY: 10 })
  fireEvent.click(el)
}

it('a long press of 500 ms opens the tab menu and does not switch tab', () => {
  const fns = setup()
  const second = screen.getByRole('button', { name: 'Assembly 1' })
  press(second, LONG_PRESS_MS)
  expect(LONG_PRESS_MS).toBe(500)
  expect(screen.getByRole('menu', { name: 'Assembly 1 menu' })).toBeTruthy()
  expect(fns.onSelect).not.toHaveBeenCalled()
})

it('a shorter press switches to the tab and opens no menu', () => {
  const fns = setup()
  press(screen.getByRole('button', { name: 'Assembly 1' }), LONG_PRESS_MS - 1)
  expect(fns.onSelect).toHaveBeenCalledWith('T2')
  expect(screen.queryByRole('menu')).toBeNull()
  // and the timer is gone: waiting on does not open the menu afterwards
  act(() => {
    vi.advanceTimersByTime(1000)
  })
  expect(screen.queryByRole('menu')).toBeNull()
})

it('a press that moves away before 500 ms is a drag, not a long press', () => {
  setup()
  const el = screen.getByRole('button', { name: 'Part Studio 1' })
  fireEvent.pointerDown(el, { pointerId: 1, pointerType: 'touch', button: 0, clientX: 10, clientY: 10 })
  fireEvent.pointerMove(el, { pointerId: 1, pointerType: 'touch', clientX: 60, clientY: 10 })
  act(() => {
    vi.advanceTimersByTime(600)
  })
  expect(screen.queryByRole('menu')).toBeNull()
})

it('the menu renames and deletes the tab it was opened on, and a press elsewhere closes it', () => {
  const fns = setup()
  press(screen.getByRole('button', { name: 'Assembly 1' }), 500)
  act(() => screen.getByRole('menuitem', { name: 'Rename' }).click())
  expect(fns.onRename).toHaveBeenCalledWith(tabs[1])
  expect(screen.queryByRole('menu')).toBeNull()
  press(screen.getByRole('button', { name: 'Part Studio 1' }), 500)
  act(() => screen.getByRole('menuitem', { name: 'Delete' }).click())
  expect(fns.onDelete).toHaveBeenCalledWith(tabs[0])
  press(screen.getByRole('button', { name: 'Part Studio 1' }), 500)
  fireEvent.pointerDown(screen.getByTestId('popover-scrim'))
  expect(screen.queryByRole('menu')).toBeNull()
})

it('the tab menu items are off while the Document is read-only', () => {
  setup(true)
  press(screen.getByRole('button', { name: 'Part Studio 1' }), 500)
  expect((screen.getByRole('menuitem', { name: 'Rename' }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('menuitem', { name: 'Delete' }) as HTMLButtonElement).disabled).toBe(true)
})

it('add tab offers a Part Studio and an Assembly', () => {
  const fns = setup()
  act(() => screen.getByRole('button', { name: 'Add tab' }).click())
  act(() => screen.getByRole('menuitem', { name: 'Assembly' }).click())
  expect(fns.onAdd).toHaveBeenCalledWith('assembly')
  act(() => screen.getByRole('button', { name: 'Add tab' }).click())
  act(() => screen.getByRole('menuitem', { name: 'Part Studio' }).click())
  expect(fns.onAdd).toHaveBeenCalledWith('studio')
})
