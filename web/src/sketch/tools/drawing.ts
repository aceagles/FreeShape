import type { PenHandler, PenPoint } from '../../input/pointer'
import type { PickPointer } from '../../viewport/pick'
import { color } from '../../theme/tokens'
import type { P } from '../layout'
import type { SketchStore } from '../store'
import { sketchColor } from '../svg'
import type { Uv } from '../wire'
import { addBody, degenerate, isDrawTool, PICKS, rubberBand } from './shapes'
import type { DrawTool, Pick } from './shapes'
import { snapAim } from './snap'
import type { Aim, SnapContext } from './snap'

/** A pen that lifts further than this outside the view (CSS px) cancels the point it was aiming. */
export const OUTSIDE_MARGIN = 40

/** What the drawing tools need of the sketch mode that hosts them. */
export interface DrawHost {
  /** The snap context for a pointer of this kind and the view as it is now, or null when no sketch is on screen. */
  context(pointer: PickPointer): SnapContext | null
  width(): number
  height(): number
  requestRender(): void
  /** The layer the aim and the rubber band are drawn in. */
  readonly overlayParent: HTMLElement
}

/** What `window.__freeshapeViewport.sketch.draw` reports: the drawing tools' state, for tests that look at the page from outside. */
export interface DrawReport {
  tool: string
  /** The points committed so far for the shape in progress (a chained line's start included). */
  picks: { uv: Uv; snap: string | null }[]
  aim: { uv: Uv; at: P; snap: string | null; infer: 'H' | 'V' | null } | null
  /** `sketch.add` messages sent. */
  sent: number
}

const SNAP_TEXT: Record<string, string> = { point: 'Coincident', on: 'On curve', edge: 'On edge' }
const INFER_TEXT = { H: 'Horizontal', V: 'Vertical' } as const

/** How near (CSS px) a press must be to the last placed point to end the shape there: a mouse is exact, a pen less, a finger least. */
export const LAST_POINT_REACH: Record<PickPointer, number> = { mouse: 6, pen: 12, touch: 24 }

/** What the active tool asks for, shown at the foot of the view; a mouse has the keyboard and a right button, a pen or finger has neither. */
const HINT: Record<DrawTool, string> = {
  line: 'Line: press and lift to place each point. Tap the last point, or tap Line again, to stop.',
  rectangle: 'Rectangle: place two opposite corners. Tap Rectangle again for a centre rectangle.',
  center_rectangle: 'Centre rectangle: place the centre, then a corner. Tap Rectangle again for corners.',
  circle: 'Circle: place the centre, then a point on the circle.',
  arc: 'Arc: place the start, the end, then a point on the arc.',
  point: 'Point: place a point.',
}

const n = (v: number): string => String(Math.round(v * 100) / 100)

/**
 * The drawing tools of a sketch (I7.8). With a drawing tool on, the pen Aims while it is down (a mark follows it with the
 * snap it found, and the shape so far is rubber-banded) and Commits the point when it lifts. A finger places nothing. A
 * committed shape goes to the server as one `sketch.add` with the constraints its snaps imply; a line chains from point to
 * point until the tool is tapped again or the sketch is done. The mouse does the same, Aiming as it hovers.
 */
export class DrawTools {
  private picks: Pick[] = []
  private aim: Aim | null = null
  /** Sends go one at a time: a chained line's next start is the end the server made for the one before. */
  private queue: Promise<unknown> = Promise.resolve()
  private sent = 0
  private lastTool = ''
  private lastSeq = -1
  private lastOpen = -1
  private readonly layer: HTMLDivElement
  private lastMarkup = ''
  /** The kind of pointer that last Aimed, for the hint's words. */
  private lastKind: PickPointer = 'pen'
  private readonly off: () => void

  constructor(
    private readonly host: DrawHost,
    private readonly store: SketchStore,
  ) {
    this.layer = document.createElement('div')
    this.layer.className = 'sketch-aim-layer'
    this.layer.dataset['testid'] = 'sketch-aim-layer'
    host.overlayParent.append(this.layer)
    this.off = store.subscribe(() => this.onStore())
    this.onStore()
  }

  dispose(): void {
    this.off()
    this.layer.remove()
  }

