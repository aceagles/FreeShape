import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HOLD_TIME } from '../input/pointer'
import { installSurfaceGuards } from '../input/surface'
import { QUICK_TOOLS, ringCentre, sectorOf } from './quickmenu'
import { rig } from './tools/testRig'
import type { Rig } from './tools/testRig'

let r: Rig
beforeEach(() => {
  vi.useFakeTimers()
  r = rig()
  r.store.chooseTool('line')
})
afterEach(() => {
  r.dispose()
  vi.useRealTimers()
})

const ring = () => r.parent.querySelector<HTMLElement>('[data-testid="quick-menu"]')
const button = (id: string) => r.parent.querySelector<HTMLButtonElement>(`[data-testid="quick-${id}"]`)!
const hold = () => vi.advanceTimersByTime(HOLD_TIME)

describe('the sketch quick menu', () => {
  it('a pen press held 400 ms without moving opens the ring, and its release places no point', () => {
    r.pen('pointerdown', { x: 300, y: 300 })
    expect(ring()).toBeNull()
    hold()
    expect(ring()).not.toBeNull()
    expect(r.parent.querySelectorAll('.quick-menu button')).toHaveLength(8)
    expect([...r.parent.querySelectorAll('.quick-menu button')].map((b) => b.textContent)).toEqual(QUICK_TOOLS.map((t) => t.label))
    r.pen('pointerup', { x: 300, y: 300 })
    expect(r.mode.report()!.draw.picks).toHaveLength(0)
    expect(r.adds()).toHaveLength(0)
    expect(ring()).not.toBeNull() // released without sliding: the ring stays for a tap
    expect(r.mode.report()!.draw.aim).toBeNull()
  })

  it('also opens for a finger, and its release selects nothing', () => {
    r.store.chooseTool('select')
    r.fire('pointerdown', { pointerId: 3, pointerType: 'touch', clientX: 400, clientY: 300 })
    hold()
    expect(ring()).not.toBeNull()
    r.fire('pointerup', { pointerId: 3, pointerType: 'touch', clientX: 400, clientY: 300 })
    expect(r.store.getState().selected).toEqual([])
  })

  it('sliding onto Circle and releasing makes circle the active tool, and places no point', () => {
    r.pen('pointerdown', { x: 300, y: 300 })
    hold()
    r.pen('pointermove', { x: 357, y: 357 }) // south east: Circle
    expect(button('circle').hasAttribute('data-aimed')).toBe(true)
    r.pen('pointerup', { x: 357, y: 357 })
    expect(r.store.getState().tool).toBe('circle')
    expect(ring()).toBeNull()
    expect(r.mode.report()!.draw.picks).toHaveLength(0)
  })

  it('a tap on a button of the open ring picks it', () => {
    r.pen('pointerdown', { x: 300, y: 300 })
    hold()
    r.pen('pointerup', { x: 300, y: 300 })
    button('point').click()
    expect(r.store.getState().tool).toBe('point')
    expect(ring()).toBeNull()
  })

  it('a press that moves 30 px within 400 ms opens nothing', () => {
    r.pen('pointerdown', { x: 300, y: 300 })
    vi.advanceTimersByTime(200)
    r.pen('pointermove', { x: 330, y: 300 })
    hold()
    expect(ring()).toBeNull()
    r.pen('pointerup', { x: 330, y: 300 })
    expect(r.mode.report()!.draw.picks).toHaveLength(1) // it Aimed and Committed as before
  })

  it('a finger that moves sooner pans as before', () => {
    const before = r.mode.report()!.screenOf('point', 'p2')!
    r.fire('pointerdown', { pointerId: 3, pointerType: 'touch', clientX: 300, clientY: 300 })
    r.fire('pointermove', { pointerId: 3, pointerType: 'touch', clientX: 340, clientY: 300 })
    hold()
    expect(ring()).toBeNull()
    expect(r.mode.report()!.screenOf('point', 'p2')!.x - before.x).toBeCloseTo(40, 3)
  })

  it('a hold 20 px from the corner of the view opens the ring with all eight buttons inside the view', () => {
    for (const [x, y] of [
      [20, 20],
      [780, 20],
      [20, 580],
      [780, 580],
    ] as const) {
      r.pen('pointerdown', { x, y })
      hold()
      const root = ring()!
      const cx = parseFloat(root.style.left)
      const cy = parseFloat(root.style.top)
      const buttons = [...root.querySelectorAll<HTMLElement>('button')]
      expect(buttons).toHaveLength(8)
      for (const b of buttons) {
        const left = cx + parseFloat(b.style.left)
        const top = cy + parseFloat(b.style.top)
        expect(left).toBeGreaterThanOrEqual(0)
        expect(top).toBeGreaterThanOrEqual(0)
        expect(left + parseFloat(b.style.width)).toBeLessThanOrEqual(800)
        expect(top + parseFloat(b.style.height)).toBeLessThanOrEqual(600)
      }
      r.pen('pointerup', { x, y })
      r.pen('pointerdown', { x: 400, y: 300 }) // a press elsewhere closes it
      r.pen('pointerup', { x: 400, y: 300 })
      expect(ring()).toBeNull()
    }
  })

  it('a press elsewhere closes the ring and does nothing else', () => {
    r.pen('pointerdown', { x: 300, y: 300 })
    hold()
    r.pen('pointerup', { x: 300, y: 300 })
    r.pen('pointerdown', { x: 500, y: 200 })
    expect(ring()).toBeNull()
    r.pen('pointermove', { x: 510, y: 210 })
    r.pen('pointerup', { x: 510, y: 210 })
    expect(r.mode.report()!.draw.picks).toHaveLength(0)
    // the next press draws again
    r.stroke({ x: 500, y: 200 })
    expect(r.mode.report()!.draw.picks).toHaveLength(1)
  })

  it('every button is on; Construct acts on the selection and leaves the tool alone (no tool is left without a card)', () => {
    r.pen('pointerdown', { x: 300, y: 300 })
    hold()
    r.pen('pointerup', { x: 300, y: 300 })
    for (const id of ['line', 'rectangle', 'circle', 'arc', 'point', 'select', 'dimension', 'construct']) expect(button(id).hasAttribute('aria-disabled')).toBe(false)
    button('construct').click()
    expect(r.store.getState().tool).toBe('line')
  })

  it('does not open while a second finger makes it a pinch, and a cancelled press closes it', () => {
    r.fire('pointerdown', { pointerId: 3, pointerType: 'touch', clientX: 300, clientY: 300 })
    r.fire('pointerdown', { pointerId: 4, pointerType: 'touch', clientX: 400, clientY: 300 })
    hold()
    expect(ring()).toBeNull()
    r.fire('pointerup', { pointerId: 3, pointerType: 'touch', clientX: 300, clientY: 300 })
    r.fire('pointerup', { pointerId: 4, pointerType: 'touch', clientX: 400, clientY: 300 })
    r.pen('pointerdown', { x: 300, y: 300 })
    hold()
    expect(ring()).not.toBeNull()
    r.fire('pointercancel', { pointerId: 7, pointerType: 'pen' })
    expect(ring()).toBeNull()
  })

  it('is gone when the sketch closes', () => {
    r.pen('pointerdown', { x: 300, y: 300 })
    hold()
    r.pen('pointerup', { x: 300, y: 300 })
    r.mode.dispose()
    expect(ring()).toBeNull()
  })
})

