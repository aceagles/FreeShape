import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HOLD_TIME } from '../input/pointer'
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
    r.pen('pointermove', { x: 380, y: 303 }) // east: Circle
    expect(button('circle').hasAttribute('data-aimed')).toBe(true)
    r.pen('pointerup', { x: 380, y: 303 })
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

  it('a button for a tool a later card builds is dimmed and does nothing', () => {
    r.pen('pointerdown', { x: 300, y: 300 })
    hold()
    r.pen('pointerup', { x: 300, y: 300 })
    expect(button('construct').getAttribute('aria-disabled')).toBe('true')
    for (const id of ['line', 'rectangle', 'circle', 'arc', 'point', 'select', 'dimension']) expect(button(id).hasAttribute('aria-disabled')).toBe(false)
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
    expect(ringCentre(10, 10, 800, 600)).toEqual({ x: 162, y: 152 })
    expect(ringCentre(795, 595, 800, 600)).toEqual({ x: 638, y: 448 })
    expect(ringCentre(50, 50, 200, 200)).toEqual({ x: 100, y: 100 })
  })
})
