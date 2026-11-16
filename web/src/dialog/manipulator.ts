import type { HandleHandler, PenPoint } from '../input/pointer'
import type { ViewCamera } from '../viewport/camera'
import type { Manipulator } from '../wire'

/** Where a drag arrow sends its value and where a tap on its label goes: the open feature dialog registers itself here. */
export interface ManipulatorSink {
  /** Send these values as one dialog update (latest position wins while one is in flight). */
  set(values: Record<string, unknown>): void
  /** Open the pad on this field (a tap on the value label). */
  edit(field: string): void
}

let current: ManipulatorSink | null = null
/** The open feature dialog registers here; `null` removes it. Only one dialog is open at a time. */
export function setManipulatorSink(sink: ManipulatorSink | null): void {
  current = sink
}

/** Screen size of the round Drag handle (radius, as in the approved layout) and the gap from the arrow head to its centre. */
const HANDLE_RADIUS = 22
const HANDLE_GAP = 26
/** A press this close to the handle's centre takes it: the finger's slop on top of the circle. */
const HIT_RADIUS = 30
const LABEL_GAP = 32
const LABEL_HEIGHT = 28
const LABEL_SLOP = 6
/** A press that moves less than this (px) before it lifts is a tap. */
const TAP_SLOP = 6
const SVG = 'http://www.w3.org/2000/svg'
const BLUE = '#155bb0'

/** The snap step the desktop picks from its zoom (`_snap_step`): about 6 px per step, rounded up to 1, 2, 5 or 10 times a power of ten. */
export function viewSnapStep(scale: number): number {
  const raw = scale * 6
  const mag = 10 ** Math.floor(Math.log10(Math.max(raw, 1e-6)))
  for (const m of [1, 2, 5, 10]) if (m * mag >= raw) return m * mag
  return mag * 10
}

/** The step of a manipulator's snap rule: "view" follows the zoom, a number is a fixed step, anything else does not snap. */
export function snapStep(snap: string, scale: number): number {
  if (snap === 'view') return viewSnapStep(scale)
  const n = Number(snap)
  return Number.isFinite(n) && n > 0 ? n : 0
}

const round6 = (v: number): number => Math.round(v * 1e6) / 1e6
const fmt = (v: number): string => String(Number(v.toFixed(4)))

interface Screen {
  base: { x: number; y: number }
  tip: { x: number; y: number }
  /** Unit screen direction the arrow points in. */
  dir: { x: number; y: number }
  /** Screen pixels per model unit along `dir`, signed as the arrow's own axis (not flipped by the value's sign). */
  axis: { x: number; y: number }
}

interface Item {
  m: Manipulator
  value: number
  label: string
}

interface Press {
  index: number
  part: 'handle' | 'label'
  id: number
  start: { x: number; y: number }
  /** Parameter at the press minus the value: the grab does not jump the arrow. */
  offset: number
  moved: boolean
}

/**
 * The drag arrows of a preview, drawn as an SVG over the view and dragged through the pointer module (it gives the
 * press to this handler before selection and navigation). A drag moves the value along the arrow's direction on
 * screen; each move sends the values as one dialog update and the release sends the final one. Tapping the label
 * opens the pad on the field.
 */
export class ManipulatorLayer implements HandleHandler {
  private items: Item[] = []
  private grab: Press | null = null
  private readonly svg: SVGSVGElement

  constructor(
    parent: HTMLElement,
    private readonly view: ViewCamera,
    private readonly redraw: () => void = () => {},
    private readonly sink: () => ManipulatorSink | null = () => current,
  ) {
    this.svg = document.createElementNS(SVG, 'svg')
    this.svg.setAttribute('class', 'viewport-manipulators')
    this.svg.setAttribute('data-testid', 'manipulators')
    this.svg.setAttribute('aria-hidden', 'true')
    parent.append(this.svg)
  }

  /** The arrows of the outputs on screen. The arrow being dragged keeps the value the drag gave it. */
  set(list: readonly Manipulator[]): void {
    const dragging = this.grab?.part === 'handle' ? this.grab.index : -1
    this.items = list.map((m, i) => (i === dragging && this.items[i] ? { ...this.items[i]!, m } : { m, value: m.value, label: m.label }))
    if (this.grab && this.grab.index >= this.items.length) this.grab = null
    this.update()
  }

