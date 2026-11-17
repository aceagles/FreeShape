import { useEffect, useRef } from 'react'
import { isImportName } from '../files/filesApi'
import { inTextEntry } from '../input/surface'

/*
 * Dropping files on the page (card I11.8).
 *
 * A file dragged over a page that does not handle it is opened by the browser in place of the app. `installDropGuard` stops that
 * on every screen: it takes the file drags' `dragover` and `drop` and does nothing with them. `useFileDrop` is the Shell's use of
 * the drop. These are the browser's drag-and-drop events, not pointer input (no pointerdown, pointermove or touch event is read
 * or stopped here; a native drag takes over the pointer and fires none), so they do not meet src/input/pointer.ts, which keeps
 * every pointer handler on the view. They sit with the viewport because the viewport is what a file is dropped on; the listeners
 * are on the document so that a file dropped off the view is caught as well, and told so.
 */

/** Whether a drag carries files (and not text or a link). */
export function dragHasFiles(dt: DataTransfer | null): boolean {
  return dt !== null && Array.from(dt.types).includes('Files')
}

/** Starts keeping the browser from navigating to a dropped file; returns the function that stops it. A file input keeps its own drop. */
export function installDropGuard(doc: Document = document): () => void {
  const guard = (e: DragEvent): void => {
    if (!dragHasFiles(e.dataTransfer) || inTextEntry(e.target)) return
    e.preventDefault()
    if (e.type === 'dragover' && e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
  }
  doc.addEventListener('dragover', guard)
  doc.addEventListener('drop', guard)
  return () => {
    doc.removeEventListener('dragover', guard)
    doc.removeEventListener('drop', guard)
  }
}

/** What a drop comes to: the files to import into new Part Studios, and what to say about the rest (null: nothing to say). */
export interface DropPlan {
  import: File[]
  say: string | null
}

/**
 * The drop's outcome. Desktop mode on the view imports each STEP or BREP file (the extensions of the desktop's `open_any`); any
 * other file is named in the message. Off the view, in Tablet mode, or when `blocked` gives a reason, nothing is imported and the
 * message says why.
 */
export function planDrop(files: readonly File[], ctx: { desktop: boolean; onView: boolean; blocked: string | null }): DropPlan {
  if (files.length === 0) return { import: [], say: null }
  if (!ctx.desktop) return { import: [], say: 'Dropping files works in Desktop mode. Here, use Files, then From this device…, to import a STEP or BREP file.' }
  if (!ctx.onView) return { import: [], say: 'Drop a STEP or BREP file on the view to import it into a new Part Studio.' }
  if (ctx.blocked !== null) return { import: [], say: ctx.blocked }
  const ok = files.filter((f) => isImportName(f.name))
  const rest = files.filter((f) => !isImportName(f.name))
  const named = rest.map((f) => `“${f.name}”`).join(', ')
  return {
    import: ok,
    say: rest.length === 0 ? null : `${named} ${rest.length === 1 ? 'is' : 'are'} not a STEP or BREP file. The view imports .step, .stp, .brep and .brp files.`,
  }
}

/** The Shell's drop: `onDrop` gets the dropped files and whether they landed on `view`. */
export function useFileDrop(view: HTMLElement | null, onDrop: (files: File[], onView: boolean) => void): void {
  const latest = useRef(onDrop)
  useEffect(() => {
    latest.current = onDrop
  })
  useEffect(() => {
    const over = (e: DragEvent): void => {
      if (!dragHasFiles(e.dataTransfer) || inTextEntry(e.target)) return
      e.preventDefault()
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
    }
    const drop = (e: DragEvent): void => {
      if (!dragHasFiles(e.dataTransfer) || inTextEntry(e.target)) return
      e.preventDefault()
      const files = Array.from(e.dataTransfer?.files ?? [])
      latest.current(files, view !== null && e.target instanceof Node && view.contains(e.target))
    }
    document.addEventListener('dragover', over)
    document.addEventListener('drop', drop)
    return () => {
      document.removeEventListener('dragover', over)
      document.removeEventListener('drop', drop)
    }
  }, [view])
}