describe('the ring geometry', () => {
  it('has no button chosen inside the dead zone and eight sectors outside it', () => {
    expect(sectorOf(10, 10)).toBe(-1)
    expect([sectorOf(0, -60), sectorOf(60, -60), sectorOf(60, 0), sectorOf(60, 60), sectorOf(0, 60), sectorOf(-60, 60), sectorOf(-60, 0), sectorOf(-60, -60)]).toEqual([0, 1, 2, 3, 4, 5, 6, 7])
  })
  it('is centred on the press when it fits and moved inward when it does not', () => {
    expect(ringCentre(400, 300, 800, 600)).toEqual({ x: 400, y: 300 })
    expect(ringCentre(10, 10, 800, 600)).toEqual({ x: 128, y: 120 })
    expect(ringCentre(795, 595, 800, 600)).toEqual({ x: 672, y: 480 })
    expect(ringCentre(50, 50, 200, 200)).toEqual({ x: 100, y: 100 })
  })
})

describe('a finger holds, slides and lifts (F19)', () => {
  const t = (type: string, x: number, y: number) => r.fire(type, { pointerId: 5, pointerType: 'touch', clientX: x, clientY: y })

  it('keeps every move with the finger down on the ring: it lights the item under the finger, a release on it runs it, nothing is selected, no selectstart default runs', () => {
    const off = installSurfaceGuards()
    r.store.chooseTool('select')
    t('pointerdown', 300, 300)
    hold()
    expect(ring()).not.toBeNull()
    // what the browser does after a long press: start a selection under the finger
    const label = document.createElement('span')
    label.textContent = 'label'
    r.parent.append(label)
    const start = new Event('selectstart', { bubbles: true, cancelable: true })
    label.dispatchEvent(start)
    expect(start.defaultPrevented).toBe(true)
    t('pointermove', 350, 250) // up and right: Line
    expect(button('line').hasAttribute('data-aimed')).toBe(true)
    t('pointermove', 350, 350) // down and right: Circle
    expect(button('circle').hasAttribute('data-aimed')).toBe(true)
    expect(button('line').hasAttribute('data-aimed')).toBe(false)
    expect(window.getSelection()?.toString() ?? '').toBe('')
    t('pointerup', 350, 350)
    expect(r.store.getState().tool).toBe('circle')
    expect(ring()).toBeNull()
    expect(window.getSelection()?.rangeCount ?? 0).toBe(0)
    off()
  })

  it('releasing back at the centre or lifting leaves the ring for a tap, and a tool is not changed', () => {
    r.store.chooseTool('select')
    t('pointerdown', 300, 300)
    hold()
    t('pointermove', 350, 350)
    t('pointermove', 302, 301) // back inside the dead zone
    expect(r.parent.querySelector('.quick-menu [data-aimed]')).toBeNull()
    t('pointerup', 302, 301)
    expect(r.store.getState().tool).toBe('select')
    expect(ring()).not.toBeNull()
  })

  it('a pointercancel after the hold opened (the browser took the gesture) closes the ring and leaves no timer, no tool change', () => {
    r.store.chooseTool('select')
    t('pointerdown', 300, 300)
    hold()
    t('pointermove', 350, 350)
    t('pointercancel', 350, 350)
    expect(ring()).toBeNull()
    expect(r.store.getState().tool).toBe('select')
    expect(vi.getTimerCount()).toBe(0)
    // and the next press starts clean
    t('pointerdown', 200, 200)
    hold()
    expect(ring()).not.toBeNull()
  })

  it('a pointercancel before the hold is up cancels the timer: no ring opens later', () => {
    t('pointerdown', 300, 300)
    t('pointercancel', 300, 300)
    expect(vi.getTimerCount()).toBe(0)
    hold()
    expect(ring()).toBeNull()
  })

  it('a pen cancelled mid-hold leaves no Aim and no ring', () => {
    r.store.chooseTool('line')
    r.pen('pointerdown', { x: 300, y: 300 })
    hold()
    expect(ring()).not.toBeNull()
    r.fire('pointercancel', { pointerId: 7, pointerType: 'pen', clientX: 300, clientY: 300 })
    expect(ring()).toBeNull()
    expect(r.mode.report()!.draw.aim).toBeNull()
    expect(r.mode.report()!.draw.picks).toHaveLength(0)
  })
})

