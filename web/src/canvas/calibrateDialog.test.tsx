import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Vector3 } from 'three'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { featureDialogKeys } from '../dialog/FeatureDialog'
import type { Description, FieldSpec, OpenReply } from '../dialog/dialogApi'
import { PointerModule } from '../input/pointer'
import { MODE_KEY } from '../shell/mode'
import { Shell } from '../shell/Shell'
import { makeStore, stubFetch, tab } from '../test/fakes'
import type { Call } from '../test/fakes'
import { fixture } from '../test/fixtures'
import { ViewCamera } from '../viewport/camera'
import { canvasSpec } from '../viewport/canvases'
import { registerView } from '../viewport/viewCommands'
import { rulerRunning } from './Ruler'
import type { RulerView } from './ruler'

// Calibrate in the whole Shell (card I12.6): the Canvas dialog draws its Calibrate controls, a click pair on the canvas and the typed
// distance reach the server's calibrate route as the body I12.2 takes, the reply's size is what the dialog shows, and Esc ends the ruler
// before it cancels the dialog. The view's own host is stood in for by one over a canvas of known size (the fixture has none).
vi.mock('three', async (orig) => {
  const three = await orig<typeof import('three')>()
  class FakeRenderer {
    setClearColor() {}
    setPixelRatio() {}
    setSize() {}
    render() {}
    dispose() {}
  }
  return { ...three, WebGLRenderer: FakeRenderer }
})

let mode = 'desktop'
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  window.localStorage.clear()
  window.localStorage.setItem(MODE_KEY, mode)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
  mode = 'desktop'
})

const row = (key: string, kind: string, label: string, extra: Partial<FieldSpec> = {}): FieldSpec => ({ key, kind, standard: true, visible: true, label, ...extra })
const description: Description = {
  type: 'canvas',
  custom: [],
  fields: [row('width', 'num', 'Width', { unit: 'mm', min: 0 }), row('height', 'num', 'Height', { unit: 'mm', min: 0 }), row('selectable', 'check', 'Selectable')],
}
const values = (w: number, h: number) => ({ width: { value: w, text: String(w) }, height: { value: h, text: String(h) }, selectable: { value: true } })
const open = (): OpenReply => ({ dialog: 'did1', document: 'd', tab: 'T1', feature: 'F2', name: 'Canvas 1', revision: 1, description, values: values(100, 50), fields: {}, active: null })

async function setup() {
  const { bytes } = fixture('bracket')
  const calls = stubFetch((c) => {
    if (c.method === 'GET' && c.url.endsWith('/tabs/T1/outputs')) return { bytes }
    if (c.url.endsWith('/features/F2/dialog')) return { status: 201, json: open() }
    if (c.url.endsWith('/update')) return { json: { ...open(), errors: {}, preview: null } }
    if (c.url.endsWith('/calibrate')) return { json: { ...open(), values: values(125, 62.5), errors: {}, preview: 2 } }
    if (c.url.endsWith('/cancel')) return { json: { cancelled: true } }
    if (c.url.endsWith('/accept')) return { json: { rebuilt: ['T1'], revision: 2 } }
    return undefined
  })
  const { store, sock } = makeStore()
  store.watch('d')
  render(<Shell store={store} documentId="d" onLeave={() => {}} />)
  sock.say({ type: 'hello', client: 'abcdefgh', document: { id: 'd', name: 'Bracket' }, tabs: [tab('T1', 'studio', 'Part Studio 1')], read_only: false, holder: null })
  await screen.findByRole('list', { name: 'Features' })
  await waitFor(() => expect(window.__freeshapeViewport?.revision).toBe(fixture('bracket').outputs.revision))
  const el = screen.getByRole('button', { name: 'Extrude 1' })
  fireEvent.click(el)
  fireEvent.click(el)
  await screen.findByTestId('calibrate')
  const calling = (suffix: string) => calls.filter((c: Call) => c.method === 'POST' && c.url.endsWith(suffix))
  return { calling }
}

