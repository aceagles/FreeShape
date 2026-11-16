import { json, tabPath } from '../net/api'
import type { Pick } from './picks'

/** One row of GET .../features/types: `standard` is false for a type whose dialog has a hand-built row, `can_start` whether the picks could start it. */
export interface FeatureType {
  type: string
  label: string
  standard: boolean
  can_start: boolean
}

export interface TypesReply {
  document: string
  tab: string
  revision: number
  types: FeatureType[]
}

/** What each tool may do now, from the server. Needs no client id. With no picks, every type can start. */
export const fetchFeatureTypes = (doc: string, tab: string, picks: readonly Pick[], signal?: AbortSignal) =>
  json<TypesReply>('GET', `${tabPath(doc, tab)}/features/types${picks.length > 0 ? `?picks=${encodeURIComponent(JSON.stringify(picks))}` : ''}`, { signal })