  /** A drawing tool is on in an open sketch: the pen and the mouse place points, a finger tap does nothing. */
  get active(): boolean {
    const s = this.store.getState()
    return s.open && isDrawTool(s.tool)
  }

  private get tool(): DrawTool {
    return this.store.getState().tool as DrawTool
  }

  private onStore(): void {
    const s = this.store.getState()
    if (s.openSeq !== this.lastOpen || s.tool !== this.lastTool || s.toolSeq !== this.lastSeq || !s.open) {
      this.lastOpen = s.openSeq
      this.lastTool = s.tool
      this.lastSeq = s.toolSeq
      this.reset()
    }
  }

  /** Drop the shape in progress and the aim. */
  private reset(): void {
    this.picks = []
    this.aim = null
    this.store.setDrawing(0)
    this.host.requestRender()
  }

  /** The handler of a pointer kind: it draws when a drawing tool is on, and otherwise hands over to `other` (selection). */
  wrap(kind: 'pen' | 'mouse', other: PenHandler): PenHandler {
    return {
      aim: (p) => (this.active ? this.aimAt(p, kind) : other.aim(p)),
      commit: (p) => (this.active ? this.commitAt(p, kind) : other.commit(p)),
      cancel: () => {
        if (this.active) this.dropAim()
        else other.cancel()
      },
      // a right click (the mouse's, not an orbit drag) ends the shape in progress
      secondary: (p) => {
        if (this.active) this.endShape()
        else other.secondary?.(p)
      },
    }
  }

  /** End the shape in progress without adding it (the segment the band shows is never made). The tool stays on. */
  private endShape(): void {
    this.store.endShape()
  }

  /** Whether a pointer at (x, y) is on the last point placed, so it means "stop here" and not "place another". */
  private onLastPoint(x: number, y: number, kind: PickPointer): boolean {
    const last = this.picks[this.picks.length - 1]
    const ctx = last ? this.host.context(kind) : null
    if (!last || !ctx) return false
    const at = ctx.toView(last.uv)
    return Math.hypot(at.x - x, at.y - y) <= LAST_POINT_REACH[kind]
  }

  /** A finger taps: with a drawing tool on it places nothing, but a tap on the last point ends the shape there. */
  tap(x: number, y: number): void {
    if (this.active && this.onLastPoint(x, y, 'touch')) this.endShape()
  }

  private anchor(): Uv | null {
    return this.tool === 'line' ? (this.picks[0]?.uv ?? null) : null
  }

  private aimAt(p: PenPoint, kind: PickPointer): Aim | null {
    const ctx = this.host.context(kind)
    this.lastKind = kind
    this.aim = ctx ? snapAim(ctx, p.x, p.y, this.anchor()) : null
    this.host.requestRender()
    return this.aim
  }

  private dropAim(): void {
    this.aim = null
    this.host.requestRender()
  }

  private outside(p: PenPoint): boolean {
    return p.x < -OUTSIDE_MARGIN || p.y < -OUTSIDE_MARGIN || p.x > this.host.width() + OUTSIDE_MARGIN || p.y > this.host.height() + OUTSIDE_MARGIN
  }

  /** The pen lifts (or the mouse clicks): commit the point it was aiming. A mouse button release is at the aim of its last move. */
  private commitAt(p: PenPoint, kind: PickPointer): void {
    if (this.outside(p)) return this.dropAim()
    // a click or lift on the last point placed stops the shape there, as a double click does; nothing is added
    if (this.onLastPoint(p.x, p.y, kind)) return this.endShape()
    const aim = this.aimAt(p, kind)
    this.aim = null
    this.host.requestRender()
    if (aim) this.place({ uv: aim.uv, snap: aim.snap, infer: aim.infer })
  }

  private place(pick: Pick): void {
    const tool = this.tool
    if (degenerate(tool, this.picks, pick.uv)) return
    const picks = [...this.picks, pick]
    if (picks.length < PICKS[tool]) {
      this.picks = picks
      this.store.setDrawing(picks.length)
      this.host.requestRender()
      return
    }
    this.finish(tool, picks)
  }

