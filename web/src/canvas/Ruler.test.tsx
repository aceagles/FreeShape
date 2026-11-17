import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Vector3 } from 'three'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PointerModule } from '../input/pointer'
import { ViewCamera } from '../viewport/camera'
import { canvasSpec } from '../viewport/canvases'
import { registerView } from '../viewport/viewCommands'
import { CalibrateControls, CENTRE_LABEL, endRuler, rulerRunning, SNAP_LABEL } from './Ruler'
import type { CalibrateBody, RulerView } from './ruler'

// Calibrate's controls (card I12.6) over a real pointer module: Calibrate, two clicks on the canvas, the distance typed (Desktop mode) or
// entered on the pad (Tablet mode), and the request that goes out. A 100 x 50 mm canvas seen from above at 4 px to the mm.

const px = (u: number, v = 0) => ({ x: 400 + u * 4, y: 300 - v * 4 })
let el: HTMLCanvasElement
let module: PointerModule
let unregister: () => void
let calibrate: ReturnType<typeof vi.fn<(b: CalibrateBody) => Promise<boolean>>>
let onBegin: ReturnType<typeof vi.fn<() => void>>

const spec = canvasSpec({ id: 'C1', name: 'Canvas 1', blob: 'k', origin: [0, 0, 0], xdir: [1, 0, 0], ydir: [0, 1, 0], normal: [0, 0, 1], width: 100, height: 50, opacity: 50, flipH: false, flipV: false, selectable: true, hidden: false, filename: 'p.png', pixels: [] })

beforeEach(() => {
  const view = new ViewCamera()
  view.resize(800, 600)
  view.scale = 0.25
  view.setBack([0, 0, 1])
  view.target.set(0, 0, 0)
  view.apply()
  el = document.createElement('canvas')
  el.setPointerCapture = (() => {}) as unknown as typeof el.setPointerCapture
  const stage = document.createElement('div')
  stage.append(el)
  document.body.append(stage)
  module = new PointerModule(el, { view, pickPoint: () => null, modelCentre: () => new Vector3(), fit: vi.fn(), requestRender: vi.fn() })
  const host: RulerView = { pointer: module, view, canvas: () => spec, element: stage, surface: el, onFrame: () => () => {}, requestRender: () => {} }
  unregister = registerView({ setStandardView: () => {}, fit: () => {}, rulerHost: () => host })
  calibrate = vi.fn(async () => true)
  onBegin = vi.fn<() => void>()
})
afterEach(() => {
  cleanup()
  unregister()
  module.dispose()
  el.parentElement?.remove()
})

