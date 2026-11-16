import { Vector3 } from 'three'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PointerModule } from '../input/pointer'
import { fixture } from '../test/fixtures'
import { studioBoxHits } from '../viewport/boxSelect'
import { ViewCamera, STANDARD_VIEWS } from '../viewport/camera'
import { PickIndex } from '../viewport/pick'
import { buildScene } from '../viewport/scene'
import { TabView } from '../viewport/visibility'
import { wireSelection } from './aimCommit'
import { DEFAULT_FILTER, filterKinds, isDefaultFilter } from './filter'
import { partItem, SelectionStore } from './store'
import type { SelectionItem } from './store'

// I9.14: the Select menu's filter reaches every pick and the box; a dialog's field still decides; a mouse double click reaches
// the selection's handler in Desktop mode only. The Bracket with a real picker and a fixed camera (1024 x 768, iso, fitted).

function rig(desktop = true) {
  const { outputs } = fixture('bracket')
  const built = buildScene(outputs)
  const view = new ViewCamera()
  view.resize(1024, 768)
  view.setBack(STANDARD_VIEWS['iso'])
  view.fit(built.fitBounds)
  const index = new PickIndex(outputs, built)
  const fit = vi.fn()
  const host = { view, pickPoint: () => null, modelCentre: () => new Vector3(), fit, requestRender: vi.fn() }
  const canvas = document.createElement('canvas')
  canvas.setPointerCapture = (() => {}) as unknown as typeof canvas.setPointerCapture
  document.body.append(canvas)
  const pointer = new PointerModule(canvas, host)
  pointer.setDesktop(desktop)
  const selection = new SelectionStore()
  wireSelection(pointer, selection, (x, y, kind, filter) => index.pick(view, x, y, kind, filter), {
    hits: (b, filter) => studioBoxHits(outputs, new Set(index.partIds), view, b, filter),
    draw: () => {},
  })
  const fire = (type: string, init: PointerEventInit) => canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, ...init }))
  return { canvas, view, outputs, selection, pointer, index, fire, fit }
}

let r: ReturnType<typeof rig>
beforeEach(() => {
  r = rig()
})
afterEach(() => {
  r.pointer.dispose()
  r.canvas.remove()
})

const mouse = (x: number, y: number): PointerEventInit => ({ pointerId: 20, pointerType: 'mouse', clientX: x, clientY: y, button: 0 })
const click = (x: number, y: number) => {
  r.fire('pointermove', mouse(x, y))
  r.fire('pointerdown', mouse(x, y))
  r.fire('pointerup', mouse(x, y))
}
/** What the Desktop-mode pick finds at every spot of a grid, with the store's current filter. */
function grid(): { x: number; y: number; kind: string | null }[] {
  const out = []
  for (let y = 10; y < 768; y += 12) for (let x = 10; x < 1024; x += 12) out.push({ x, y, kind: r.index.pick(r.view, x, y, 'mouse', r.selection.pickFilter)?.kind ?? null })
  return out
}
const desktopFilter = (patch: Partial<typeof DEFAULT_FILTER> = {}) => r.selection.setUserFilter(filterKinds({ ...DEFAULT_FILTER, ...patch }))

