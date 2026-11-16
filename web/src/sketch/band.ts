import { FIT_MARGIN } from '../viewport/camera'
import type { Bounds, FitMargins, ViewCamera } from '../viewport/camera'
import type { SketchDrawing } from './draw'
import { uvToView } from './frame'
import { layoutSketch } from './layout'
import type { Rect } from './layout'
import type { Frame } from './wire'

// What lies over the top and the foot of a sketch's view, in CSS pixels from the view's top left (sketch.css and
// shell.css set these; the e2e measure the real boxes against the same numbers). Labels win: nothing may sit on top of a
// dimension label, so a fit keeps the sketch out of these boxes (F10).
const INSET = 12
const CHIP_ROW = 52
const GAP = 8
/** The refused-constraint note, one line (`min-height`). */
const NOTE_HEIGHT = 44
/** A hint, one line: 8 + 18 + 8 px of padding and text, and its border. */
const HINT_HEIGHT = 40
/** `max-width` of the note and the hints, and their left edge. */
const SLOT_WIDTH = 600
/** The drawing tool's hint at the foot of the view: 12 px up, one line. */
const AIM_HINT = INSET + 34

/** Where the note, the hint on its own, and the hint under a note are (the last two never meet in one state). */
export const BANDS = {
  chips: { x: INSET, y: INSET, w: SLOT_WIDTH, h: CHIP_ROW },
  note: { x: INSET, y: INSET + CHIP_ROW + GAP, w: SLOT_WIDTH, h: NOTE_HEIGHT },
  hint: { x: INSET, y: INSET + CHIP_ROW + GAP, w: SLOT_WIDTH, h: HINT_HEIGHT },
  stackedHint: { x: INSET, y: INSET + CHIP_ROW + GAP + NOTE_HEIGHT + GAP, w: SLOT_WIDTH, h: HINT_HEIGHT },
} as const satisfies Record<string, Rect>

/** The top of the sketch's room: under the chip row with a note and a hint stacked on it. */
export const TOP_BAND = BANDS.stackedHint.y + BANDS.stackedHint.h + GAP
/** The foot's: under the drawing tool's hint. */
export const BOTTOM_BAND = Math.max(AIM_HINT + GAP, FIT_MARGIN)
/** A label stays this far from the view's edges. */
const EDGE = 8
const ROUNDS = 6

/**
 * Frame a sketch so that its geometry and every dimension label lie clear of the chips, the hints and the view's edges.
 * `box` is the sketch's box in the plane (world); the labels stand off the geometry by a fixed number of pixels, so the
 * margins grow by what a first fit leaves over the edge until nothing does.
 */
export function fitSketchView(view: ViewCamera, box: Bounds, frame: Frame, drawing: SketchDrawing): void {
  const margins: FitMargins = { top: TOP_BAND, bottom: BOTTOM_BAND, side: FIT_MARGIN }
  const { width, height } = view
  for (let i = 0; i < ROUNDS; i++) {
    view.fit(box, margins)
    const layout = layoutSketch(drawing, (uv) => uvToView(view, frame, uv))
    let over = { top: 0, bottom: 0, side: 0 }
    for (const { label } of layout.dims) {
      over = {
        top: Math.max(over.top, TOP_BAND - label.y),
        bottom: Math.max(over.bottom, label.y + label.h - (height - BOTTOM_BAND)),
        side: Math.max(over.side, EDGE - label.x, label.x + label.w - (width - EDGE)),
      }
    }
    if (over.top <= 0.5 && over.bottom <= 0.5 && over.side <= 0.5) return
    margins.top = Math.min(margins.top + Math.max(over.top, 0), height * 0.4)
    margins.bottom = Math.min(margins.bottom + Math.max(over.bottom, 0), height * 0.3)
    margins.side = Math.min(margins.side + Math.max(over.side, 0), width * 0.3)
  }
  view.fit(box, margins)
}
