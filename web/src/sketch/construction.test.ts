import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { constructionPressed } from './store'
import { rig } from './tools/testRig'
import type { Rig } from './tools/testRig'

// F20: Construct with nothing selected turns construction mode on; the drawing tools then make construction geometry in the
// same sketch.add message (one undo step); the active tool chosen again goes back to Select.

let r: Rig
beforeEach(() => {
  r = rig()
})
afterEach(() => r.dispose())

// the fixture's rectangle, circle and diagonal fill the middle: these are empty places of it
const A: [number, number] = [5, 30]
const B: [number, number] = [25, 40]
const C: [number, number] = [8, 44]
const pressed = () => constructionPressed(r.store.getState())

describe('Construct with nothing selected', () => {
  it('turns the mode on and the button shows pressed, and a second press turns it off', () => {
    expect(pressed()).toBe(false)
    r.store.chooseTool('construct')
    expect(r.store.getState().construction).toBe(true)
    expect(pressed()).toBe(true)
    expect(r.sent.filter((m) => m['type'] === 'sketch.construction')).toEqual([])
    r.store.chooseTool('construct')
    expect(r.store.getState().construction).toBe(false)
    expect(pressed()).toBe(false)
  })

  it('changes no tool and does not end a line in progress', () => {
    r.store.chooseTool('line')
    r.stroke(r.at(A))
    expect(r.store.getState().drawing).toBe(1)
    r.store.chooseTool('construct')
    expect(r.store.getState().tool).toBe('line')
    expect(r.store.getState().drawing).toBe(1)
  })

  it('a line drawn with the mode on is made construction in the one sketch.add message', async () => {
    r.store.chooseTool('line')
    r.store.chooseTool('construct')
    r.stroke(r.at(A))
    r.stroke(r.at(B))
    await r.flush()
    expect(r.adds()).toHaveLength(1)
    expect(r.adds()[0]!['construction']).toBe(true)
    // one message is one undo step: nothing follows that flags it afterwards
    expect(r.sent.filter((m) => m['type'] === 'sketch.construction')).toEqual([])
  })

  it('with the mode off a line carries no construction flag', async () => {
    r.store.chooseTool('line')
    r.stroke(r.at(A))
    r.stroke(r.at(B))
    await r.flush()
    expect(r.adds()[0]!['construction']).toBeUndefined()
  })

  it('every drawing tool takes the mode, but a point is not flagged (it is always construction)', async () => {
    r.store.chooseTool('construct')
    r.store.chooseTool('circle')
    r.stroke(r.at(A))
    r.stroke(r.at(B))
    r.store.chooseTool('rectangle')
    r.stroke(r.at(A))
    r.stroke(r.at(C))
    r.store.chooseTool('point')
    r.stroke(r.at(B))
    await r.flush()
    await r.flush()
    await r.flush()
    expect(r.adds().map((m) => [m['shape'], m['construction']])).toEqual([['circle', true], ['rect', true], ['point', undefined]])
  })

  it('stays on across tools and the chain, and goes off when the sketch closes', async () => {
    r.store.chooseTool('construct')
    r.store.chooseTool('line')
    r.stroke(r.at(A))
    r.stroke(r.at(B))
    r.stroke(r.at(C))
    r.store.chooseTool('select')
    expect(r.store.getState().construction).toBe(true)
    await r.flush()
    expect(r.adds().map((m) => m['construction'])).toEqual([true, true])
    r.store.cancel()
    // the server answers a cancel with sketch.cancelled; the store is closed then
    r.sent.length = 0
  })

  it('the rubber band is drawn as construction while it is drawn', () => {
    r.store.chooseTool('line')
    r.store.chooseTool('construct')
    r.stroke(r.at(A))
    r.pen('pointerdown', r.at(B))
    r.pen('pointermove', r.at(B))
    r.mode.update()
    const band = r.parent.querySelector('[data-kind="band"]')!
    expect(band.hasAttribute('data-construction')).toBe(true)
    expect(band.querySelector('path')!.getAttribute('stroke-dasharray')).toBe('8 6')
  })
})