describe('the Select filter', () => {
  it('has the desktop defaults, and Reset to default restores them', () => {
    expect(DEFAULT_FILTER).toEqual({ vertex: false, edge: true, face: true, part: false, sketch: true, plane: true, axis: true })
    const tab = new TabView()
    expect(isDefaultFilter(tab.getState().select)).toBe(true)
    tab.toggleSelect('face')
    tab.toggleSelect('vertex')
    expect(tab.getState().select).toMatchObject({ face: false, vertex: true })
    expect(isDefaultFilter(tab.getState().select)).toBe(false)
    tab.resetSelect()
    expect(tab.getState().select).toEqual(DEFAULT_FILTER)
  })

  it('is kept per tab: another tab has its own', () => {
    const a = new TabView()
    const b = new TabView()
    a.toggleSelect('edge')
    expect(b.getState().select.edge).toBe(true)
    expect(a.getState().select.edge).toBe(false)
  })

  it('with Faces unticked a click never picks a face', () => {
    expect(grid().some((g) => g.kind === 'face')).toBe(true)
    desktopFilter({ face: false })
    const found = grid()
    expect(found.some((g) => g.kind === 'face')).toBe(false)
    expect(found.some((g) => g.kind === 'edge')).toBe(true)
    // and a click on a spot that was a face selects nothing
    desktopFilter()
    const spot = grid().find((g) => g.kind === 'face')!
    desktopFilter({ face: false })
    click(spot.x, spot.y)
    expect(r.selection.getState().items.filter((i) => i.kind === 'face')).toEqual([])
  })

  /** A vertex of the Bracket in view, and where it is on screen. */
  function vertexSpot(): { x: number; y: number } {
    const vertex = grid()
      .map((g) => r.index.pick(r.view, g.x, g.y, 'mouse', ['vertex']))
      .find((h) => h?.kind === 'vertex')!
    expect(vertex).toBeDefined()
    return r.view.project([vertex.point[0], vertex.point[1], vertex.point[2]])
  }

  it('Vertices is off by default, so a click on a vertex picks the edge', () => {
    const at = vertexSpot()
    desktopFilter()
    click(at.x, at.y)
    expect(r.selection.getState().items.map((i) => i.kind)).toEqual(['edge'])
  })

  it('with Vertices ticked a click on a vertex picks the vertex', () => {
    const at = vertexSpot()
    desktopFilter({ vertex: true })
    click(at.x, at.y)
    expect(r.selection.getState().items.map((i) => i.kind)).toEqual(['vertex'])
  })

  it('a box takes edges only with Faces unticked, and the vertices only when ticked', () => {
    const box = { x0: 0, y0: 0, x1: 1024, y1: 768 }
    desktopFilter({ face: false })
    const kinds = new Set(r.selection.pickFilter && studioBoxHits(r.outputs, new Set(r.index.partIds), r.view, box, r.selection.pickFilter).map((i) => i.kind))
    expect([...kinds]).toEqual(['edge'])
    desktopFilter({ vertex: true, face: false })
    const more = new Set(studioBoxHits(r.outputs, new Set(r.index.partIds), r.view, box, r.selection.pickFilter!).map((i) => i.kind))
    expect(more).toEqual(new Set(['vertex', 'edge']))
  })

  it('an open dialog field that wants edges takes an edge with Edges unticked in the filter', () => {
    desktopFilter({ edge: false })
    expect(r.selection.pickFilter).not.toContain('edge')
    r.selection.enterDialog()
    r.selection.setPickFilter(['edge'])
    expect(r.selection.pickFilter).toEqual(['edge'])
    const picked: (SelectionItem | null)[] = []
    r.selection.setFieldPicker((i) => void picked.push(i))
    const spot = grid().find((g) => g.kind === 'edge')!
    click(spot.x, spot.y)
    expect(picked).toHaveLength(1)
    expect(picked[0]?.kind).toBe('edge')
    // the dialog over, the menu's filter is the pick's filter again
    r.selection.leaveDialog()
    expect(r.selection.pickFilter).not.toContain('edge')
  })

  it('drops the selected items a tick took away, and keeps a part the Parts list selected when the mode brings the filter', () => {
    r.selection.set([partItem(r.outputs.parts[0]!.id)])
    desktopFilter()
    expect(r.selection.getState().items).toHaveLength(1)
    desktopFilter({ edge: false })
    expect(r.selection.getState().items).toHaveLength(1)
    r.selection.clear()
    const spot = grid().find((g) => g.kind === 'face')!
    click(spot.x, spot.y)
    expect(r.selection.getState().items).toHaveLength(1)
    desktopFilter({ edge: false })
    expect(r.selection.getState().items).toHaveLength(1)
    desktopFilter({ face: false })
    expect(r.selection.getState().items).toHaveLength(0)
  })
})

describe('a double click', () => {
  const double = (x: number, y: number) => {
    click(x, y)
    r.fire('pointerdown', mouse(x, y))
    r.fire('pointerup', mouse(x, y))
  }

  it('in Desktop mode tells the selection what is under it, once, and the second click does not toggle', () => {
    const got: SelectionItem[] = []
    r.selection.setDoubleClickHandler((i) => void got.push(i))
    const spot = grid().find((g) => g.kind === 'face')!
    double(spot.x, spot.y)
    expect(got.map((i) => i.kind)).toEqual(['face'])
    // the first click selected the face and the second did not toggle it off (the desktop's second press is the double click)
    expect(r.selection.getState().items.map((i) => i.kind)).toEqual(['face'])
    // a third press is a new click
    click(spot.x, spot.y)
    expect(r.selection.getState().items).toHaveLength(0)
  })

  it('on empty space tells nothing; two clicks far apart are not a double click', () => {
    const got: SelectionItem[] = []
    r.selection.setDoubleClickHandler((i) => void got.push(i))
    const empty = grid().find((g) => g.kind === null)!
    double(empty.x, empty.y)
    expect(got).toEqual([])
    const faces = grid().filter((g) => g.kind === 'face')
    click(faces[0]!.x, faces[0]!.y)
    click(faces[faces.length - 1]!.x, faces[faces.length - 1]!.y)
    expect(got).toEqual([])
  })

  it('uses the filter: with Faces unticked a double click on a face tells nothing', () => {
    const got: SelectionItem[] = []
    r.selection.setDoubleClickHandler((i) => void got.push(i))
    const spot = grid().find((g) => g.kind === 'face')!
    desktopFilter({ face: false })
    double(spot.x, spot.y)
    expect(got.filter((i) => i.kind === 'face')).toEqual([])
  })

  it('is not made in Tablet mode, where a double tap fits the view', () => {
    r.pointer.dispose()
    r.canvas.remove()
    r = rig(false)
    const got: SelectionItem[] = []
    r.selection.setDoubleClickHandler((i) => void got.push(i))
    const spot = grid().find((g) => g.kind === 'face')!
    click(spot.x, spot.y)
    click(spot.x, spot.y)
    expect(got).toEqual([])
    const tap = () => {
      const init = { pointerId: 5, pointerType: 'touch', clientX: spot.x, clientY: spot.y, isPrimary: true }
      r.fire('pointerdown', init)
      r.fire('pointerup', init)
    }
    tap()
    tap()
    expect(r.fit).toHaveBeenCalledTimes(1)
    expect(got).toEqual([])
  })
})
