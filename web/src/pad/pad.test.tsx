import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ConnectionStore, type SocketLike } from '../net/connection'
import { Shell } from '../shell/Shell'
import { NumberPad, PAD_KEYS, PadField } from './NumberPad'
import { openText, reduce } from './editor'
import { usePadEditor } from './usePadEditor'
import type { PadOptions } from './usePadEditor'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function Harness(opts: PadOptions) {
  const editor = usePadEditor(opts)
  return (
    <>
      <PadField editor={editor} label="Depth" />
      <NumberPad editor={editor} />
    </>
  )
}

function setup(over: Partial<PadOptions> = {}) {
  const onCommit = vi.fn()
  const onCancel = vi.fn()
  render(<Harness value="" variables={['depth', 'width']} onCommit={onCommit} onCancel={onCancel} {...over} />)
  const tap = (...names: string[]) => names.forEach((n) => act(() => screen.getByRole('button', { name: n }).click()))
  const text = () => screen.getByTestId('pad-text').textContent + (screen.getByTestId('pad-field').lastElementChild?.textContent ?? '')
  return { onCommit, onCancel, tap, text }
}

it('1 2 . 5 mm Enter gives the text 12.5 mm and one commit', () => {
  const { onCommit, tap, text } = setup()
  tap('1', '2', 'Decimal point', '5', 'mm')
  expect(text()).toBe('12.5 mm')
  tap('Enter')
  expect(onCommit).toHaveBeenCalledTimes(1)
  expect(onCommit).toHaveBeenCalledWith('12.5 mm')
})

it('Enter twice in a row commits once', () => {
  const { onCommit, tap } = setup()
  tap('4', 'Enter', 'Enter')
  expect(onCommit).toHaveBeenCalledTimes(1)
})

it('Cancel restores the value the field had when the pad opened', () => {
  const { onCancel, onCommit, tap, text } = setup({ value: '25 mm' })
  expect(text()).toBe('25 mm')
  tap('7', 'Plus', '8')
  expect(text()).toBe('7+8')
  tap('Cancel')
  expect(text()).toBe('25 mm')
  expect(onCancel).toHaveBeenCalledTimes(1)
  expect(onCommit).not.toHaveBeenCalled()
})

it('the opening value is replaced by a digit, and carried on by an operator', () => {
  const a = setup({ value: '25 mm' })
  a.tap('3')
  expect(a.text()).toBe('3')
  cleanup()
  const b = setup({ value: '25' })
  b.tap('Multiply', '2')
  expect(b.text()).toBe('25*2')
})

it('the # key lists the variables passed in and inserts the chosen one', () => {
  const { tap, text } = setup({ value: '25 mm', variables: ['depth', 'wall_thickness'] })
  expect(screen.queryByTestId('pad-variables')).toBeNull()
  tap('Variables')
  const list = within(screen.getByTestId('pad-variables'))
  expect(list.getAllByRole('option').map((o) => o.textContent)).toEqual(['#depth', '#wall_thickness'])
  act(() => list.getByRole('option', { name: '#wall_thickness' }).click())
  expect(text()).toBe('#wall_thickness')
  expect(screen.queryByTestId('pad-variables')).toBeNull()
  tap('Multiply', '2')
  expect(text()).toBe('#wall_thickness*2')
})

it('says so when no variable is in scope', () => {
  const { tap } = setup({ variables: [] })
  tap('Variables')
  expect(screen.getByText('No variables in scope')).toBeTruthy()
})

it('the field contains no input, textarea or contenteditable element', () => {
  const { tap } = setup({ value: '25 mm' })
  tap('Variables')
  expect(document.body.querySelectorAll('input, textarea, select, [contenteditable]').length).toBe(0)
  expect(screen.getByTestId('pad-field').tagName).toBe('DIV')
})

it('has the 25 keys of the layout, each named', () => {
  setup()
  expect(PAD_KEYS.length).toBe(25)
  expect(screen.getByTestId('pad').querySelectorAll('.pad-key').length).toBe(25)
  for (const n of ['Backspace', 'Change sign', 'Variables', 'mm', 'deg', 'in', 'Cancel', 'Enter', 'Open bracket', 'Close bracket']) {
    expect(screen.getByRole('button', { name: n })).toBeTruthy()
  }
})

