import type { PenHandler, PenPoint, PointerModule } from '../input/pointer'
import type { PickHit, PickPointer } from '../viewport/pick'
import { itemOf } from './store'
import type { SelectionItem, SelectionStore } from './store'

/** What is under a view position (CSS pixels from the canvas's top left) for this kind of pointer, or null: `ViewportController.pickAt` without the filter. */
export type PickFn = (x: number, y: number, pointer: PickPointer, filter: SelectionStore['pickFilter']) => PickHit | null

/**
 * Connects the pointer module to the selection: Aim, Commit on release, and the finger tap.
 *
 * Pen: down starts an Aim; every move picks and sets the Aim; up Commits on what is Aimed at that moment (so a pen
 * that lifts over empty space commits `null`: single mode clears, Multi mode cancels that pick). A pointercancel
 * (palm rejection, the app going to the background) only ends the Aim: it never Commits and never clears.
 * Mouse: moving highlights, and a click (left button down and up) Commits on what is under it.
 * Finger: a tap (the pointer module decides: under 250 ms, no more than 10 px) Commits on the item under it, at once.
 * The second tap of a double tap takes the first one back (the double tap fits the view and selects nothing).
 * Returns the function that undoes the wiring.
 */
export function wireSelection(pointer: PointerModule, selection: SelectionStore, pick: PickFn): () => void {
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
      selection.commit(item)
      // the mouse stays over the item; the pen is up and has nothing under it
      if (kind === 'mouse') selection.aim(item)
    },
    cancel: () => selection.aim(null),
  })
  let before: readonly SelectionItem[] | null = null
  pointer.setPenHandler(picking('pen'))
  pointer.setMouseHandler(picking('mouse'))
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
