import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { desktopKeymap } from '../input/bindings'
import { attachKeys, keymapFor } from '../input/keys'
import { ModeProvider, MODE_KEY } from '../shell/mode'
import { desktopCommands } from '../shell/desktopKeys'
import type { DesktopKeyContext } from '../shell/desktopKeys'
import { keyOf } from '../test/keys'
import { ConstraintStore } from './constraints'
import { DimensionTool } from './dimension'
import { DimensionHint, DimensionPad } from './DimensionPad'
import { SketchChip } from './SketchChip'
import { SketchStore } from './store'
import type { SketchTransport } from './store'
import { openedMessage } from './testFixture'

// Card I9.7: the desktop's sketch keys (freeshape/ui/sketcher.py Sketcher.key_press and TOOL_KEYS) through the one keymap.
// The rig routes keys exactly as the shell does: attachKeys, the Desktop keymap, desktopCommands.

interface Rig {
  store: SketchStore
  constraints: ConstraintStore
  dimension: DimensionTool
  sent: Record<string, unknown>[]
  say(m: Record<string, unknown>): void
  key(chord: string, target?: EventTarget): KeyboardEvent
  left: { count: number }
  closed: { opened: number; closed: number }
}

let off: (() => void)[] = []
beforeEach(() => {
  window.localStorage.clear()
  window.localStorage.setItem(MODE_KEY, 'desktop')
})
afterEach(() => {
  cleanup()
  off.forEach((f) => f())
  off = []
  window.localStorage.clear()
})

function rig(mode: 'desktop' | 'tablet' = 'desktop'): Rig {
  const sent: Record<string, unknown>[] = []
  const listeners = new Set<(m: Record<string, unknown>) => void>()
  const transport: SketchTransport = {
    send: (m) => (sent.push(m), true),
    onSketch: (l) => (listeners.add(l), () => listeners.delete(l)),
    onHello: () => () => {},
  }
  const store = new SketchStore(transport)
  const constraints = new ConstraintStore(store, transport)
  const closed = { opened: 0, closed: 0 }
  const dimension = new DimensionTool({ sketch: store, transport, openPad: () => closed.opened++, closePad: () => closed.closed++ })
  dimension.attach()
  const say = (m: Record<string, unknown>) => act(() => listeners.forEach((l) => l(m)))
  say(openedMessage())
  const left = { count: 0 }
  // the Desktop's tool wiring: the dimension tool is on while it is the sketch's tool (Shell.tsx)
  const follow = () => dimension.setActive(store.getState().tool === 'dimension')
  store.subscribe(follow)
  const ctx = (): DesktopKeyContext =>
    ({
      readOnly: false,
      tabKind: 'studio',
      sketch: store.getState().open ? store : null,
      constraints,
      dialog: () => null,
      closeArea: null,
      modalOpen: false,
      leaveSketch: () => void left.count++,
    }) as unknown as DesktopKeyContext
  off.push(attachKeys(() => desktopCommands(ctx()), () => keymapFor(mode)))
  const key = (chord: string, target: EventTarget = window): KeyboardEvent => {
    const e = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...keyOf(chord) })
    act(() => void target.dispatchEvent(e))
    return e
  }
  return { store, constraints, dimension, sent, say, key, left, closed }
}

const TOOLS: Record<string, string> = { L: 'line', G: 'rectangle', R: 'center_rectangle', C: 'circle', A: 'arc', D: 'dimension', S: 'select', P: 'point', U: 'use_edge' }
const CONSTRAINTS: Record<string, string> = { I: 'coincident', H: 'horizontal', V: 'vertical', E: 'equal', T: 'tangent' }

