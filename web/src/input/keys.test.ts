import { afterEach, describe, expect, it, vi } from 'vitest'
import { attachKeys, chordOf, keymap, keymapFor, suspendKeys } from './keys'
import { BINDINGS, chordFor } from './bindings'
import { MENU_BAR } from '../shell/menus'
import type { MenuItem } from '../shell/menus'
import { keyOf } from '../test/keys'

const press = (init: KeyboardEventInit, target: EventTarget = window): KeyboardEvent => {
  const e = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init })
  target.dispatchEvent(e)
  return e
}

let off: (() => void) | null = null
afterEach(() => off?.())

describe('the keymap', () => {
  it('names a chord with its modifiers in a fixed order', () => {
    expect(chordOf({ key: 'z', ctrlKey: true, altKey: false, shiftKey: false, metaKey: false })).toBe('Ctrl+Z')
    expect(chordOf({ key: 'Delete', ctrlKey: false, altKey: false, shiftKey: true, metaKey: false })).toBe('Shift+Delete')
    expect(chordOf({ key: 'Escape', ctrlKey: false, altKey: false, shiftKey: false, metaKey: false })).toBe('Escape')
  })

  it('runs the command a chord names, takes the key, and lets a later keymap entry add a chord', () => {
    const cancel = vi.fn()
    const undo = vi.fn()
    off = attachKeys({ cancel, undo }, { ...keymap, 'Ctrl+Z': 'undo' })
    expect(press({ key: 'Escape' }).defaultPrevented).toBe(true)
    press({ key: 'z', ctrlKey: true })
    press({ key: 'q' })
    expect(cancel).toHaveBeenCalledTimes(1)
    expect(undo).toHaveBeenCalledTimes(1)
  })

  it('leaves the key to the browser when the command declines it', () => {
    off = attachKeys({ cancel: () => false })
    expect(press({ key: 'Escape' }).defaultPrevented).toBe(false)
  })

  it('stays quiet while a field has the key, the pad is on, or an earlier listener took the event', () => {
    const cancel = vi.fn()
    off = attachKeys({ cancel })
    for (const tag of ['input', 'textarea', 'select']) {
      const el = document.createElement(tag)
      document.body.append(el)
      press({ key: 'Escape' }, el)
      el.remove()
    }
    expect(cancel).not.toHaveBeenCalled()
    const resume = suspendKeys()
    press({ key: 'Escape' })
    expect(cancel).not.toHaveBeenCalled()
    resume()
    const taken = (e: KeyboardEvent) => e.preventDefault()
    window.addEventListener('keydown', taken, true)
    press({ key: 'Escape' })
    window.removeEventListener('keydown', taken, true)
    expect(cancel).not.toHaveBeenCalled()
    press({ key: 'Escape' })
    expect(cancel).toHaveBeenCalledTimes(1)
  })
})

// ---- Desktop mode's keys (card I9.6): input/bindings.ts read through keymapFor

describe('the desktop keymap', () => {
  const bound = BINDINGS.filter((b) => !b.shownOnly)
  const spies = () => Object.fromEntries([...new Set(BINDINGS.map((b) => b.command))].map((c) => [c, vi.fn()]))

  it('names Shift+1 by its key position, Space by name, and an Alt letter a Mac turns into another character by its letter', () => {
    expect(chordOf({ key: '!', code: 'Digit1', ctrlKey: false, altKey: false, shiftKey: true, metaKey: false })).toBe('Shift+1')
    expect(chordOf({ key: ' ', code: 'Space', ctrlKey: false, altKey: false, shiftKey: false, metaKey: false })).toBe('Space')
    expect(chordOf({ key: 'Dead', code: 'KeyN', ctrlKey: false, altKey: true, shiftKey: false, metaKey: false })).toBe('Alt+N')
    expect(chordOf({ key: 'Z', code: 'KeyZ', ctrlKey: true, altKey: false, shiftKey: true, metaKey: false })).toBe('Ctrl+Shift+Z')
    expect(chordOf({ key: 'ArrowRight', code: 'ArrowRight', ctrlKey: true, altKey: false, shiftKey: true, metaKey: false })).toBe('Ctrl+Shift+ArrowRight')
  })

  it('in Desktop mode every binding runs its command, and takes the key', () => {
    const commands = spies()
    off = attachKeys(commands, keymapFor('desktop'))
    for (const b of bound) {
      const e = press(keyOf(b.chord))
      expect(commands[b.command], b.chord).toHaveBeenCalled()
      expect(e.defaultPrevented, b.chord).toBe(true)
      commands[b.command]!.mockClear()
    }
    expect(bound.map((b) => b.chord)).toEqual(expect.arrayContaining(['Ctrl+Z', 'Ctrl+Y', 'Ctrl+Shift+Z', 'Ctrl+S', 'Ctrl+O', 'Shift+S', 'Shift+E', 'Shift+W', 'Shift+F', 'N', 'F', 'Space', 'Delete', 'Escape', 'Enter']))
    expect(bound.filter((b) => /^Shift\+[1-7]$/.test(b.chord)).map((b) => b.command)).toEqual(['view.front', 'view.back', 'view.left', 'view.right', 'view.top', 'view.bottom', 'view.iso'])
  })

  it('in Tablet mode only Esc and Delete run (decision d-s9-tablet-keys)', () => {
    const commands = spies()
    off = attachKeys(commands, keymapFor('tablet'))
    for (const b of bound) press(keyOf(b.chord))
    const ran = Object.entries(commands).filter(([, f]) => f.mock.calls.length > 0).map(([c]) => c)
    expect(ran.sort()).toEqual(['cancel', 'delete'])
    expect(keymapFor('tablet')).toEqual(keymap)
  })

  it('binds no chord a desktop browser keeps; New Part Studio, New Assembly and Close tab take the Alt replacements', () => {
    for (const kept of ['Ctrl+W', 'Ctrl+N', 'Ctrl+Shift+N', 'Ctrl+T', 'Ctrl+Q']) expect(keymapFor('desktop')[kept], kept).toBeUndefined()
    expect(chordFor('document.newStudio')).toBe('Alt+N')
    expect(chordFor('document.newAssembly')).toBe('Alt+Shift+N')
    expect(chordFor('document.closeTab')).toBe('Alt+W')
    expect(keymapFor('desktop')['Ctrl+O']).toBe('document.open')
  })

  it("the menus' chord hints are the bindings' chords", () => {
    const items = MENU_BAR.flatMap((m) => m.entries).filter((e): e is MenuItem => e.kind !== 'separator')
    for (const i of items) expect(i.chord, i.command).toBe(chordFor(i.command))
    expect(items.filter((i) => i.chord !== undefined).length).toBeGreaterThan(15)
  })

  it('no desktop key acts while a field or an open menu has the focus, or while the pad is editing', () => {
    const commands = spies()
    off = attachKeys(commands, keymapFor('desktop'))
    const field = document.createElement('input')
    const menu = document.createElement('div')
    menu.setAttribute('role', 'menu')
    const item = document.createElement('button')
    menu.append(item)
    document.body.append(field, menu)
    for (const b of bound) {
      press(keyOf(b.chord), field)
      press(keyOf(b.chord), item)
    }
    const resume = suspendKeys()
    for (const b of bound) press(keyOf(b.chord))
    resume()
    field.remove()
    menu.remove()
    expect(Object.values(commands).every((f) => f.mock.calls.length === 0)).toBe(true)
  })
})
