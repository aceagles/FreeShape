import { color } from '../theme/tokens'
import type { Part, StudioOutputs } from '../wire'

/** How far the edited feature's result moves from its own colour toward the accent. */
const TINT = 0.35

const hex = (h: string): [number, number, number] => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as [number, number, number]

/** Whether `part` is a result of `feature` (part ids are `<feature id>/<n>`). */
export const isResultOf = (part: Part, feature: string): boolean => part.id === feature || part.id.startsWith(`${feature}/`)

/**
 * The outputs of a dialog's preview with the edited feature's result tinted toward the accent colour (a face colour
 * override on those parts goes, so the whole result reads as one). Returns the outputs and how many parts were tinted;
 * with none tinted (a feature that failed, or one that changed an older part) the outputs are returned as they are.
 */
export function tintFeature(outputs: StudioOutputs, feature: string): { outputs: StudioOutputs; tinted: number } {
  const accent = hex(color.accent)
  let tinted = 0
  const parts = outputs.parts.map((p) => {
    if (!isResultOf(p, feature)) return p
    tinted++
    const base = p.color
    const mixed = [0, 1, 2].map((i) => (base[i] ?? 0.7) * (1 - TINT) + accent[i]! * TINT)
    return { ...p, color: base.length > 3 ? [...mixed, base[3]!] : mixed, faceColors: null }
  })
  return tinted === 0 ? { outputs, tinted: 0 } : { outputs: { ...outputs, parts }, tinted }
}
