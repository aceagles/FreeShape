/**
 * The colours a part can be given: the desktop's own PART_PALETTE (freeshape/features.py), the muted colours it hands
 * to new parts in creation order, with the names of its comments. The desktop's colour dialog is free-form; a tablet
 * offers this fixed set (card I3.3) so both programs start from the same colours. "Default colour" in the dialog sends
 * `color: null`, which puts back the colour the part was built with.
 */
export interface PaletteColor {
  readonly name: string
  /** r g b in 0..1, as the engine keeps them. */
  readonly rgb: readonly [number, number, number]
}

export const PART_PALETTE: readonly PaletteColor[] = [
  { name: 'Steel blue', rgb: [0.6, 0.74, 0.85] },
  { name: 'Apricot', rgb: [0.93, 0.66, 0.45] },
  { name: 'Sage green', rgb: [0.55, 0.78, 0.6] },
  { name: 'Dusty rose', rgb: [0.84, 0.58, 0.68] },
  { name: 'Lavender', rgb: [0.72, 0.66, 0.88] },
  { name: 'Mustard', rgb: [0.9, 0.8, 0.45] },
  { name: 'Teal', rgb: [0.5, 0.78, 0.78] },
  { name: 'Clay', rgb: [0.8, 0.62, 0.52] },
  { name: 'Olive', rgb: [0.66, 0.76, 0.46] },
  { name: 'Slate blue', rgb: [0.62, 0.68, 0.8] },
]

/** Whether a colour of a part is this palette entry (the engine hands floats back unchanged; a hair of slack). */
export function sameColor(a: readonly number[], b: readonly number[]): boolean {
  return a.length >= 3 && b.length >= 3 && [0, 1, 2].every((k) => Math.abs((a[k] as number) - (b[k] as number)) < 0.002)
}

/** A colour of the engine as a CSS colour, for a swatch. */
export function cssColor(c: readonly number[]): string {
  const v = (k: number) => Math.round(Math.min(Math.max(c[k] ?? 0.7, 0), 1) * 255)
  return `rgb(${v(0)}, ${v(1)}, ${v(2)})`
}
