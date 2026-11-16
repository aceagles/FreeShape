import { expect, it } from 'vitest'
import { DimensionTool } from './dimension'
import { PREVIEW_DIM } from './pick'
import { SketchStore } from './store'
import type { SketchTransport } from './store'
import { openedMessage } from './testFixture'

// F22: the Dimension tool's mouse flow in Desktop mode, as the desktop's sketcher.py has it (a press on an item picks it, the
// dimension then follows the pointer, the next click or a drag's release places it) and the same gestures in Tablet mode.
// The fixture rectangle is 60 x 48: e1 its bottom line, e2 its right line. 1 px = 0.5 mm.
const MM = 0.5
const E1 = { kind: 'entity' as const, id: 'e1' }
const E2 = { kind: 'entity' as const, id: 'e2' }

function rig(desktop: boolean) {
  const sent: Record<string, unknown>[] = []
  const listeners = new Set<(m: Record<string, unknown>) => void>()
  const transport: SketchTransport = {
    send: (m) => (sent.push(m), true),
    onSketch: (l) => (listeners.add(l), () => listeners.delete(l)),
    onHello: () => () => {},
  }
  const store = new SketchStore(transport)
  const tool = new DimensionTool({ sketch: store, transport, openPad: () => {}, closePad: () => {} })
  tool.attach()
  listeners.forEach((l) => l(openedMessage()))
  tool.setDesktop(desktop)
  tool.setActive(true)
  type Hit = typeof E1 | null
  const press = (hit: Hit, uv: [number, number]) => store.pressTools(hit, uv, 'mouse', MM)
  const aim = (hit: Hit, uv: [number, number]) => store.aimTools(hit, uv, 'mouse', MM)
  const release = (hit: Hit, uv: [number, number]) => store.commitTools(hit, uv, 'mouse', MM)
  /** The pointer pressing, moving and releasing, as mode.ts hands them to the store. */
  const gesture = (from: Hit, a: [number, number], moves: [number, number][], to: Hit, b: [number, number]) => {
    press(from, a)
    aim(from, a)
    for (const m of moves) aim(null, m)
    return release(to, b)
  }
  const previewed = () => store.getState().live?.extra?.[0] ?? null
  return { sent, store, tool, press, aim, release, gesture, previewed }
}

it('Desktop: a press on a side picks it, the drag shows the dimension at the pointer, the release places it and opens the value box', () => {
  const r = rig(true)
  r.press(E1, [30, 0])
  expect(r.tool.getState().picks).toEqual([E1])
  expect(r.store.getState().selected).toEqual([E1])
  r.aim(E1, [30, 0])
  expect(r.previewed()).toBeNull() // still over the item: it could be the second pick
  r.aim(null, [30, -20])
  expect(r.previewed()).toMatchObject({ id: PREVIEW_DIM, type: 'distance', refs: ['e1'], value: 60, pos: [30, -20] })
  expect(r.release(null, [30, -22])).toBe(true)
  expect(r.previewed()).toBeNull()
  const pad = r.tool.getState().pad
  expect(pad).toMatchObject({ mode: 'new', inline: true, value: '60 mm' })
  expect(r.tool.inlineAnchor()).toMatchObject({ uv: [30, -22], picks: ['e1'] })
  r.tool.padCommit('30')
  expect(r.sent).toEqual([{ type: 'sketch.dimension', picks: ['e1'], pos: [30, -22], value: 30 }])
})

it('Desktop: a press and release in place only picks; the preview then follows the pointer and the next click places the label', () => {
  const r = rig(true)
  expect(r.gesture(E1, [30, 0], [], E1, [30, 0])).toBe(true)
  expect(r.tool.getState()).toMatchObject({ picks: [E1], pad: null })
  r.aim(null, [10, -15])
  expect(r.previewed()).toMatchObject({ pos: [10, -15] })
  r.press(null, [12, -16])
  r.release(null, [12, -16])
  expect(r.tool.getState().pad).toMatchObject({ mode: 'new', inline: true })
  expect(r.tool.inlineAnchor()).toMatchObject({ uv: [12, -16] })
})

it('Desktop: a press on a second item, with or without a drag, makes the pair; the preview shows once two are picked', () => {
  const r = rig(true)
  r.gesture(E1, [30, 0], [], E1, [30, 0])
  r.press(E2, [60, 24])
  r.release(E2, [60, 24])
  expect(r.tool.getState().picks).toEqual([E1, E2])
  r.aim(E2, [60, 24])
  expect(r.previewed()).toMatchObject({ refs: ['e1', 'e2'] })
  expect(r.previewed()?.type).toBe('angle')
  expect(r.tool.getState().pad).toBeNull()
  // a drag from the first side that lets go on the second picks it and waits for the label
  const d = rig(true)
  d.gesture(E1, [30, 0], [[40, 10], [58, 20]], E2, [60, 24])
  expect(d.tool.getState().picks).toEqual([E1, E2])
  expect(d.tool.getState().pad).toBeNull()
})

it('Desktop: a drag that stays within the slop is a click, not a placement', () => {
  const r = rig(true)
  r.gesture(E1, [30, 0], [[31, 0.5]], E1, [31, 0.5])
  expect(r.tool.getState()).toMatchObject({ picks: [E1], pad: null })
})

it('Desktop: Esc during the press drops the picks and the release then does nothing', () => {
  const r = rig(true)
  r.press(E1, [30, 0])
  r.aim(null, [30, -20])
  expect(r.tool.escape()).toBe(true)
  expect(r.tool.getState().picks).toEqual([])
  expect(r.previewed()).toBeNull()
  expect(r.release(E1, [30, -20])).toBe(true)
  expect(r.tool.getState()).toMatchObject({ picks: [], pad: null })
  expect(r.sent).toEqual([])
})

it('Desktop: the hints say Click and drag, not Tap', () => {
  const r = rig(true)
  expect(r.tool.getState().hint).toMatch(/^Click .*drag/)
  r.press(E1, [30, 0])
  r.release(E1, [30, 0])
  expect(r.tool.getState().hint).toMatch(/^Click where the dimension goes/)
  expect(r.tool.getState().hint).not.toMatch(/Tap/)
})

it('Desktop: a pen or finger Commit is the Tablet flow: no preview, two Commits', () => {
  const r = rig(true)
  r.tool.commit(E1, [30, 0], 'pen')
  r.tool.aim?.(null, [30, -20], 'pen')
  expect(r.previewed()).toBeNull()
  r.tool.commit(null, [30, -20], 'pen')
  expect(r.tool.getState().pad).toMatchObject({ mode: 'new' })
  expect(r.tool.getState().pad?.inline).toBeUndefined()
})

it('Tablet: a mouse press, drag and release works as before: the release picks, no preview, the second Commit places', () => {
  const r = rig(false)
  r.press(E1, [30, 0])
  expect(r.tool.getState().picks).toEqual([])
  r.aim(null, [30, -20])
  expect(r.previewed()).toBeNull()
  r.release(E1, [30, 0])
  expect(r.tool.getState().picks).toEqual([E1])
  expect(r.tool.getState().hint).toMatch(/^Tap where the dimension goes/)
  r.aim(null, [30, -20])
  expect(r.previewed()).toBeNull()
  r.release(null, [30, -20])
  expect(r.tool.getState().pad).toMatchObject({ mode: 'new' })
  expect(r.tool.getState().pad?.inline).toBeUndefined()
})