  clear(): void {
    this.grab = null
    this.items = []
    this.update()
  }

  dispose(): void {
    this.svg.remove()
  }

  /** The value arrow `i` shows now. */
  valueOf(i: number): number | null {
    return this.items[i]?.value ?? null
  }

  private screen(it: Item): Screen | null {
    const { m } = it
    const o = this.view.project(m.origin)
    const tip = this.view.project([m.origin[0] + m.dir[0] * it.value, m.origin[1] + m.dir[1] * it.value, m.origin[2] + m.dir[2] * it.value])
    const one = this.view.project([m.origin[0] + m.dir[0], m.origin[1] + m.dir[1], m.origin[2] + m.dir[2]])
    const axis = { x: one.x - o.x, y: one.y - o.y }
    const n = Math.hypot(axis.x, axis.y)
    // an arrow that points along the line of sight has no direction to drag along
    const unit = n > 1e-9 ? { x: axis.x / n, y: axis.y / n } : { x: 0, y: -1 }
    const sign = it.value < 0 ? -1 : 1
    return { base: o, tip, dir: { x: unit.x * sign, y: unit.y * sign }, axis }
  }

  private handleCentre(s: Screen): { x: number; y: number } {
    return { x: s.tip.x + s.dir.x * HANDLE_GAP, y: s.tip.y + s.dir.y * HANDLE_GAP }
  }

  private labelBox(s: Screen, text: string): { x: number; y: number; w: number; h: number } {
    const c = this.handleCentre(s)
    const w = Math.max(66, text.length * 8 + 16)
    const right = c.x + LABEL_GAP
    // beside the handle, on the other side when it would leave the view
    const x = right + w > this.view.width - 8 ? c.x - LABEL_GAP - w : right
    return { x, y: c.y - LABEL_HEIGHT / 2, w, h: LABEL_HEIGHT }
  }

  /** The arrow and the part (handle or label) under a view position, or null. The handle wins where they overlap. */
  hit(x: number, y: number): { index: number; part: 'handle' | 'label' } | null {
    for (let i = 0; i < this.items.length; i++) {
      const s = this.screen(this.items[i]!)
      if (!s) continue
      const c = this.handleCentre(s)
      if (Math.hypot(c.x - x, c.y - y) <= HIT_RADIUS) return { index: i, part: 'handle' }
      const b = this.labelBox(s, this.items[i]!.label)
      if (x >= b.x - LABEL_SLOP && x <= b.x + b.w + LABEL_SLOP && y >= b.y - LABEL_SLOP && y <= b.y + b.h + LABEL_SLOP) return { index: i, part: 'label' }
    }
    return null
  }

  /** How far along the arrow (model units) the view position is: the closest point of the arrow's line to it. */
  private param(s: Screen, x: number, y: number): number | null {
    const dd = s.axis.x * s.axis.x + s.axis.y * s.axis.y
    if (dd < 1e-9) return null
    return ((x - s.base.x) * s.axis.x + (y - s.base.y) * s.axis.y) / dd
  }

  press(p: PenPoint): boolean {
    if (this.grab) return false
    const h = this.hit(p.x, p.y)
    if (!h) return false
    const it = this.items[h.index]!
    const s = this.screen(it)!
    const t = this.param(s, p.x, p.y)
    this.grab = { index: h.index, part: h.part, id: p.pointerId, start: { x: p.x, y: p.y }, offset: t === null ? 0 : t - it.value, moved: false }
    this.update()
    return true
  }

  move(p: PenPoint): void {
    const pr = this.grab
    if (!pr || pr.id !== p.pointerId) return
    if (!pr.moved && Math.hypot(p.x - pr.start.x, p.y - pr.start.y) <= TAP_SLOP) return
    pr.moved = true
    if (pr.part !== 'handle') return
    const it = this.items[pr.index]
    const s = it && this.screen(it)
    if (!it || !s) return
    const t = this.param(s, p.x, p.y)
    if (t === null) return
    const v = this.limit(it, t - pr.offset)
    if (v !== it.value) {
      it.value = v
      this.send(it)
      this.update()
      this.redraw()
    }
  }