it('a hardware keyboard types into the same field', () => {
  const { onCommit, text } = setup({ value: '25 mm' })
  const key = (k: string) => act(() => void fireEvent.keyDown(window, { key: k }))
  for (const k of ['1', '2', '.', '5', ' ', 'm', 'm']) key(k)
  expect(text()).toBe('12.5 mm')
  key('Backspace')
  expect(text()).toBe('12.5 m')
  key('ArrowLeft')
  key('9')
  expect(text()).toBe('12.5 9m')
  key('Enter')
  expect(onCommit).toHaveBeenCalledWith('12.5 9m')
})

it('Escape on a hardware keyboard cancels', () => {
  const { onCancel } = setup({ value: '5' })
  act(() => void fireEvent.keyDown(window, { key: 'Escape' }))
  expect(onCancel).toHaveBeenCalledTimes(1)
})

it('stops listening to the keyboard once unmounted', () => {
  const { onCommit } = setup({ value: '5' })
  cleanup()
  fireEvent.keyDown(window, { key: 'Enter' })
  expect(onCommit).not.toHaveBeenCalled()
})

it('a unit replaces a unit, and sign flips a number or wraps an expression', () => {
  let s = openText('')
  const run = (...a: Parameters<typeof reduce>[1][]) => a.forEach((x) => (s = reduce(s, x)))
  run({ kind: 'insert', text: '5' }, { kind: 'unit', unit: 'mm' }, { kind: 'unit', unit: 'in' })
  expect(s.text).toBe('5 in')
  run({ kind: 'sign' })
  expect(s.text).toBe('-5 in')
  run({ kind: 'sign' })
  expect(s.text).toBe('5 in')
  s = openText('')
  run({ kind: 'variable', name: 'mm' }, { kind: 'unit', unit: 'mm' })
  expect(s.text).toBe('#mm mm')
  s = openText('')
  run({ kind: 'insert', text: '2' }, { kind: 'insert', text: '+' }, { kind: 'insert', text: '3' }, { kind: 'sign' })
  expect(s.text).toBe('-(2+3)')
  run({ kind: 'sign' })
  expect(s.text).toBe('2+3')
})

// ---- in the shell: docked in the dialog area, and a tap on it never reaches the viewport ----

class Sock implements SocketLike {
  readyState = 1
  onopen: SocketLike['onopen'] = null
  onmessage: SocketLike['onmessage'] = null
  onclose: SocketLike['onclose'] = null
  onerror: SocketLike['onerror'] = null
  send() {}
  close() {}
}

it('the pad docks at the foot of the dialog area, and its taps reach neither the viewport nor the selection', () => {
  vi.stubGlobal('fetch', async () => new Response('', { status: 404 }))
  const store = new ConnectionStore({ clientId: 'abcdefgh', url: 'ws://t/ws', createSocket: () => new Sock(), visible: () => true, pingMs: 3_600_000, backoff: () => 3_600_000 })
  store.start()
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  act(() => window.__freeshape?.openPadDemo({ value: '25 mm' }))
  const dialog = screen.getByRole('dialog')
  const pad = screen.getByTestId('pad')
  expect(dialog.lastElementChild?.contains(pad)).toBe(true)
  expect(screen.getByTestId('left-column').contains(pad)).toBe(true)

  const viewport = screen.getByTestId('shell-viewport')
  const seen = vi.fn()
  for (const t of ['pointerdown', 'pointerup', 'pointermove', 'click']) viewport.addEventListener(t, seen)
  const rev = window.__freeshape!.shell.selection.getState().revision
  const key = screen.getByRole('button', { name: '5' })
  fireEvent.pointerDown(key, { pointerType: 'touch', pointerId: 1 })
  fireEvent.pointerUp(key, { pointerType: 'touch', pointerId: 1 })
  fireEvent.click(key)
  expect(seen).not.toHaveBeenCalled()
  expect(window.__freeshape!.shell.selection.getState().revision).toBe(rev)
  expect(screen.getByTestId('pad-text').textContent).toBe('5')

  act(() => screen.getByRole('button', { name: 'Enter' }).click())
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(window.__freeshape?.padResult()).toEqual({ committed: true, text: '5' })
  store.stop()
})
