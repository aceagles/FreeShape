import type { HandleHandler, PenPoint } from '../input/pointer'
import { PointerDrag } from './drag'
import type { DragView } from './drag'
import type { P, ScreenLayout } from './layout'
import type { SketchHit } from './pick'
import { isMovable } from './select'
import type { DragTarget } from './select'
import type { SketchStore } from './store'

/** The round Drag handle of the layout mock's sketch state: radius 22, 44 px up and right of the item, a dashed line to it. */
export const HANDLE = { radius: 22, offset: 44, edge: 28 } as const
/** A press this close to a handle's centre takes it, by pointer: the circle plus the finger's slop. */
const HIT: Readonly<Record<'pen' | 'mouse' | 'touch', number>> = { pen: 26, mouse: 24, touch: 30 }
/** The handle starts a drag after this many pixels of travel (a press and lift alone does nothing). */
const HANDLE_SLOP = 3

export interface HandleSpot {
  hit: SketchHit & DragTarget
  /** Where the item is on screen, and where its handle is. */
  anchor: P
  x: number
  y: number
}

function anchorOf(layout: ScreenLayout, hit: SketchHit): P | null {
  if (hit.kind === 'point') return layout.points.find((p) => p.id === hit.id) ?? null
  if (hit.kind === 'dimension') {
    const l = layout.dims.find((d) => d.id === hit.id)?.label
    return l ? { x: l.x + l.w / 2, y: l.y + l.h / 2 } : null
  }
  const s = layout.strokes.find((q) => q.id === hit.id)
  if (!s) return null
  // three quarters along a line (its middle is where the constraint glyphs sit), and for a curve the point halfway round
  if (s.pts.length === 2) return { x: s.pts[0]!.x + (s.pts[1]!.x - s.pts[0]!.x) * 0.75, y: s.pts[0]!.y + (s.pts[1]!.y - s.pts[0]!.y) * 0.75 }
  return s.pts[Math.floor(s.pts.length / 2)] ?? null
}

/**
 * The Drag handles of the selected items that can be moved, on screen. A handle sits up and to the right of its item,
 * or down and to the left of it where that would leave the view.
 */
export function handleSpots(layout: ScreenLayout, selected: readonly SketchHit[], sketch: Parameters<typeof isMovable>[0], width: number): HandleSpot[] {
  const out: HandleSpot[] = []
  for (const h of selected) {
    if (!isMovable(sketch, h)) continue
    const a = anchorOf(layout, h)
    if (!a) continue
    const right = a.x + HANDLE.offset <= width - HANDLE.edge
    const up = a.y - HANDLE.offset >= HANDLE.edge
    out.push({ hit: h, anchor: a, x: right ? a.x + HANDLE.offset : a.x - HANDLE.offset, y: up ? a.y - HANDLE.offset : a.y + HANDLE.offset })
  }
  return out
}

const n = (v: number): string => String(Math.round(v * 100) / 100)
const BLUE = '#155bb0'

/** The handles as SVG markup for the sketch layer; `grabbed` is the one being dragged. */
export function handleMarkup(spots: readonly HandleSpot[], grabbed: SketchHit | null = null): string {
  return spots
    .map((s) => {
      const dx = s.x - s.anchor.x
      const dy = s.y - s.anchor.y
      const l = Math.hypot(dx, dy) || 1
      const u = { x: dx / l, y: dy / l }
      const from = { x: s.anchor.x + u.x * 9, y: s.anchor.y + u.y * 9 }
      const to = { x: s.x - u.x * (HANDLE.radius + 2), y: s.y - u.y * (HANDLE.radius + 2) }
      const down = grabbed !== null && grabbed.kind === s.hit.kind && grabbed.id === s.hit.id
      return (
        `<g data-handle="${s.hit.kind}" data-id="${s.hit.id.replace(/"/g, '&quot;')}">` +
        `<path d="M${n(from.x)} ${n(from.y)}L${n(to.x)} ${n(to.y)}" fill="none" stroke="${BLUE}" stroke-width="1.5" stroke-dasharray="3 4"/>` +
        `<circle cx="${n(s.x)}" cy="${n(s.y)}" r="${HANDLE.radius}" fill="${down ? '#dbe8f9' : '#ffffff'}" stroke="${BLUE}" stroke-width="2.5"/>` +
        `<path d="M0-13v26M-13 0h26M-4-9l4-4 4 4M-4 9l4 4 4-4M-9-4l-4 4 4 4M9-4l4 4-4 4" transform="translate(${n(s.x)} ${n(s.y)})" fill="none" stroke="${BLUE}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></g>`
      )
    })
    .join('')
}

/** What the Drag handles need of the sketch mode that draws them. */
export interface HandleHost extends DragView {
  /** The handles on screen now. */
  spots(): readonly HandleSpot[]
}

/**
 * The Drag handles' pointer side (the pointer module's one handle hook): a press on a handle, by finger, pen or mouse,
 * takes the pointer before selection and navigation; dragging moves the handle's item. The press is the handle's until
 * the pointer lifts.
 */
export class SketchHandles implements HandleHandler {
  private readonly drag: PointerDrag
  /** The handle held, for its look. */
  grabbed: SketchHit | null = null

  constructor(
    store: SketchStore,
    private readonly host: HandleHost,
    private readonly changed: () => void,
  ) {
    this.drag = new PointerDrag(store, host)
  }

  press(p: PenPoint, pointer: 'pen' | 'mouse' | 'touch' = 'touch'): boolean {
    let best: HandleSpot | null = null
    let bestD = HIT[pointer]
    for (const s of this.host.spots()) {
      const d = Math.hypot(s.x - p.x, s.y - p.y)
      if (d <= bestD) {
        best = s
        bestD = d
      }
    }
    if (!best || !this.drag.arm(best.hit, p.x, p.y, HANDLE_SLOP)) return false
    this.grabbed = best.hit
    this.changed()
    return true
  }

  move(p: PenPoint): void {
    this.drag.move(p.x, p.y)
  }

  release(p: PenPoint): void {
    this.drag.release(p.x, p.y)
    this.done()
  }

  cancel(): void {
    this.drag.cancel()
    this.done()
  }

  private done(): void {
    this.grabbed = null
    this.changed()
  }
}
