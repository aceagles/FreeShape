import type { HoldHandler, PenPoint } from '../input/pointer'
import { size } from '../theme/tokens'
import { inlinePaths } from '../tools/inlinePaths'
import type { ToolState } from '../tools/useToolTypes'
import type { SketchStore } from './store'

/** The ring's buttons, clockwise from the top, as the layout mock's script lists them. */
export const QUICK_TOOLS: readonly { id: string; label: string }[] = [
  { id: 'line', label: 'Line' },
  { id: 'rectangle', label: 'Rectangle' },
  { id: 'circle', label: 'Circle' },
  { id: 'arc', label: 'Arc' },
  { id: 'dimension', label: 'Dimension' },
  { id: 'point', label: 'Point' },
  { id: 'select', label: 'Select' },
  { id: 'construct', label: 'Construct' },
]

export interface QuickMenuHost {
  /** The element the ring is added to (the canvas's parent); positions are CSS pixels from its top left, the view's. */
  readonly overlayParent: HTMLElement
  width(): number
  height(): number
}

const SVG = 'http://www.w3.org/2000/svg'
const { width: BW, height: BH } = size.quickMenuButton
const RADIUS = size.quickMenuRadius

/** Where button `i` sits relative to the ring's centre: its top left corner. */
const cornerOf = (i: number): { x: number; y: number } => {
  const a = (i * Math.PI) / 4
  return { x: Math.round(RADIUS * Math.sin(a) - BW / 2), y: Math.round(-RADIUS * Math.cos(a) - BH / 2) }
}

/** Which button a slide of (dx, dy) from the press points at: -1 inside the dead zone. */
export function sectorOf(dx: number, dy: number): number {
  if (Math.hypot(dx, dy) < size.quickMenuDeadZone) return -1
  return (Math.round(Math.atan2(dx, -dy) / (Math.PI / 4)) + 8) % 8
}

/**
 * The ring's centre for a press at `x`, `y`: the press itself, moved inward so every button is inside a view of this size
 * (a view too small to hold the ring is centred).
 */
export function ringCentre(x: number, y: number, width: number, height: number): { x: number; y: number } {
  const hx = RADIUS + BW / 2
  const hy = RADIUS + BH / 2
  const clamp = (v: number, half: number, span: number) => (span < 2 * half ? span / 2 : Math.min(Math.max(v, half), span - half))
  return { x: clamp(x, hx, width), y: clamp(y, hy, height) }
}

/**
 * The sketch quick menu (I7.8): a press with the pen or a finger that stays within 10 px for 0.4 s opens a ring of eight tool
 * buttons round that point (the pointer module times the hold). Sliding towards a button and lifting picks it; lifting without
 * sliding leaves the ring open for a tap on a button; a press elsewhere closes it. A button for a tool a later card builds is
 * dimmed and does nothing. The menu is made by sketch mode, so it exists only in a sketch.
 */
export class QuickMenu implements HoldHandler {
  private root: HTMLDivElement | null = null
  private press = { x: 0, y: 0 }
  private aimed = -1
  private readonly off: () => void

  constructor(
    private readonly host: QuickMenuHost,
    private readonly store: SketchStore,
    private readonly stateOf: (id: string) => ToolState,
  ) {
    this.off = store.subscribe(() => {
      if (this.root && !this.store.getState().open) this.close()
    })
  }

  dispose(): void {
    this.off()
    this.close()
  }

  isOpen(): boolean {
    return this.root !== null
  }

  open(x: number, y: number): boolean {
    if (!this.store.getState().open) return false
    this.close()
    this.press = { x, y }
    this.aimed = -1
    const c = ringCentre(x, y, this.host.width(), this.host.height())
    const root = document.createElement('div')
    root.className = 'quick-menu'
    root.dataset['testid'] = 'quick-menu'
    root.style.left = `${c.x}px`
    root.style.top = `${c.y}px`
    const disc = document.createElement('div')
    disc.className = 'quick-disc'
    const dd = size.quickMenuDisc
    disc.style.cssText = `left:${-dd / 2}px;top:${-dd / 2}px;width:${dd}px;height:${dd}px`
    const hub = document.createElement('div')
    hub.className = 'quick-hub'
    const hh = size.quickMenuHub
    // the hub marks the press, which is not the ring's centre when the ring was moved inward
    hub.style.cssText = `left:${x - c.x - hh / 2}px;top:${y - c.y - hh / 2}px;width:${hh}px;height:${hh}px`
    root.append(disc, hub)
    QUICK_TOOLS.forEach((t, i) => {
      const b = document.createElement('button')
      b.type = 'button'
      b.dataset['tool'] = t.id
      b.dataset['testid'] = `quick-${t.id}`
      if (this.stateOf(t.id) !== 'ok') b.setAttribute('aria-disabled', 'true')
      const p = cornerOf(i)
      b.style.cssText = `left:${p.x}px;top:${p.y}px;width:${BW}px;height:${BH}px`
      const svg = document.createElementNS(SVG, 'svg')
      svg.setAttribute('width', String(size.icon))
      svg.setAttribute('height', String(size.icon))
      svg.setAttribute('viewBox', '0 0 24 24')
      svg.setAttribute('fill', 'none')
      svg.setAttribute('stroke', 'currentColor')
      svg.setAttribute('stroke-width', '1.8')
      svg.setAttribute('stroke-linecap', 'round')
      svg.setAttribute('stroke-linejoin', 'round')
      const path = document.createElementNS(SVG, 'path')
      path.setAttribute('d', inlinePaths[t.id] ?? '')
      svg.append(path)
      const label = document.createElement('span')
      label.textContent = t.label
      b.append(svg, label)
      b.addEventListener('click', () => this.pick(i))
      root.append(b)
    })
    this.host.overlayParent.append(root)
    this.root = root
    return true
  }

  move(p: PenPoint): void {
    this.aim(sectorOf(p.x - this.press.x, p.y - this.press.y))
  }

  release(p: PenPoint): void {
    const i = sectorOf(p.x - this.press.x, p.y - this.press.y)
    if (i >= 0) this.pick(i)
    else this.aim(-1) // no slide: the ring stays for a tap on a button
  }

  cancel(): void {
    this.close()
  }

  dismiss(): void {
    this.close()
  }

  private aim(i: number): void {
    if (i === this.aimed || !this.root) return
    this.aimed = i
    this.root.querySelectorAll('button').forEach((b, k) => {
      if (k === i && b.getAttribute('aria-disabled') !== 'true') b.setAttribute('data-aimed', '')
      else b.removeAttribute('data-aimed')
    })
  }

  private pick(i: number): void {
    const t = QUICK_TOOLS[i]!
    const ok = this.stateOf(t.id) === 'ok'
    this.close()
    if (ok && this.store.getState().tool !== t.id) this.store.chooseTool(t.id)
  }

  private close(): void {
    this.root?.remove()
    this.root = null
    this.aimed = -1
  }
}
