import { vi } from 'vitest'
import { PointerModule } from '../../input/pointer'
import { ViewCamera } from '../../viewport/camera'
import { uvToView } from '../frame'
import { SketchMode } from '../mode'
import { SketchStore } from '../store'
import type { SketchTransport } from '../store'
import { FRONT, openedMessage, rectAndCircle } from '../testFixture'
import type { Uv } from '../wire'

/** A sketch mode on a 800 x 600 view with a store whose transport records what is sent and answers each sketch.add. */
export interface Rig {
  view: ViewCamera
  store: SketchStore
  mode: SketchMode
  canvas: HTMLCanvasElement
  parent: HTMLDivElement
  pointer: PointerModule
  sent: Record<string, unknown>[]
  adds(): Record<string, unknown>[]
  /** Where a sketch point is on screen. */
  at(uv: Uv): { x: number; y: number }
  fire(type: string, init: PointerEventInit): void
  pen(type: 'pointerdown' | 'pointermove' | 'pointerup', p: { x: number; y: number }): void
  /** Set to true and the server refuses every sketch.add. */
  refuse: { on: boolean }
  /** A pen press, move and lift at one point, then a frame. */
  stroke(p: { x: number; y: number }): void
  flush(): Promise<void>
  dispose(): void
}

export function rig(): Rig {
  const view = new ViewCamera()
  view.resize(800, 600)
  const sent: Record<string, unknown>[] = []
  let listener: (m: Record<string, unknown>) => void = () => {}
  let made = 0
  const refuse = { on: false }
  const transport: SketchTransport = {
    send: (m) => {
      sent.push(m)
      if (m['type'] === 'sketch.add') {
        const picks = (m['picks'] as unknown[]).length
        // the server answers once the message has been taken in, never inside the send
        if (refuse.on) queueMicrotask(() => listener({ type: 'error', for: 'sketch.add', message: 'refused' }))
        else queueMicrotask(() => listener({ ...openedMessage(), type: 'sketch.changed', made: { points: Array.from({ length: picks }, () => `n${++made}`), entities: [`m${made}`] } }))
      }
      return true
    },
    onSketch: (l) => ((listener = l), () => {}),
    onHello: () => () => {},
  }
  const store = new SketchStore(transport)
  const parent = document.createElement('div')
  const canvas = document.createElement('canvas')
  canvas.setPointerCapture = vi.fn() as unknown as typeof canvas.setPointerCapture
  parent.append(canvas)
  document.body.append(parent)
  const host = { view, requestRender: vi.fn(), pickPoint: () => null, modelCentre: () => view.target.clone(), fit: vi.fn() }
  const pointer = new PointerModule(canvas, host)
  const mode = new SketchMode({ view, pointer, overlayParent: parent, requestRender: vi.fn(), modelBounds: () => null }, store)
  listener({ ...openedMessage(rectAndCircle(), FRONT) })
  mode.update()
  const fire = (type: string, init: PointerEventInit) => canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, ...init }))
  const pen = (type: 'pointerdown' | 'pointermove' | 'pointerup', p: { x: number; y: number }) => fire(type, { pointerId: 7, pointerType: 'pen', clientX: p.x, clientY: p.y, pressure: type === 'pointerup' ? 0 : 0.5 })
  return {
    view,
    store,
    mode,
    canvas,
    parent,
    pointer,
    sent,
    refuse,
    adds: () => sent.filter((m) => m['type'] === 'sketch.add'),
    at: (uv) => uvToView(view, FRONT, uv),
    fire,
    pen,
    stroke: (p) => {
      pen('pointerdown', p)
      pen('pointermove', p)
      pen('pointerup', p)
      mode.update()
    },
    flush: async () => {
      for (let i = 0; i < 6; i++) await Promise.resolve()
    },
    dispose: () => {
      mode.dispose()
      pointer.dispose()
      parent.remove()
    },
  }
}