/** A view of a 100 x 50 mm canvas from above, 4 px to the mm, on a pointer module of its own; the latest registered view is the one Calibrate asks. */
function standIn(): { click: (u: number) => void; off: () => void } {
  const view = new ViewCamera()
  view.resize(800, 600)
  view.scale = 0.25
  view.setBack([0, 0, 1])
  view.target.set(0, 0, 0)
  view.apply()
  const el = document.createElement('canvas')
  el.setPointerCapture = (() => {}) as unknown as typeof el.setPointerCapture
  const stage = document.createElement('div')
  stage.append(el)
  document.body.append(stage)
  const module = new PointerModule(el, { view, pickPoint: () => null, modelCentre: () => new Vector3(), fit: vi.fn(), requestRender: vi.fn() })
  const spec = canvasSpec({ id: 'F2', name: 'Canvas 1', blob: 'k', origin: [0, 0, 0], xdir: [1, 0, 0], ydir: [0, 1, 0], normal: [0, 0, 1], width: 100, height: 50, opacity: 50, flipH: false, flipV: false, selectable: true, hidden: false, filename: 'p.png', pixels: [] })
  const host: RulerView = { pointer: module, view, canvas: () => spec, element: stage, surface: el, onFrame: () => () => {}, requestRender: () => {} }
  const unregister = registerView({ setStandardView: () => {}, fit: () => {}, rulerHost: () => host })
  const fire = (type: string, x: number, y: number) =>
    act(() => {
      el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse', clientX: x, clientY: y, button: 0 }))
    })
  return {
    click: (u) => {
      fire('pointermove', 400 + u * 4, 300)
      fire('pointerdown', 400 + u * 4, 300)
      fire('pointerup', 400 + u * 4, 300)
    },
    off: () => {
      unregister()
      module.dispose()
      stage.remove()
    },
  }
}

it('Desktop: the Canvas dialog has Calibrate; two clicks and 50 send the calibrate body and the dialog shows the new size', async () => {
  const { calling } = await setup()
  const form = screen.getByTestId('dialog-form')
  expect(within(form).getByTestId('calibrate-start')).toBeTruthy()
  expect(within(form).getByLabelText('Centre on midpoint')).toBeTruthy()
  expect(within(form).getByLabelText('Snap rotation to line')).toBeTruthy()
  const ruler = standIn()
  try {
    fireEvent.click(screen.getByTestId('calibrate-start'))
    ruler.click(-20)
    ruler.click(20)
    const box = (await screen.findByLabelText('Distance')) as HTMLInputElement
    expect(box.value).toBe('40')
    fireEvent.change(box, { target: { value: '50' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(calling('/calibrate')).toHaveLength(1))
    const body = calling('/calibrate')[0]!.body as { p0: number[]; p1: number[]; distance: number; centre: boolean; snap: boolean }
    expect(body.p0[0]).toBeCloseTo(-20, 6)
    expect(body.p1[0]).toBeCloseTo(20, 6)
    expect(body.p0[1]).toBeCloseTo(0, 6)
    expect(body.distance).toBe(50)
    expect(Object.keys(body).sort()).toEqual(['centre', 'distance', 'p0', 'p1', 'snap'])
    // the reply is what the dialog shows now, and the ruler is over
    await waitFor(() => expect((screen.getByLabelText('Width') as HTMLInputElement).value).toBe('125'))
    expect((screen.getByLabelText('Height') as HTMLInputElement).value).toBe('62.5')
    await waitFor(() => expect(rulerRunning()).toBe(false))
    expect(calling('/accept')).toHaveLength(0)
  } finally {
    ruler.off()
  }
})

it('Desktop: while the ruler runs Enter does not accept the dialog, Esc ends the ruler, and the next Esc cancels the dialog', async () => {
  const { calling } = await setup()
  const ruler = standIn()
  try {
    fireEvent.click(screen.getByTestId('calibrate-start'))
    ruler.click(-20)
    expect(rulerRunning()).toBe(true)
    act(() => {
      expect(featureDialogKeys()!.accept()).toBe(false)
    })
    expect(calling('/accept')).toHaveLength(0)
    act(() => featureDialogKeys()!.cancel())
    expect(rulerRunning()).toBe(false)
    expect(calling('/calibrate')).toHaveLength(0)
    expect(calling('/cancel')).toHaveLength(0)
    expect(screen.getByRole('dialog', { name: 'Canvas 1' })).toBeTruthy()
    act(() => featureDialogKeys()!.cancel())
    await waitFor(() => expect(calling('/cancel')).toHaveLength(1))
  } finally {
    ruler.off()
  }
})

it('Tablet: Calibrate closes the first number\'s pad, then the distance is on the pad', async () => {
  window.localStorage.setItem(MODE_KEY, 'tablet')
  await setup()
  // the first number of the dialog opens on the pad by itself
  expect(screen.getAllByTestId('pad')).toHaveLength(1)
  const ruler = standIn()
  try {
    fireEvent.click(screen.getByTestId('calibrate-start'))
    expect(screen.queryByTestId('pad')).toBeNull()
    expect(rulerRunning()).toBe(true)
    ruler.click(-20)
    ruler.click(20)
    await screen.findByTestId('ruler-pad')
    expect(screen.getAllByTestId('pad')).toHaveLength(1)
    expect(document.querySelectorAll('input, textarea, select, [contenteditable]').length).toBe(0)
  } finally {
    ruler.off()
  }
})
