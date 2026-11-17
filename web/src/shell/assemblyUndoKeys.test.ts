import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { attachKeys, keymapFor } from '../input/keys'
import { MODE_KEY } from './mode'
import { desktopCommands } from './desktopKeys'
import type { DesktopKeyContext } from './desktopKeys'
import { keyOf } from '../test/keys'

// F30: Ctrl+Z, Ctrl+Y and Ctrl+Shift+Z on an Assembly tab call the Assembly toolbar's undo and redo (mainwindow.py undo / redo).
let off: (() => void) | null = null
beforeEach(() => window.localStorage.setItem(MODE_KEY, 'desktop'))
afterEach(() => {
  off?.()
  off = null
  document.body.replaceChildren()
  window.localStorage.clear()
})

function rig(over: Partial<DesktopKeyContext> = {}, mode: 'desktop' | 'tablet' = 'desktop') {
  const calls = { assemblyUndo: vi.fn(), assemblyRedo: vi.fn(), undo: vi.fn(), redo: vi.fn(), sketchUndo: vi.fn() }
  const ctx = {
    readOnly: false,
    tabKind: 'assembly',
    sketch: null,
    dialog: () => null,
    closeArea: null,
    modalOpen: false,
    undo: { enabled: true, run: calls.undo },
    redo: { enabled: true, run: calls.redo },
    assemblyUndo: calls.assemblyUndo,
    assemblyRedo: calls.assemblyRedo,
    ...over,
  } as unknown as DesktopKeyContext
  off = attachKeys(() => desktopCommands(ctx), () => keymapFor(mode))
  const key = (chord: string, target: EventTarget = window): KeyboardEvent => {
    const e = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...keyOf(chord) })
    target.dispatchEvent(e)
    return e
  }
  return { calls, key }
}

describe('Undo and Redo keys on an Assembly tab', () => {
  it('Ctrl+Z undoes an Assembly step and not the Part Studio\'s', () => {
    const { calls, key } = rig()
    expect(key('Ctrl+Z').defaultPrevented).toBe(true)
    expect(calls.assemblyUndo).toHaveBeenCalledTimes(1)
    expect(calls.undo).not.toHaveBeenCalled()
    expect(calls.assemblyRedo).not.toHaveBeenCalled()
  })

  it('Ctrl+Y and Ctrl+Shift+Z redo an Assembly step', () => {
    const { calls, key } = rig()
    key('Ctrl+Y')
    key('Ctrl+Shift+Z')
    expect(calls.assemblyRedo).toHaveBeenCalledTimes(2)
    expect(calls.redo).not.toHaveBeenCalled()
    expect(calls.assemblyUndo).not.toHaveBeenCalled()
  })

  it('does nothing in a read-only Document, over an open dialog, or while typing in a field', () => {
    const ro = rig({ readOnly: true })
    ro.key('Ctrl+Z')
    expect(ro.calls.assemblyUndo).not.toHaveBeenCalled()
    off?.()
    const dlg = rig({ closeArea: () => {} })
    dlg.key('Ctrl+Z')
    expect(dlg.calls.assemblyUndo).not.toHaveBeenCalled()
    off?.()
    const typing = rig()
    const field = document.createElement('input')
    document.body.append(field)
    typing.key('Ctrl+Z', field)
    typing.key('Ctrl+Y', field)
    expect(typing.calls.assemblyUndo).not.toHaveBeenCalled()
    expect(typing.calls.assemblyRedo).not.toHaveBeenCalled()
  })

  it('does nothing in Tablet mode', () => {
    const { calls, key } = rig({}, 'tablet')
    key('Ctrl+Z')
    key('Ctrl+Y')
    expect(calls.assemblyUndo).not.toHaveBeenCalled()
    expect(calls.assemblyRedo).not.toHaveBeenCalled()
  })

  it('a Part Studio tab keeps the Part Studio\'s undo and redo', () => {
    const { calls, key } = rig({ tabKind: 'studio' })
    key('Ctrl+Z')
    key('Ctrl+Y')
    expect(calls.undo).toHaveBeenCalledTimes(1)
    expect(calls.redo).toHaveBeenCalledTimes(1)
    expect(calls.assemblyUndo).not.toHaveBeenCalled()
    expect(calls.assemblyRedo).not.toHaveBeenCalled()
  })

  it('an open sketch keeps the sketch\'s undo', () => {
    const { calls, key } = rig({ tabKind: 'studio', sketch: {} as never })
    key('Ctrl+Z')
    expect(calls.undo).toHaveBeenCalledTimes(1)
    expect(calls.assemblyUndo).not.toHaveBeenCalled()
  })
})
