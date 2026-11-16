import type { BoxDrag } from '../input/pointer'
import './selection.css'

/** The badge sits this far right of and below the cursor (the desktop's `_update_count_badge`). */
export const BADGE_OFFSET = { x: 14, y: 12 } as const

/**
 * What Desktop mode draws over the view for the mouse (I9.9): the selection box while it is dragged (blue and solid left
 * to right, green and dashed right to left, as boxsel.draw_box), and the count badge beside the cursor when more than one
 * item is selected. Plain DOM over the canvas that never takes a pointer event; the pointer module tells it where the
 * mouse is (it adds no listener of its own).
 */
export class MouseOverlay {
  private readonly box: HTMLDivElement
  private readonly badge: HTMLDivElement
  private at: { x: number; y: number } | null = null
  private count = 0
  private on = false

  constructor(parent: HTMLElement) {
    this.box = document.createElement('div')
    this.box.className = 'select-box'
    this.box.dataset['testid'] = 'select-box'
    this.box.hidden = true
    this.badge = document.createElement('div')
    this.badge.className = 'select-count-badge'
    this.badge.dataset['testid'] = 'select-count-badge'
    this.badge.hidden = true
    parent.append(this.box, this.badge)
  }

  /** Desktop mode and a view whose selection the badge counts (not an open sketch, as on the desktop). */
  setEnabled(on: boolean): void {
    this.on = on
    if (!on) this.showBox(null)
    this.paintBadge()
  }

  /** The box being dragged, or null when none is. */
  showBox(b: BoxDrag | null): void {
    if (b === null) {
      this.box.hidden = true
      return
    }
    const s = this.box.style
    s.left = `${Math.min(b.x0, b.x1)}px`
    s.top = `${Math.min(b.y0, b.y1)}px`
    s.width = `${Math.abs(b.x1 - b.x0)}px`
    s.height = `${Math.abs(b.y1 - b.y0)}px`
    if (b.x1 < b.x0) this.box.dataset['crossing'] = ''
    else delete this.box.dataset['crossing']
    this.box.hidden = false
  }

  /** Where the mouse is over the view, or null when it has left it. */
  cursor(p: { x: number; y: number } | null): void {
    this.at = p
    this.paintBadge()
  }

  setCount(n: number): void {
    this.count = n
    this.paintBadge()
  }

  private paintBadge(): void {
    const show = this.on && this.count > 1 && this.at !== null
    this.badge.hidden = !show
    if (!show) return
    this.badge.textContent = String(this.count)
    this.badge.style.left = `${this.at!.x + BADGE_OFFSET.x}px`
    this.badge.style.top = `${this.at!.y + BADGE_OFFSET.y}px`
  }

  dispose(): void {
    this.box.remove()
    this.badge.remove()
  }
}
