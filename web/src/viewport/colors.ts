import { Color, ColorManagement } from 'three'
import { color, sceneColor } from '../theme/tokens'

// The desktop hands raw 0..1 values to the graphics card. Switching colour management off does the same here,
// so a token or an engine colour reaches the canvas unchanged and matches the desktop and the layout page.
ColorManagement.enabled = false

/** A token hex string or an r g b triple in 0..1 as a three.js colour, unconverted. */
export function rgb(c: string | readonly number[]): Color {
  if (typeof c === 'string') return new Color(c)
  return new Color(c[0] ?? 0, c[1] ?? 0, c[2] ?? 0)
}

export const viewportBackground = (): Color => rgb(color.viewport)
export const edgeColor = (): Color => rgb(sceneColor.edge)