describe('the ring size (F19)', () => {
  it('every button is at least 44 x 44, no two overlap, and the hub is clear of every button', () => {
    r.pen('pointerdown', { x: 400, y: 300 })
    hold()
    const rects = [...ring()!.querySelectorAll<HTMLElement>('button')].map((b) => ({
      l: parseFloat(b.style.left),
      t: parseFloat(b.style.top),
      w: parseFloat(b.style.width),
      h: parseFloat(b.style.height),
    }))
    for (const a of rects) {
      expect(a.w).toBeGreaterThanOrEqual(44)
      expect(a.h).toBeGreaterThanOrEqual(44)
      expect(a.l <= 0 && a.l + a.w >= 0 && a.t <= 0 && a.t + a.h >= 0).toBe(false) // the finger's own spot is no button
      expect(Math.hypot(Math.max(Math.abs(a.l + a.w / 2) - a.w / 2, 0), Math.max(Math.abs(a.t + a.h / 2) - a.h / 2, 0))).toBeGreaterThanOrEqual(40) // 40 px of room round the press
    }
    for (const [i, a] of rects.entries())
      for (const b of rects.slice(i + 1)) expect(a.l + a.w <= b.l || b.l + b.w <= a.l || a.t + a.h <= b.t || b.t + b.h <= a.t).toBe(true)
  })
})

describe('Select in the ring (F19)', () => {
  it('is the top item, straight up from the held point', () => {
    expect(QUICK_TOOLS[0]!.id).toBe('select')
    expect(QUICK_TOOLS.slice(1).map((t) => t.id)).toEqual(['line', 'rectangle', 'circle', 'arc', 'dimension', 'point', 'construct'])
    expect(sectorOf(0, -80)).toBe(0)
  })

  it('hold mid-chain, pick Select: no chain is pending and the tool is Select', () => {
    r.store.chooseTool('line')
    r.store.setDrawing(2) // a chain of two points is pending
    expect(r.store.getState().drawing).toBe(2)
    r.fire('pointerdown', { pointerId: 5, pointerType: 'touch', clientX: 300, clientY: 300 })
    hold()
    expect(ring()).not.toBeNull() // the hold opens mid-chain
    r.fire('pointermove', { pointerId: 5, pointerType: 'touch', clientX: 300, clientY: 240 }) // straight up
    expect(button('select').hasAttribute('data-aimed')).toBe(true)
    r.fire('pointerup', { pointerId: 5, pointerType: 'touch', clientX: 300, clientY: 240 })
    expect(r.store.getState().drawing).toBe(0)
    expect(r.store.getState().tool).toBe('select')
    expect(ring()).toBeNull()
  })

  it('draws the tool that is on as pressed', () => {
    r.fire('pointerdown', { pointerId: 5, pointerType: 'touch', clientX: 300, clientY: 300 })
    hold()
    expect(button('line').getAttribute('aria-pressed')).toBe('true')
    expect(button('circle').hasAttribute('aria-pressed')).toBe(false)
  })
})