describe('the tool keys', () => {
  it('each drawing tool, Dimension, Select, Point and Use letter chooses its tool in Desktop mode', () => {
    const r = rig()
    for (const [chord, tool] of Object.entries(TOOLS)) {
      // from another tool, so that the key is not a restart
      r.store.chooseTool(tool === 'line' ? 'circle' : 'line')
      r.key(chord)
      expect(r.store.getState().tool, chord).toBe(tool)
    }
  })

  it('each Constrain letter starts the Constrain tool, tool first, as its button does', () => {
    const r = rig()
    for (const [chord, tool] of Object.entries(CONSTRAINTS)) {
      r.key('S')
      r.key(chord)
      expect(r.constraints.getState().picking, chord).toBe(tool)
    }
  })

  it('none chooses a tool in Tablet mode', () => {
    const r = rig('tablet')
    for (const chord of [...Object.keys(TOOLS), ...Object.keys(CONSTRAINTS), 'Q', 'O']) {
      const e = r.key(chord)
      expect(e.defaultPrevented, chord).toBe(false)
    }
    expect(r.store.getState().tool).toBe('select')
    expect(r.constraints.getState().picking).toBeNull()
  })

  it('nothing is chosen outside a sketch', () => {
    const r = rig()
    r.say({ type: 'sketch.cancelled' })
    expect(r.key('L').defaultPrevented).toBe(false)
    expect(r.store.getState().tool).toBe('select')
  })

  it('a tool key drops the selection, as set_tool does', () => {
    const r = rig()
    r.store.setSelection([{ kind: 'entity', id: 'e1' }])
    r.key('L')
    expect(r.store.getState().selected).toEqual([])
  })

  it('a tool key for the tool that is on starts it over and stays on it; the button chosen again goes back to Select', () => {
    const r = rig()
    r.key('L')
    r.store.setDrawing(1)
    r.key('L')
    expect(r.store.getState().tool).toBe('line')
    expect(r.store.getState().drawing).toBe(0)
    r.store.chooseTool('line')
    expect(r.store.getState().tool).toBe('select')
  })

  it('a key with a modifier is not a tool key, and letters in a field are the field\'s', () => {
    const r = rig()
    r.key('Shift+L')
    r.key('Ctrl+L')
    expect(r.store.getState().tool).toBe('select')
    const input = document.createElement('input')
    document.body.append(input)
    r.key('L', input)
    input.remove()
    expect(r.store.getState().tool).toBe('select')
  })

  it('O shows the hint Desktop only for now, changes no tool, and the hint goes with the next tool', () => {
    const r = rig()
    r.key('L')
    const view = render(<SketchChip store={r.store} />, { wrapper: ModeProvider })
    expect(r.key('O').defaultPrevented).toBe(true)
    expect(r.store.getState().tool).toBe('line')
    expect(r.store.getState().notice).toBe('Desktop only for now')
    expect(screen.getByTestId('sketch-notice').textContent).toBe('Desktop only for now')
    expect(r.sent.filter((m) => m['type'] !== 'sketch.fits')).toEqual([])
    r.key('C')
    expect(r.store.getState().notice).toBeNull()
    view.unmount()
  })
})

describe('Q, Ctrl+A and Space', () => {
  it('Q makes the selected line construction and, pressed again, real', () => {
    const r = rig()
    r.store.setSelection([{ kind: 'entity', id: 'e1' }])
    r.key('Q')
    expect(r.sent.filter((m) => m['type'] === 'sketch.construction').at(-1)).toMatchObject({ ids: ['e1'], value: true })
    // the server's answer marks it; the second press reverses
    const view = openedMessage()
    const sk = view['sketch'] as { entities: Record<string, Record<string, unknown>> }
    sk.entities['e1']!['construction'] = true
    r.say({ ...view, type: 'sketch.changed' })
    r.store.setSelection([{ kind: 'entity', id: 'e1' }])
    r.key('Q')
    expect(r.sent.filter((m) => m['type'] === 'sketch.construction').at(-1)).toMatchObject({ ids: ['e1'], value: false })
  })

  it('Q with nothing selected switches construction mode on and off', () => {
    const r = rig()
    r.key('Q')
    expect(r.store.getState().construction).toBe(true)
    r.key('Q')
    expect(r.store.getState().construction).toBe(false)
  })

  it('Ctrl+A selects every entity and Space clears the selection', () => {
    const r = rig()
    const e = r.key('Ctrl+A')
    expect(e.defaultPrevented).toBe(true)
    expect(r.store.getState().selected.map((h) => h.id).sort()).toEqual(['e1', 'e2', 'e3', 'e4', 'e5', 'e6'])
    expect(r.store.getState().selected.every((h) => h.kind === 'entity')).toBe(true)
    r.key('Space')
    expect(r.store.getState().selected).toEqual([])
  })

  it('Delete and Backspace delete the selection', () => {
    const r = rig()
    for (const chord of ['Delete', 'Backspace']) {
      r.store.setSelection([{ kind: 'entity', id: 'e5' }])
      r.key(chord)
      expect(r.sent.filter((m) => m['type'] === 'sketch.delete').at(-1)).toMatchObject({ ids: expect.arrayContaining(['e5']) })
    }
  })
})

describe('Esc, in the desktop sketcher\'s order', () => {
  it('a chain in progress is dropped and the selection cleared; then Select; then the selection clears; then the sketch is left', () => {
    const r = rig()
    r.key('L')
    r.store.setDrawing(2)
    r.store.setSelection([{ kind: 'entity', id: 'e1' }])
    r.key('Escape')
    expect(r.store.getState().drawing).toBe(0)
    expect(r.store.getState().selected).toEqual([])
    expect(r.store.getState().tool).toBe('line')
    r.key('Escape')
    expect(r.store.getState().tool).toBe('select')
    r.store.setSelection([{ kind: 'entity', id: 'e1' }])
    r.key('Escape')
    expect(r.store.getState().selected).toEqual([])
    expect(r.left.count).toBe(0)
    r.key('Escape')
    expect(r.left.count).toBe(1)
  })

  it('a Constrain tool collecting picks drops them and clears the selection first', () => {
    const r = rig()
    r.key('H')
    r.store.setSelection([{ kind: 'entity', id: 'e1' }])
    r.key('Escape')
    expect(r.constraints.getState().picking).toBeNull()
    expect(r.store.getState().selected).toEqual([])
    expect(r.store.getState().tool).toBe('select')
    expect(r.left.count).toBe(0)
  })

  it('a dimension\'s first pick is dropped and the tool stays; the next Esc chooses Select', () => {
    const r = rig()
    r.key('D')
    act(() => void r.dimension.commit({ kind: 'entity', id: 'e1' }, [10, 0], 'mouse'))
    expect(r.dimension.getState().picks).toHaveLength(1)
    r.key('Escape')
    expect(r.dimension.getState().picks).toEqual([])
    expect(r.store.getState().selected).toEqual([])
    expect(r.store.getState().tool).toBe('dimension')
    r.key('Escape')
    expect(r.store.getState().tool).toBe('select')
  })
})

