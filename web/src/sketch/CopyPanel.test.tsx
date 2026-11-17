import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { MODE_KEY, ModeProvider } from '../shell/mode'
import type { SketchHit } from './pick'
import { SketchStore } from './store'
import type { SketchTransport } from './store'
import { openedMessage, rectAndCircle } from './testFixture'
import { CopyActions, CopyPanel } from './CopyPanel'
import { CopyTools } from './tools/copy'

// The pattern panel (card I14.3) in each mode: Tablet mode takes the numbers on the pad and has no native input; Desktop
// mode types them in I9.16's fields, Enter in Instances moves to Spacing, Enter there accepts, Tab moves between them, and
// a pen opens the pad.

afterEach(() => {
  cleanup()
  window.localStorage.removeItem(MODE_KEY)
})

const E = (id: string): SketchHit => ({ kind: 'entity', id })

function open(mode: 'tablet' | 'desktop') {
  window.localStorage.setItem(MODE_KEY, mode)
  const sent: Record<string, unknown>[] = []
  const listeners = new Set<(m: Record<string, unknown>) => void>()
  const transport: SketchTransport = {
    send: (m) => (sent.push(m), true),
    onSketch: (l) => (listeners.add(l), () => listeners.delete(l)),
    onHello: () => () => {},
  }
  const store = new SketchStore(transport)
  const tool = new CopyTools({ sketch: store, transport, variables: () => [] })
  tool.attach()
  const say = (m: Record<string, unknown>) => act(() => listeners.forEach((l) => l(m)))
  say(openedMessage(rectAndCircle()))
  act(() => store.setSelection([E('e5')]))
  act(() => store.chooseTool('sk_lpattern'))
  act(() => void store.commitTools(E('e1'), [70, -2], mode === 'desktop' ? 'mouse' : 'pen', 0.1))
  render(
    <ModeProvider>
      <CopyActions tool={tool} />
      <CopyPanel tool={tool} variables={[]} />
    </ModeProvider>,
  )
  // the server's opening spacing
  const answer = (made: Record<string, unknown>) => {
    const last = sent.filter((m) => m['preview'] === true).at(-1)!
    say({ type: 'sketch.previewed', for: last['type'], id: last['id'], made, outlines: [] })
  }
  answer({ entities: ['e8'], spacing: 25 })
  const previews = () => sent.filter((m) => m['preview'] === true)
  const accepted = () => sent.filter((m) => m['type'] === 'sketch.linear_pattern' && m['preview'] !== true)
  return { tool, store, sent, previews, accepted, answer }
}

const panel = () => screen.getByTestId('copy-panel')
const key = (name: string) => fireEvent.click(within(screen.getByTestId('pad')).getByRole('button', { name }))

it('Tablet mode: the pad opens on Instances, a tap on Spacing moves the pad to it, every key previews, and no native input is in the panel', () => {
  const r = open('tablet')
  expect(panel().querySelector('input, textarea, [contenteditable]')).toBeNull()
  expect(screen.getByRole('textbox', { name: 'Instances, editing on the pad' })).toBeTruthy()
  key('4')
  expect(r.previews().at(-1)).toMatchObject({ count: 4, spacing: 25 })
  key('Enter')
  expect(screen.queryByTestId('pad')).toBeNull()
  // Spacing shows the server's value with its unit; a tap opens the pad on it
  const spacing = within(panel()).getByRole('button', { name: /^Spacing, 25 mm/ })
  fireEvent.click(spacing)
  expect(screen.getByRole('textbox', { name: 'Spacing (mm), editing on the pad' })).toBeTruthy()
  r.answer({ entities: ['e8'], spacing: 25 })
  key('1')
  key('2')
  r.answer({ entities: ['e8'], spacing: 1 })
  expect(r.previews().at(-1)).toMatchObject({ count: 4, spacing: 12 })
  expect(panel().querySelector('input, textarea, [contenteditable]')).toBeNull()
  key('Enter')
  fireEvent.click(screen.getByRole('button', { name: 'Accept' }))
  expect(r.accepted()).toHaveLength(1)
  expect(r.accepted()[0]).toMatchObject({ count: 4, spacing: 12 })
})