describe('Construct with a selection', () => {
  it('toggles it, keeps the selection, and a second press reverses it', () => {
    r.store.setSelection([{ kind: 'entity', id: 'e1' }])
    expect(pressed()).toBe(false)
    r.store.chooseTool('construct')
    expect(r.sent.at(-1)).toEqual({ type: 'sketch.construction', ids: ['e1'], value: true })
    expect(r.store.getState().selected).toEqual([{ kind: 'entity', id: 'e1' }])
    expect(r.store.getState().construction).toBe(false)
  })

  it('shows pressed when every selected entity is construction, so a press will make it real', () => {
    r.store.setSelection([{ kind: 'entity', id: 'e6' }])
    expect(pressed()).toBe(true)
    r.store.chooseTool('construct')
    expect(r.sent.at(-1)).toEqual({ type: 'sketch.construction', ids: ['e6'], value: false })
    r.store.setSelection([{ kind: 'entity', id: 'e6' }, { kind: 'entity', id: 'e1' }])
    expect(pressed()).toBe(false)
  })

  it('shows the mode, not the selection, when only a point or a dimension is selected', () => {
    r.store.chooseTool('construct')
    r.store.setSelection([{ kind: 'point', id: 'p1' }, { kind: 'dimension', id: 'k3' }])
    expect(pressed()).toBe(true)
  })

  it('choosing a drawing tool drops the selection, so Construct then sets the mode, as on the desktop', () => {
    r.store.setSelection([{ kind: 'entity', id: 'e1' }])
    r.store.chooseTool('line')
    expect(r.store.getState().selected).toEqual([])
    r.store.chooseTool('construct')
    expect(r.store.getState().construction).toBe(true)
    expect(r.sent.filter((m) => m['type'] === 'sketch.construction')).toEqual([])
  })
})

describe('back to Select', () => {
  it('the tool that is on, chosen again, goes to Select and drops a chain in progress', () => {
    r.store.chooseTool('line')
    r.stroke(r.at(A))
    expect(r.store.getState().drawing).toBe(1)
    r.store.chooseTool('line')
    expect(r.store.getState().tool).toBe('select')
    expect(r.store.getState().drawing).toBe(0)
    expect(r.mode.report()!.draw.picks).toEqual([])
  })

  it('Select chosen mid-chain does the same', () => {
    r.store.chooseTool('line')
    r.stroke(r.at(A))
    r.store.chooseTool('select')
    expect(r.store.getState().tool).toBe('select')
    expect(r.store.getState().drawing).toBe(0)
    expect(r.mode.report()!.draw.picks).toEqual([])
  })

  it('the Center rectangle again goes to Select too, and Rectangle from it is the other tool', () => {
    r.store.chooseTool('center_rectangle')
    r.store.chooseTool('rectangle')
    expect(r.store.getState().tool).toBe('rectangle')
    r.store.chooseTool('rectangle')
    expect(r.store.getState().tool).toBe('select')
  })

  it('Select with Select on changes nothing but starts over, and Delete and Construct are not tools', () => {
    const seq = r.store.getState().toolSeq
    r.store.chooseTool('select')
    expect(r.store.getState().tool).toBe('select')
    expect(r.store.getState().toolSeq).toBe(seq + 1)
    r.store.chooseTool('line')
    r.store.chooseTool('delete')
    r.store.chooseTool('construct')
    expect(r.store.getState().tool).toBe('line')
  })

  it('Esc still takes two steps: end the shape, then leave the tool', () => {
    r.store.chooseTool('line')
    r.stroke(r.at(A))
    expect(r.store.stepBack()).toBe(true)
    expect(r.store.getState().tool).toBe('line')
    expect(r.store.stepBack()).toBe(true)
    expect(r.store.getState().tool).toBe('select')
  })
})
