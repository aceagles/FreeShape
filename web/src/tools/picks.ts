import type { SelectionItem } from '../selection/store'

/** A pick as the server's refs, types and create-dialog routes take it (freeshape/serve.py, "Refs"). `index` counts from 1. */
export interface Pick {
  part: string
  kind: 'face' | 'edge' | 'vertex' | 'part'
  index?: number
}

/** The longest query string the types route takes. */
export const MAX_QUERY = 8000

/**
 * The selection as picks, in selection order. The client's items count from 0 and the server's from 1, so the index is
 * one more. A plane or an axis has no part and is left out: the server has no pick for it. Items that would make the
 * types query longer than the route takes (about 8000 characters) are dropped from the end.
 */
export function picksOf(items: readonly SelectionItem[]): Pick[] {
  const picks: Pick[] = []
  for (const i of items) {
    if (i.part === null) continue
    if (i.kind === 'part') picks.push({ part: i.part, kind: 'part' })
    else if (i.kind === 'face' || i.kind === 'edge' || i.kind === 'vertex') picks.push({ part: i.part, kind: i.kind, index: i.index + 1 })
  }
  while (picks.length > 0 && encodeURIComponent(JSON.stringify(picks)).length + 'picks='.length > MAX_QUERY - 100) picks.pop()
  return picks
}
