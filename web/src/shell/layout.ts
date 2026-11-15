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