const mount = (desktop: boolean, fieldPad = false) => render(<CalibrateControls editing="C1" desktop={desktop} calibrate={calibrate} onBegin={onBegin} fieldPad={fieldPad} ready />)
const mouse = (type: string, x: number, y: number, t = 0) => {
  act(() => {
    el.dispatchEvent(Object.assign(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse', clientX: x, clientY: y, button: 0 }), {}))
    void t
  })
}
const clickAt = (u: number, v = 0) => {
  const p = px(u, v)
  mouse('pointermove', p.x, p.y)
  mouse('pointerdown', p.x, p.y)
  mouse('pointerup', p.x, p.y)
}
const phase = () => screen.getByTestId('calibrate').getAttribute('data-phase')

describe('Desktop mode', () => {
  it('Calibrate, two clicks 40 mm apart, 50 and Enter send calibrate with the two points in the canvas frame', async () => {
    mount(true)
    expect(rulerRunning()).toBe(false)
    fireEvent.click(screen.getByTestId('calibrate-start'))
    expect(onBegin).toHaveBeenCalledTimes(1)
    expect(rulerRunning()).toBe(true)
    clickAt(-20)
    expect(phase()).toBe('second')
    const second = px(20)
    mouse('pointermove', second.x, second.y)
    // the live length follows the mouse
    expect(screen.getByTestId('ruler-length').getAttribute('data-mm')).toBe('40')
    clickAt(20)
    expect(phase()).toBe('set')
    // the box asks for the real distance, prefilled with the measured length
    const box = (await screen.findByLabelText('Distance')) as HTMLInputElement
    expect(box.value).toBe('40')
    fireEvent.change(box, { target: { value: '50' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(calibrate).toHaveBeenCalledTimes(1))
    const body = calibrate.mock.calls[0]![0]
    expect(body.p0[0]).toBeCloseTo(-20, 6)
    expect(body.p0[1]).toBeCloseTo(0, 6)
    expect(body.p1[0]).toBeCloseTo(20, 6)
    expect(body.p1[1]).toBeCloseTo(0, 6)
    expect(body.distance).toBe(50)
    expect(body.centre).toBe(false)
    expect(body.snap).toBe(false)
    // the ruler is over and the pointer module is the selection's again
    await waitFor(() => expect(rulerRunning()).toBe(false))
    expect(screen.getByTestId('calibrate').getAttribute('data-phase')).toBe('off')
  })

  it('Enter on the prefilled length sends it as it is', async () => {
    mount(true)
    fireEvent.click(screen.getByTestId('calibrate-start'))
    clickAt(-20)
    clickAt(20)
    fireEvent.keyDown(await screen.findByLabelText('Distance'), { key: 'Enter' })
    await waitFor(() => expect(calibrate).toHaveBeenCalledTimes(1))
    expect(calibrate.mock.calls[0]![0].distance).toBe(40)
  })

  it('Centre on midpoint and Snap rotation to line send centre and snap', async () => {
    mount(true)
    fireEvent.click(screen.getByLabelText(CENTRE_LABEL))
    fireEvent.click(screen.getByLabelText(SNAP_LABEL))
    fireEvent.click(screen.getByTestId('calibrate-start'))
    clickAt(-20)
    clickAt(20)
    fireEvent.keyDown(await screen.findByLabelText('Distance'), { key: 'Enter' })
    await waitFor(() => expect(calibrate).toHaveBeenCalledTimes(1))
    expect(calibrate.mock.calls[0]![0]).toMatchObject({ centre: true, snap: true })
  })

  it('a click off the canvas sets no end, and Esc sends nothing and ends the ruler', async () => {
    mount(true)
    fireEvent.click(screen.getByTestId('calibrate-start'))
    clickAt(-80, 0)
    expect(phase()).toBe('first')
    expect(screen.getByTestId('calibrate-note').textContent).toMatch(/on the picture/)
    clickAt(-20)
    clickAt(20)
    const box = await screen.findByLabelText('Distance')
    fireEvent.change(box, { target: { value: '50' } })
    fireEvent.keyDown(box, { key: 'Escape' })
    expect(calibrate).not.toHaveBeenCalled()
    await waitFor(() => expect(rulerRunning()).toBe(false))
  })

  it('Esc without the box (the page\'s key reaches endRuler) ends the ruler; a second Esc is the dialog\'s', () => {
    mount(true)
    fireEvent.click(screen.getByTestId('calibrate-start'))
    clickAt(-20)
    let ended = false
    act(() => {
      ended = endRuler()
    })
    expect(ended).toBe(true)
    expect(rulerRunning()).toBe(false)
    expect(endRuler()).toBe(false)
    expect(calibrate).not.toHaveBeenCalled()
  })

  it('a distance that is not above 0 is refused and nothing is sent', async () => {
    mount(true)
    fireEvent.click(screen.getByTestId('calibrate-start'))
    clickAt(-20)
    clickAt(20)
    const box = await screen.findByLabelText('Distance')
    fireEvent.change(box, { target: { value: '0' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(screen.getByTestId('calibrate-note').textContent).toMatch(/above 0/))
    expect(calibrate).not.toHaveBeenCalled()
    expect(rulerRunning()).toBe(true)
  })

  it('a refused calibrate keeps the ruler for another try', async () => {
    calibrate.mockResolvedValueOnce(false)
    mount(true)
    fireEvent.click(screen.getByTestId('calibrate-start'))
    clickAt(-20)
    clickAt(20)
    fireEvent.keyDown(await screen.findByLabelText('Distance'), { key: 'Enter' })
    await waitFor(() => expect(calibrate).toHaveBeenCalledTimes(1))
    expect(rulerRunning()).toBe(true)
    expect(phase()).toBe('set')
  })

  it('a field\'s pad opening ends the ruler', () => {
    const { rerender } = mount(true)
    fireEvent.click(screen.getByTestId('calibrate-start'))
    expect(rulerRunning()).toBe(true)
    rerender(<CalibrateControls editing="C1" desktop calibrate={calibrate} onBegin={onBegin} fieldPad ready />)
    expect(rulerRunning()).toBe(false)
  })
})

describe('Tablet mode', () => {
  const pen = (type: string, x: number, y: number) =>
    act(() => {
      el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 7, pointerType: 'pen', clientX: x, clientY: y, pressure: type === 'pointerup' ? 0 : 0.5 }))
    })

  it('a pen drag from one end to the other, then 50 on the pad and Enter, send calibrate', async () => {
    mount(false)
    fireEvent.click(screen.getByTestId('calibrate-start'))
    const a = px(-20)
    const b = px(20)
    pen('pointerdown', a.x, a.y)
    pen('pointermove', px(0).x, px(0).y)
    pen('pointermove', b.x, b.y)
    pen('pointerup', b.x, b.y)
    expect(phase()).toBe('set')
    // the pad, prefilled with the measured length; the page holds no native input while it is open
    await screen.findByTestId('ruler-pad')
    expect(screen.getByTestId('pad-text').textContent).toBe('40')
    expect(document.querySelectorAll('input, textarea, select, [contenteditable]').length).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: '5' }))
    fireEvent.click(screen.getByRole('button', { name: '0' }))
    expect(screen.getByTestId('pad-text').textContent).toBe('50')
    fireEvent.click(screen.getByRole('button', { name: 'Enter' }))
    await waitFor(() => expect(calibrate).toHaveBeenCalledTimes(1))
    expect(calibrate.mock.calls[0]![0].distance).toBe(50)
    expect(calibrate.mock.calls[0]![0].p0[0]).toBeCloseTo(-20, 6)
    expect(calibrate.mock.calls[0]![0].p1[0]).toBeCloseTo(20, 6)
  })

  it('tap, tap with the pen gives the same request; the pad\'s Cancel ends the ruler with nothing sent', async () => {
    mount(false)
    fireEvent.click(screen.getByTestId('calibrate-start'))
    for (const u of [-20, 20]) {
      const p = px(u)
      pen('pointerdown', p.x, p.y)
      pen('pointerup', p.x, p.y)
    }
    expect(phase()).toBe('set')
    await screen.findByTestId('ruler-pad')
    fireEvent.click(screen.getByRole('button', { name: /^Cancel$/ }))
    expect(calibrate).not.toHaveBeenCalled()
    await waitFor(() => expect(rulerRunning()).toBe(false))
  })
})
