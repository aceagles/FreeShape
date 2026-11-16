import { afterEach, describe, expect, it, vi } from 'vitest'
import { attachKeys, chordOf, keymap, suspendKeys } from './keys'

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