  release(p: PenPoint): void {
    const pr = this.grab
    if (!pr || pr.id !== p.pointerId) return
    this.grab = null
    const it = this.items[pr.index]
    if (it && pr.moved && pr.part === 'handle') this.send(it)
    else if (it && !pr.moved && pr.part === 'label') this.sink()?.edit(it.m.field)
    this.update()
  }

  cancel(): void {
    // the drag stops where it is: what was sent stays, the next preview says what the model made of it
    this.grab = null
    this.update()
  }

  /** Snap and floor a value (the desktop's rule): round to the step, keep |v| at least `min`, never exactly zero. */
  private limit(it: Item, raw: number): number {
    const step = snapStep(it.m.snap, this.view.scale)
    let v = step > 0 ? Math.round(raw / step) * step : raw
    if (it.m.min !== null && Math.abs(v) < it.m.min) v = (v < 0 ? -1 : 1) * it.m.min
    if (Math.abs(v) < 1e-6) v = it.m.value >= 0 ? 1e-3 : -1e-3
    return v
  }

  private send(it: Item): void {
    const { m } = it
    const size = round6(Math.abs(it.value) * (m.symmetric ? 2 : 1))
    const values: Record<string, unknown> = { [m.field]: size }
    if (!m.symmetric && m.flip !== null) values[m.flip] = it.value < 0
    const unit = /\s*([^\s\d.-][^\s]*)$/.exec(m.label)?.[1]
    it.label = unit ? `${fmt(size)} ${unit}` : fmt(size)
    this.sink()?.set(values)
  }

  /** Redraw the arrows at the camera's current place. */
  update(): void {
    const svg = this.svg
    svg.setAttribute('width', String(this.view.width))
    svg.setAttribute('height', String(this.view.height))
    svg.replaceChildren()
    this.items.forEach((it, i) => {
      const s = this.screen(it)
      if (!s) return
      const active = this.grab?.index === i
      const q = { x: -s.dir.y, y: s.dir.x }
      const head = (k: number, w: number) => `${(s.tip.x - s.dir.x * k + q.x * w).toFixed(1)} ${(s.tip.y - s.dir.y * k + q.y * w).toFixed(1)}`
      const g = el('g', { 'data-manipulator': it.m.field })
      g.append(
        el('path', { d: `M${s.base.x.toFixed(1)} ${s.base.y.toFixed(1)}L${s.tip.x.toFixed(1)} ${s.tip.y.toFixed(1)} M${head(14, -10)}L${s.tip.x.toFixed(1)} ${s.tip.y.toFixed(1)}L${head(14, 10)}`, fill: 'none', stroke: BLUE, 'stroke-width': '3', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }),
      )
      const c = this.handleCentre(s)
      const turn = (Math.atan2(s.dir.y, s.dir.x) * 180) / Math.PI + 90
      g.append(
        el('circle', { cx: c.x.toFixed(1), cy: c.y.toFixed(1), r: String(HANDLE_RADIUS), fill: active ? '#dbe8f9' : '#ffffff', stroke: BLUE, 'stroke-width': '2.5', 'data-part': 'handle' }),
        el('path', { d: 'M0-12V12M-8-5L0-13L8-5M-8 5L0 13L8 5', fill: 'none', stroke: BLUE, 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', transform: `translate(${c.x.toFixed(1)} ${c.y.toFixed(1)}) rotate(${turn.toFixed(1)})` }),
      )
      const b = this.labelBox(s, it.label)
      const text = el('text', { x: (b.x + b.w / 2).toFixed(1), y: (b.y + 19).toFixed(1), fill: BLUE, 'text-anchor': 'middle', 'data-part': 'label' })
      text.textContent = it.label
      g.append(el('rect', { x: b.x.toFixed(1), y: b.y.toFixed(1), width: String(b.w), height: String(b.h), rx: '6', fill: '#ffffff', stroke: '#c4d9f3' }), text)
      svg.append(g)
    })
  }
}

function el(name: string, attrs: Record<string, string>): SVGElement {
  const e = document.createElementNS(SVG, name)
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v)
  return e
}