describe('a new dimension\'s value box', () => {
  const open = (r: Rig) => {
    r.key('D')
    act(() => {
      r.dimension.commit({ kind: 'entity', id: 'e1' }, [30, -5], 'mouse')
      r.dimension.commit(null, [30, -5], 'mouse')
    })
  }

  it('the pad, opened and untouched, takes a tool key by closing and choosing the tool', () => {
    const r = rig()
    open(r)
    expect(r.dimension.getState().pad?.mode).toBe('new')
    expect(r.closed.opened).toBe(1)
    act(() => void r.dimension.toolKey(new KeyboardEvent('keydown', keyOf('L'))))
    expect(r.dimension.getState().pad).toBeNull()
    expect(r.store.getState().tool).toBe('line')
  })

  const padOf = (r: Rig) => render(<DimensionPad tool={r.dimension} variables={[]} />)
  const placeNew = (r: Rig, pointer: 'pen' | 'mouse') => {
    r.key('D')
    act(() => {
      r.dimension.commit({ kind: 'entity', id: 'e1' }, [30, -5], pointer)
      r.dimension.commit(null, [30, -5], pointer)
    })
  }

  it('with something typed, the key goes to the pad: a letter is typed, no tool is chosen', () => {
    const r = rig()
    placeNew(r, 'pen')
    const view = padOf(r)
    // the pad keeps the keys to itself while it is open
    expect(r.key('3').defaultPrevented).toBe(true)
    expect(r.key('L').defaultPrevented).toBe(true)
    expect(r.dimension.getState().pad).not.toBeNull()
    expect(r.store.getState().tool).toBe('dimension')
    view.unmount()
  })

  it('with nothing typed, the pad hands a tool key on: it closes and the tool is chosen', () => {
    const r = rig()
    placeNew(r, 'pen')
    const view = padOf(r)
    r.key('L')
    expect(r.dimension.getState().pad).toBeNull()
    expect(r.store.getState().tool).toBe('line')
    view.unmount()
  })

  const inline = (r: Rig) => {
    const view = render(
      <ModeProvider>
        <div className="shell" data-mode="desktop">
          <div className="shell-viewport">
            <DimensionHint tool={r.dimension} />
          </div>
        </div>
      </ModeProvider>,
    )
    placeNew(r, 'mouse')
    expect(r.dimension.getState().pad?.inline).toBe(true)
    return view
  }

  it('the inline box, untouched: a tool key closes it and chooses the tool', () => {
    const r = rig()
    inline(r)
    fireEvent.keyDown(screen.getByRole('textbox'), keyOf('L'))
    expect(r.dimension.getState().pad).toBeNull()
    expect(r.store.getState().tool).toBe('line')
  })

  it('the inline box, once a character is typed: the key is typed', () => {
    const r = rig()
    inline(r)
    const box = screen.getByRole('textbox')
    fireEvent.change(box, { target: { value: '3' } })
    fireEvent.keyDown(box, keyOf('L'))
    expect(r.dimension.getState().pad).not.toBeNull()
    expect(r.store.getState().tool).toBe('dimension')
  })
})

describe('the keymap', () => {
  it('every tool key of the desktop is bound to the sketch, and the tooltips of the Desktop toolbar name the same keys', async () => {
    const { DESKTOP_SKETCH_TOOLBAR } = await import('./strip')
    const bound = Object.entries(desktopKeymap).filter(([, c]) => c.includes('sketch.tool.')).map(([chord]) => chord)
    expect(bound.sort()).toEqual(['A', 'C', 'D', 'E', 'G', 'H', 'I', 'L', 'O', 'P', 'R', 'S', 'T', 'U', 'V'])
    for (const entry of DESKTOP_SKETCH_TOOLBAR.items) {
      if (entry.kind !== 'command' || entry.key === undefined || entry.key.length !== 1) continue
      expect(bound.concat('Q'), entry.tool.label).toContain(entry.key)
    }
  })

  it('a chord bound in a sketch and outside one runs the sketch command first and lets the other take the key outside', () => {
    const a = vi.fn(() => false)
    const b = vi.fn(() => true)
    off.push(attachKeys({ a, b }, { P: 'a b' }))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'p', cancelable: true }))
    expect(a).toHaveBeenCalled()
    expect(b).toHaveBeenCalled()
  })
})