  private finish(tool: DrawTool, picks: Pick[]): void {
    const end = picks[picks.length - 1]!
    this.sent++
    // the next start of a chain: where this line ended, the point the server makes for it. A line that closed on a point ends the chain.
    const next: Pick | null = tool === 'line' && end.snap?.kind !== 'point' ? { uv: end.uv, snap: null } : null
    const job = async (): Promise<void> => {
      const made = await this.store.add(addBody(tool, picks))
      if (next && made) next.point = made.points[1]
    }
    this.queue = this.queue.then(job, job)
    this.picks = next ? [next] : []
    this.store.setDrawing(this.picks.length)
    this.host.requestRender()
  }

  private hint(): string {
    if (this.tool === 'line' && this.lastKind === 'mouse') return 'Line: click to place each point. Double-click, right-click or press Esc to stop.'
    return HINT[this.tool]
  }

  report(): DrawReport {
    const aim = this.aim
    return {
      tool: this.store.getState().tool,
      picks: this.picks.map((p) => ({ uv: p.uv, snap: p.snap?.kind ?? null })),
      aim: aim ? { uv: aim.uv, at: aim.at, snap: aim.snap?.kind ?? null, infer: aim.infer } : null,
      sent: this.sent,
    }
  }

  /** Draw the cursor mark and the rubber band for the view as it is now; called once per frame. */
  update(): void {
    const ctx = this.active ? this.host.context('pen') : null
    let out = ''
    if (ctx) {
      const tool = this.tool
      const line = (pts: P[], dash?: string): string => `<path d="M${pts.map((q) => `${n(q.x)} ${n(q.y)}`).join('L')}" fill="none" stroke="${color.accent}" stroke-width="1.5" ${dash ? `stroke-dasharray="${dash}" ` : ''}stroke-linecap="round" stroke-linejoin="round"/>`
      for (const pk of this.picks) {
        const q = ctx.toView(pk.uv)
        out += `<circle data-kind="pick" cx="${n(q.x)}" cy="${n(q.y)}" r="5" fill="${color.white}" stroke="${color.accent}" stroke-width="2"/>`
      }
      const aim = this.aim
      if (aim) {
        if (aim.guide && aim.infer) {
          const g = ctx.toView(aim.guide)
          out += `<path data-kind="guide" d="M${n(g.x)} ${n(g.y)}L${n(aim.at.x)} ${n(aim.at.y)}" fill="none" stroke="${sketchColor.aimed}" stroke-width="1.5" stroke-dasharray="2 5" stroke-linecap="round"/>`
        }
        for (const poly of rubberBand(tool, this.picks, aim.uv)) out += `<g data-kind="band">${line(poly.map((u) => ctx.toView(u)), '6 4')}</g>`
        const text = aim.snap ? SNAP_TEXT[aim.snap.kind] : aim.infer ? INFER_TEXT[aim.infer] : ''
        const ring = aim.snap ? sketchColor.aimed : aim.infer ? sketchColor.aimed : color.accent
        const { x, y } = aim.at
        out +=
          `<g data-kind="aim" data-snap="${aim.snap?.kind ?? ''}" data-infer="${aim.infer ?? ''}">` +
          `<circle cx="${n(x)}" cy="${n(y)}" r="${aim.snap ? 11 : 9}" fill="none" stroke="${ring}" stroke-width="2"/>` +
          `<path d="M${n(x - 15)} ${n(y)}h8 M${n(x + 7)} ${n(y)}h8 M${n(x)} ${n(y - 15)}v8 M${n(x)} ${n(y + 7)}v8" fill="none" stroke="${ring}" stroke-width="1.5" stroke-linecap="round"/>` +
          (text
            ? `<g transform="translate(${n(x + 16)} ${n(y - 22)})"><rect width="${text.length * 7 + 14}" height="22" rx="6" fill="${color.white}" stroke="${ring}"/><text x="7" y="15" font-size="12" font-weight="500" fill="${color.ink}">${text}</text></g>`
            : '') +
          `</g>`
      }
    }
    const svg = out ? `<svg xmlns="http://www.w3.org/2000/svg" width="${this.host.width()}" height="${this.host.height()}" aria-hidden="true">${out}</svg>` : ''
    const markup = ctx ? `${svg}<div class="aim-hint" data-testid="aim-hint">${this.hint()}</div>` : ''
    if (markup !== this.lastMarkup) {
      this.lastMarkup = markup
      this.layer.innerHTML = markup
    }
  }
}
