import { screens, size } from '../theme/tokens'

/** Width of the feature panel's column: the panel (280, or 320 on the large iPad) or the 56 px rail. */
export function columnWidth(windowWidth: number, collapsed: boolean): number {
  if (collapsed) return size.rail
  return windowWidth >= screens['1366'].width ? screens['1366'].panelWidth : screens['1024'].panelWidth
}

/** The viewport takes what the column leaves. The dialog area overlays the column, so it is not part of the sum. */
export function viewportWidth(windowWidth: number, collapsed: boolean): number {
  return windowWidth - columnWidth(windowWidth, collapsed)
}

/** localStorage key holding the width the left column was dragged to on this device (Desktop mode). */
export const LEFT_WIDTH_KEY = 'freeshape.leftWidth'
/** The desktop program's width for its left column. */
export const DEFAULT_LEFT_WIDTH = 270
export const MIN_LEFT_WIDTH = 200

/** The left column's width in Desktop mode: at least 200 px and at most half the window. */
export function clampLeftWidth(width: number, windowWidth: number): number {
  return Math.round(Math.max(MIN_LEFT_WIDTH, Math.min(width, Math.floor(windowWidth / 2))))
}

/** The width kept on this device, or the default; storage that is blocked or holds rubbish gives the default. */
export function readLeftWidth(): number {
  try {
    const n = Number(window.localStorage.getItem(LEFT_WIDTH_KEY))
    if (Number.isFinite(n) && n > 0) return n
  } catch {
    // storage blocked: use the default
  }
  return DEFAULT_LEFT_WIDTH
}

/** Keep the width for this device; a storage that throws is ignored (the width then lasts for the session). */
export function storeLeftWidth(width: number): void {
  try {
    window.localStorage.setItem(LEFT_WIDTH_KEY, String(width))
  } catch {
    // keep it in memory only
  }
}

/** The column's width in Desktop mode: the rail when collapsed, else the kept width inside its limits. */
export function desktopColumnWidth(windowWidth: number, collapsed: boolean, kept: number): number {
  return collapsed ? size.rail : clampLeftWidth(kept, windowWidth)
}