it('Tablet mode: Cancel on the pad puts the opening text back; Instances of 1 is marked and Accept is off', () => {
  const r = open('tablet')
  key('7')
  expect(r.tool.getState().panel!.count.text).toBe('7')
  key('Cancel')
  expect(r.tool.getState().panel!.count.text).toBe('3')
  fireEvent.click(within(panel()).getByRole('button', { name: /^Instances, 3/ }))
  r.answer({ entities: ['e8'] })
  const before = r.sent.length
  key('1')
  expect(r.sent).toHaveLength(before)
  expect(within(panel()).getByRole('alert').textContent).toBe('A whole number, 2 or more.')
  expect((screen.getByRole('button', { name: 'Accept' }) as HTMLButtonElement).disabled).toBe(true)
  // the header's Cancel (the first; the pad has its own) closes the panel with nothing sent
  fireEvent.click(screen.getAllByRole('button', { name: 'Cancel' })[0]!)
  expect(r.tool.getState().panel).toBeNull()
  expect(r.sent).toHaveLength(before)
})

function typeInto(input: HTMLInputElement, text: string) {
  fireEvent.pointerDown(input, { pointerId: 1, pointerType: 'mouse', button: 0 })
  act(() => input.focus())
  fireEvent.change(input, { target: { value: text } })
}

it('Desktop mode: Instances has the focus, a mouse click types in place, Enter in Instances moves to Spacing, Enter there accepts', () => {
  const r = open('desktop')
  expect(screen.queryByTestId('pad')).toBeNull()
  const count = within(panel()).getByRole('textbox', { name: 'Instances' }) as HTMLInputElement
  const spacing = within(panel()).getByRole('textbox', { name: 'Spacing' }) as HTMLInputElement
  expect(document.activeElement).toBe(count)
  expect(spacing.value).toBe('25')
  typeInto(count, '4')
  fireEvent.keyDown(count, { key: 'Enter' })
  expect(r.previews().at(-1)).toMatchObject({ count: 4, spacing: 25 })
  expect(document.activeElement).toBe(spacing)
  r.answer({ entities: ['e8'], spacing: 25 })
  fireEvent.change(spacing, { target: { value: '12' } })
  fireEvent.keyDown(spacing, { key: 'Enter' })
  expect(r.accepted()).toHaveLength(1)
  expect(r.accepted()[0]).toMatchObject({ count: 4, spacing: 12 })
})

it('Desktop mode: Enter on an invalid Instances stays; Tab moves between the rows; a pen tap opens the pad', () => {
  const r = open('desktop')
  const count = () => within(panel()).getByRole('textbox', { name: 'Instances' }) as HTMLInputElement
  const spacing = () => within(panel()).getByRole('textbox', { name: 'Spacing' }) as HTMLInputElement
  typeInto(count(), '2.5')
  fireEvent.keyDown(count(), { key: 'Enter' })
  expect(document.activeElement).toBe(count())
  expect(r.tool.getState().panel!.count.error).not.toBeNull()
  typeInto(count(), '5')
  fireEvent.keyDown(count(), { key: 'Tab' })
  expect(document.activeElement).toBe(spacing())
  fireEvent.keyDown(spacing(), { key: 'Tab' })
  expect(document.activeElement).toBe(count())
  expect(r.accepted()).toHaveLength(0)
  act(() => count().blur())
  fireEvent.pointerDown(spacing(), { pointerId: 2, pointerType: 'pen', button: 0 })
  expect(screen.getByTestId('pad')).toBeTruthy()
  expect(screen.getByRole('textbox', { name: 'Spacing (mm), editing on the pad' })).toBeTruthy()
  // Esc in a field is Cancel: nothing sent, the panel gone
  key('Enter')
  fireEvent.keyDown(count(), { key: 'Escape' })
  expect(r.tool.getState().panel).toBeNull()
  expect(r.accepted()).toHaveLength(0)
})
