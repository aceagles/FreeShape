import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { ModeProvider, MODE_KEY } from '../shell/mode'
import { DimensionTool } from './dimension'
import { DimensionHint } from './DimensionPad'
import { SketchStore } from './store'
import type { SketchTransport } from './store'
import { openedMessage } from './testFixture'

// The desktop's inline dimension box (card I9.16): in Desktop mode a mouse double click on a dimension's label types its
// value in a 110 px box at the label, not on the pad; a pen still gets the pad. The fixture's k3 is a 60 mm dimension.
let mode = 'desktop'
beforeEach(() => {
  window.localStorage.clear()
  window.localStorage.setItem(MODE_KEY, mode)
})
afterEach(() => {
  cleanup()
  window.localStorage.clear()
  mode = 'desktop'
})

const rect = (left: number, top: number, width: number, height: number) => ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) }) as DOMRect

function rig() {
  const sent: Record<string, unknown>[] = []
  const listeners = new Set<(m: Record<string, unknown>) => void>()
  const transport: SketchTransport = {
    send: (m) => (sent.push(m), true),
    onSketch: (l) => (listeners.add(l), () => listeners.delete(l)),
    onHello: () => () => {},
  }
  const store = new SketchStore(transport)
  let t = 1000
  const dock = { opened: 0, closed: 0 }
  const tool = new DimensionTool({ sketch: store, transport, openPad: () => dock.opened++, closePad: () => dock.closed++, now: () => t })
  tool.attach()
  const say = (m: Record<string, unknown>) => act(() => listeners.forEach((l) => l(m)))
  say(openedMessage())
  act(() => void tool.setActive(true))
  // the shell's viewport, with the sketch layer's label of k3 at (300, 200), 40 x 20
  const view = render(
    <ModeProvider>
      <div className="shell" data-mode={mode}>
        <div className="shell-viewport" data-testid="viewport">
          <div className="sketch-layer">
            <svg>
              <g data-kind="dimension" data-id="k3">
                <rect data-testid="label-k3" />
              </g>
            </svg>
          </div>
          <div className="chip-slot">
            <DimensionHint tool={tool} />
          </div>
        </div>
      </div>
    </ModeProvider>,
  )
  screen.getByTestId('viewport').getBoundingClientRect = () => rect(0, 0, 800, 600)
  screen.getByTestId('label-k3').getBoundingClientRect = () => rect(300, 200, 40, 20)
  const double = (pointer: 'mouse' | 'pen') =>
    act(() => {
      tool.commit({ kind: 'dimension', id: 'k3' }, [30, -12], pointer)
      t += 100
      tool.commit({ kind: 'dimension', id: 'k3' }, [30, -12], pointer)
    })
  const changed = (made: Record<string, unknown>) => say({ ...openedMessage(), type: 'sketch.changed', made })
  return { sent, say, tool, dock, double, changed, view }
}

const box = () => screen.getByRole('textbox') as HTMLInputElement

it('a mouse double click on a label opens the inline box at the label, focused on the value; Enter sends it', () => {
  const r = rig()
  r.double('mouse')
  expect(r.dock.opened).toBe(0) // no dialog area, no pad
  expect(screen.queryByTestId('pad')).toBeNull()
  const el = screen.getByTestId('dimension-inline')
  // 110 px, centred on the label's middle (320, 210), 13 px above it
  expect(el.style.left).toBe('265px')
  expect(el.style.top).toBe('197px')
  expect(box().style.width).toBe('110px')
  expect(box().value).toBe('60') // the desktop shows the measure bare
  expect(document.activeElement).toBe(box())
  fireEvent.change(box(), { target: { value: '25' } })
  fireEvent.keyDown(box(), { key: 'Enter' })
  expect(r.sent).toEqual([{ type: 'sketch.dimension.set', constraint: 'k3', value: 25 }])
  r.changed({ constraint: 'k3', type: 'distance', value: 25 })
  expect(screen.queryByTestId('dimension-inline')).toBeNull()
  expect(r.dock.closed).toBe(0)
})

it('an expression goes as typed; Esc gives the box up and sends nothing', () => {
  const r = rig()
  r.double('mouse')
  fireEvent.change(box(), { target: { value: '2*10' } })
  fireEvent.keyDown(box(), { key: 'Escape' })
  expect(r.sent).toEqual([])
  expect(screen.queryByTestId('dimension-inline')).toBeNull()
  r.double('mouse')
  fireEvent.change(box(), { target: { value: '2*10' } })
  fireEvent.keyDown(box(), { key: 'Enter' })
  expect(r.sent).toEqual([{ type: 'sketch.dimension.set', constraint: 'k3', value: '2*10' }])
})

it('a text that is not a number is marked and not sent; a refusal shows the server words under the box', () => {
  const r = rig()
  r.double('mouse')
  fireEvent.change(box(), { target: { value: 'abc' } })
  fireEvent.keyDown(box(), { key: 'Enter' })
  expect(r.sent).toEqual([])
  expect(box().hasAttribute('data-invalid')).toBe(true)
  fireEvent.change(box(), { target: { value: '0' } })
  fireEvent.keyDown(box(), { key: 'Enter' })
  expect(r.sent).toHaveLength(1)
  r.say({ type: 'error', for: 'sketch.dimension.set', message: 'The value must be more than 0' })
  expect(screen.getByTestId('dimension-refusal').textContent).toBe('The value must be more than 0')
  expect(box().value).toBe('0')
})

it('leaving the box without Enter gives the dimension up, as the desktop does', () => {
  const r = rig()
  r.double('mouse')
  fireEvent.change(box(), { target: { value: '30' } })
  act(() => box().blur())
  expect(r.sent).toEqual([])
  expect(r.tool.getState().pad).toBeNull()
})

it('a pen double tap in Desktop mode still opens the pad in the dialog area', () => {
  const r = rig()
  r.double('pen')
  expect(r.dock.opened).toBe(1)
  expect(screen.queryByTestId('dimension-inline')).toBeNull()
})

it('Tablet mode is as before: a mouse double click opens the pad', () => {
  mode = 'tablet'
  window.localStorage.setItem(MODE_KEY, 'tablet')
  const r = rig()
  r.double('mouse')
  expect(r.dock.opened).toBe(1)
  expect(screen.queryByTestId('dimension-inline')).toBeNull()
})
