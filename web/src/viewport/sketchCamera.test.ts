import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SelectionStore } from '../selection/store'
import { SketchStore } from '../sketch/store'
import type { SketchTransport } from '../sketch/store'
import { openedMessage } from '../sketch/testFixture'
import { ViewportController } from './controller'

// The view's camera around a sketch (card I7.13): saved when the sketch opens, put back when it closes, and left alone
// when sketch mode is asked for again with the same store (an effect that re-ran because something else changed).
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

beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} }))
afterEach(() => vi.unstubAllGlobals())

function rig() {
  let listener: (m: Record<string, unknown>) => void = () => {}
  const transport: SketchTransport = { send: () => true, onSketch: (l) => ((listener = l), () => {}), onHello: () => () => {} }
  const store = new SketchStore(transport)
  const canvas = document.createElement('canvas')
  document.body.append(canvas)
  const c = new ViewportController(canvas, new SelectionStore())
  c.resize(1024, 768)
  store.open({ document: 'd1', tab: 't1', plane: { kind: 'std', name: 'Front' } })
  listener(openedMessage())
  return { c, store, listener }
}

it('a repeat of setSketch with the same store does not move the camera; closing puts the earlier camera back', () => {
  const { c, store, listener } = rig()
  const before = c.view.state()
  c.setSketch(store)
  const squared = c.view.state()
  expect(squared.back).not.toEqual(before.back)
  // the person pans and zooms in the sketch; the effect runs again with the same store
  c.view.target.set(5, 6, 7)
  c.view.scale = squared.scale * 0.5
  const moved = c.view.state()
  c.setSketch(store)
  c.setSketch(store)
  expect(c.view.state()).toEqual(moved)
  // Done: the camera from before the sketch is back
  listener({ type: 'sketch.committed', document: 'd1', tab: 't1', feature: 'F9', rebuilt: [], revision: 2 })
  c.setSketch(null)
  expect(c.view.state()).toEqual(before)
  c.dispose()
})
