import { useSyncExternalStore } from 'react'

// Full screen for the whole app (card F18): the document element, so menus, dialogs and portals stay on screen. The
// standard Fullscreen API with WebKit's prefixed names as the fallback. Where neither exists (iPhone Safari, some embedded
// views) the button is not shown: found by feature, never by the user agent. No key is bound (F11 is the browser's).

type FsDocument = Document & {
  webkitFullscreenElement?: Element | null
  webkitFullscreenEnabled?: boolean
  webkitExitFullscreen?: () => Promise<void> | void
}
type FsElement = HTMLElement & { webkitRequestFullscreen?: () => Promise<void> | void }

const EVENTS = ['fullscreenchange', 'webkitfullscreenchange'] as const

/** True when this browser can put the page in full screen. */
export function fullscreenSupported(): boolean {
  if (typeof document === 'undefined') return false
  const doc = document as FsDocument
  const el = document.documentElement as FsElement
  const request = typeof el.requestFullscreen === 'function' || typeof el.webkitRequestFullscreen === 'function'
  // an embedding that forbids full screen says so through fullscreenEnabled
  const enabled = doc.fullscreenEnabled ?? doc.webkitFullscreenEnabled ?? true
  return request && enabled !== false
}

/** True while some element of the page is in full screen. */
export function isFullscreen(): boolean {
  const doc = document as FsDocument
  return (doc.fullscreenElement ?? doc.webkitFullscreenElement ?? null) !== null
}

function subscribe(on: () => void): () => void {
  for (const e of EVENTS) document.addEventListener(e, on)
  return () => {
    for (const e of EVENTS) document.removeEventListener(e, on)
  }
}

/** A promise a refusing browser rejects, or a throw: either leaves the state to the change event, which never came. */
function settle(run: () => Promise<void> | void): void {
  try {
    const r = run()
    if (r && typeof (r as Promise<void>).catch === 'function') (r as Promise<void>).catch(() => {})
  } catch {
    // refused: nothing changed, and the button keeps showing the state the browser reports
  }
}

/** Enter full screen, or leave it. Call it from the click itself (full screen needs a user gesture). */
export function toggleFullscreen(): void {
  const doc = document as FsDocument
  if (isFullscreen()) {
    settle(() => (typeof doc.exitFullscreen === 'function' ? doc.exitFullscreen() : doc.webkitExitFullscreen?.()))
  } else {
    const el = document.documentElement as FsElement
    settle(() => (typeof el.requestFullscreen === 'function' ? el.requestFullscreen() : el.webkitRequestFullscreen?.()))
  }
}

/** Whether the page is in full screen now, kept current by the change events (Esc and the browser's own gesture too). */
export function useFullscreen(): boolean {
  return useSyncExternalStore(subscribe, isFullscreen, () => false)
}
