import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { SelectionChip } from '../selection/SelectionChip'
import { SelectionStore } from '../selection/store'
import { MenuButton } from './Menu'
import { MODE_KEY, ModeProvider, useSetMode } from './mode'
import { MOUSE_STYLES, viewMenu } from './menus'

// I9.9: the Multi chip is Tablet mode's way to add to a selection (Desktop mode adds with a click), and the View menu's
// Mouse style lists the desktop's styles with only Onshape, the client's own, enabled.

beforeEach(() => window.localStorage.clear())
afterEach(() => {
  cleanup()
  window.localStorage.clear()
})

/** Two buttons that set the mode, as the top bar's toggle does. */
function Switch() {
  const set = useSetMode()
  return (
    <>
      <button type="button" onClick={() => set('desktop')}>
        to desktop
      </button>
      <button type="button" onClick={() => set('tablet')}>
        to tablet
      </button>
    </>
  )
}
const switchTo = (m: 'desktop' | 'tablet') => fireEvent.click(screen.getByRole('button', { name: `to ${m}` }))

describe('the Multi chip', () => {
  it('shows only in Tablet mode', () => {
    window.localStorage.setItem(MODE_KEY, 'tablet')
    render(
      <ModeProvider>
        <Switch />
        <SelectionChip store={new SelectionStore()} />
      </ModeProvider>,
    )
    expect(screen.getByTestId('selection-multi')).toBeTruthy()
    act(() => switchTo('desktop'))
    expect(screen.queryByTestId('selection-multi')).toBeNull()
    // the count and Clear stay
    expect(screen.getByTestId('selection-count').textContent).toBe('Nothing selected')
    expect(screen.getByTestId('selection-clear')).toBeTruthy()
    act(() => switchTo('tablet'))
    expect(screen.getByTestId('selection-multi')).toBeTruthy()
  })
})

describe('View > Mouse style', () => {
  it("lists the desktop's styles in its order, Onshape checked and the only one enabled", () => {
    render(<MenuButton label="View" entries={viewMenu} commands={{}} state={{ readOnly: false, tabKind: 'studio' }} />)
    fireEvent.click(screen.getByRole('button', { name: 'View' }))
    const item = screen.getByRole('menuitem', { name: /Mouse style/ })
    expect((item as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByRole('menuitemcheckbox', { name: 'Onshape' })).toBeNull()
    fireEvent.click(item)
    expect(item.getAttribute('aria-expanded')).toBe('true')
    const rows = [...document.querySelectorAll<HTMLButtonElement>('[data-command^="view.mouse."]')]
    expect(rows.map((b) => b.textContent)).toEqual(MOUSE_STYLES.map(([, label]) => label))
    expect(MOUSE_STYLES.map(([id]) => id)).toEqual(['onshape', 'touchpad', 'cad', 'gesture', 'blender', 'solidworks', 'openinventor', 'revit', 'openscad'])
    expect(rows.filter((b) => !b.disabled).map((b) => b.textContent)).toEqual(['Onshape'])
    expect(rows.filter((b) => b.getAttribute('aria-checked') === 'true').map((b) => b.textContent)).toEqual(['Onshape'])
    // choosing Onshape closes the menu and changes nothing
    fireEvent.click(rows[0]!)
    expect(screen.queryByRole('menu')).toBeNull()
  })
})
