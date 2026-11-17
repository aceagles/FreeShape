import type { BoxDrag, PenHandler, PenPoint, PointerModule } from '../input/pointer'
import type { PickFilter, PickHit, PickKind, PickPointer } from '../viewport/pick'
import { itemOf } from './store'
import type { SelectionItem, SelectionStore } from './store'

/** What is under a view position (CSS pixels from the canvas's top left) for this kind of pointer, or null: `ViewportController.pickAt` without the filter. */
export type PickFn = (x: number, y: number, pointer: PickPointer, filter: SelectionStore['pickFilter']) => PickHit | null

/** The kinds a right click picks (the desktop's `Viewport.mouseReleaseEvent`: face, edge, region, plane, axis, sketch_line), whatever the Select menu says. */
export const CONTEXT_KINDS: readonly PickKind[] = ['face', 'edge', 'plane', 'axis', 'region']

/** What a view tells the shell about a right click (Desktop mode, card I9.10): what is under it, or null, and where (view coordinates). */
export type ContextSink = (hit: PickHit | null, p: PenPoint) => void

/** A right click on a view as the shell gets it: where (the window's coordinates), what is under it, and whether the view is in sketch mode (then `hit` is null). */
export interface ViewContextEvent {
  x: number
  y: number
  hit: PickHit | null
  sketch: boolean
}

/** What a view gives the selection for its box (I9.9): the items a released box takes, and where to draw the box (null: none). */
export interface BoxSource {
  hits(box: BoxDrag, filter: PickFilter | undefined): SelectionItem[]
  draw(box: BoxDrag | null): void
}

/**
 * Connects the pointer module to the selection: Aim, Commit on release, and the finger tap.
 *
 * Pen: down starts an Aim; every move picks and sets the Aim; up Commits on what is Aimed at that moment (so a pen
 * that lifts over empty space commits `null`: single mode clears, Multi mode cancels that pick). A pointercancel
 * (palm rejection, the app going to the background) only ends the Aim: it never Commits and never clears.
 * Mouse: moving highlights, and a click (left button down and up) Commits on what is under it.
 * Finger: a tap (the pointer module decides: under 250 ms, no more than 10 px) Commits on the item under it, at once.
 * The second tap of a double tap takes the first one back (the double tap fits the view and selects nothing).
 * Desktop mode (I9.9, `pointer.desktop`): a mouse click toggles the item under it and keeps the rest, a click on empty
 * space clears unless Shift or Ctrl is held, and with `box` a left drag draws a selection box (none while a dialog has the
 * view, as on the desktop). Pen and finger are the same in both modes. With `context`, a right click in Desktop mode reports what is
 * under it (picked with CONTEXT_KINDS, not the Select menu's filter, as the desktop does).
 * Returns the function that undoes the wiring.
 */
export function wireSelection(pointer: PointerModule, selection: SelectionStore, pick: PickFn, box?: BoxSource, context?: ContextSink): () => void {
  const itemAt = (x: number, y: number, kind: PickPointer): SelectionItem | null => {
    const hit = pick(x, y, kind, selection.pickFilter)
    return hit ? itemOf(hit) : null
  }
  const picking = (kind: 'pen' | 'mouse'): PenHandler => ({
    aim: (p: PenPoint) => selection.aim(itemAt(p.x, p.y, kind)),
    commit: (p: PenPoint) => {
      // the mouse is where it is: pick again under it; the pen lifted where its last Aim was
      if (kind === 'mouse') selection.aim(itemAt(p.x, p.y, kind))
      const item = selection.getAimed()
      if (kind === 'mouse' && pointer.desktop) selection.click(item, p.shift === true || p.ctrl === true)
      else selection.commit(item)
      // the mouse stays over the item; the pen is up and has nothing under it
      if (kind === 'mouse') selection.aim(item)
    },
    cancel: () => selection.aim(null),
  })
  const mouse: PenHandler = picking('mouse')
  if (box) {
    mouse.boxStart = () => {
      if (selection.dialogOpen) return false
      selection.aim(null)
      return true
    }
    mouse.boxMove = (b) => box.draw(b)
    mouse.boxEnd = (b, additive) => {
      box.draw(null)
      selection.addBox(box.hits(b, selection.pickFilter), additive)
    }
    mouse.boxCancel = () => box.draw(null)
  }
  // Desktop mode: a double click on something (the desktop's `mouseDoubleClickEvent` picks with its filter and tells the window)
  mouse.doubleClick = (p) => {
    const item = itemAt(p.x, p.y, 'mouse')
    if (item) selection.doubleClick(item)
  }
  // a right click without movement (the pointer module's `secondary`; a right drag orbits): Desktop mode asks the shell for a menu
  if (context) mouse.secondary = (p) => void (pointer.desktop && context(pick(p.x, p.y, 'mouse', CONTEXT_KINDS), p))
  let before: readonly SelectionItem[] | null = null
  pointer.setPenHandler(picking('pen'))
  pointer.setMouseHandler(mouse)
  pointer.setTapHandler({
    tap: (x, y) => {
      before = selection.snapshot()
      selection.commit(itemAt(x, y, 'touch'))
    },
    undoTap: () => {
      if (before) selection.restore(before)
      before = null
    },
  })
  return () => {
    pointer.setPenHandler(null)
    pointer.setMouseHandler(null)
    pointer.setTapHandler(null)
  }
}
