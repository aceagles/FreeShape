/**
 * The app's surface: what the browser may not do with a press-and-hold anywhere in the app (F19).
 *
 * On a touch screen a long press is also the browser's gesture: it starts a text selection (and drags extend it, taking the
 * moves the app needs to slide to an item of the hold's ring), or opens a context menu. The CSS half is the block in
 * shell.css (`user-select`, `-webkit-touch-callout`, `touch-action`); this is the event half, installed once on the
 * document so it covers the shell and everything portalled outside it. The one exception is real text entry: an input, a
 * textarea or a contenteditable keeps its selection and its native menu, so paste works.
 *
 * `contextmenu` is prevented here and nowhere else for mouse, pen and touch alike. The right mouse button reaches the app
 * through the pointer module (`secondary`, and right-drag orbits), so a later context-menu card draws its own menu from
 * that and needs no native one.
 */
const TEXT_ENTRY = 'input, textarea, [contenteditable]:not([contenteditable="false"])'

/** Whether an event target is inside a field the user types into. */
export function inTextEntry(target: EventTarget | null): boolean {
  const el = target instanceof Element ? target : target instanceof Node ? target.parentElement : null
  return el?.closest(TEXT_ENTRY) != null
}

/** Starts keeping the browser's selection and context menu off `doc`; returns the function that stops it. */
export function installSurfaceGuards(doc: Document = document): () => void {
  const guard = (e: Event): void => {
    if (!inTextEntry(e.target)) e.preventDefault()
  }
  doc.addEventListener('contextmenu', guard)
  doc.addEventListener('selectstart', guard)
  return () => {
    doc.removeEventListener('contextmenu', guard)
    doc.removeEventListener('selectstart', guard)
  }
}
