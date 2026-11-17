import { json, request, seg, tabPath } from '../net/api'

/** One row of a feature dialog's description (freeshape/dialog_schema.py `describe`). Only the keys a kind carries are set. */
export interface FieldSpec {
  key: string | null
  /**
   * num, choice, ops, check, sel, info, text, ticks (a list of rows to tick: `options`, its value the ids ticked); a
   * hand-built row has its own name (split_tools, ...).
   */
  kind: string
  /** False for a hand-built row: the client cannot draw it. */
  standard: boolean
  visible: boolean
  label?: string
  unit?: string | null
  min?: number | null
  signed?: boolean
  options?: Array<[string, string]>
  default?: string
  want?: string[]
  multi?: boolean
  number?: boolean
  list?: boolean
  tip?: string | null
  text?: string
  placeholder?: string
  expr?: boolean
  /** A choice's options that cannot be chosen here, each with why: {value: "Desktop only for now"}. */
  disabled?: Record<string, string>
  /** "studio": a choice of this Document's Part Studios, drawn as the Part Studio list with their parts (the Derived's source). */
  picker?: string
  /** ticks: the tick lists of one group are the tabs of one row (the Derived's Parts, Sketches and Mate connectors). */
  group?: string
}

export interface Description {
  type: string
  fields: FieldSpec[]
  /** Keys of the hand-built rows: when there are any, the feature can only be edited on the desktop. */
  custom: string[]
}

/** What the server holds for one row: the evaluated `value`, the `text` a number was typed as, and the message of the last refused input. */
export interface FieldValue {
  value: unknown
  text?: string
  error?: string
}

/** One item of a selection row, as the refs route answers it. `name` is the Element name, or null. */
export interface SelItem {
  ok: boolean
  kind?: string
  ref?: unknown
  name?: string | null
}

/** The open reply, and the part of the update reply that changes what the dialog shows. */
export interface DialogView {
  description: Description
  values: Record<string, FieldValue>
  fields: Record<string, SelItem[]>
  active: string | null
}

export interface OpenReply extends DialogView {
  dialog: string
  document: string
  tab: string
  /** The feature's id; null for a dialog that creates one (the engine gives the id at Accept). */
  feature: string | null
  /** Set when the dialog creates a feature of this type. */
  type?: string
  name: string
  revision: number
}

export interface UpdateReply extends DialogView {
  errors: Record<string, string>
  /** The preview asked for, or null when none could be. */
  preview: number | null
}

const at = (doc: string, tab: string, dialog: string, verb: string) => `${tabPath(doc, tab)}/dialog/${seg(dialog)}/${verb}`
const as = (client: string) => ({ 'X-Client-Id': client })

export const openFeatureDialog = (doc: string, tab: string, feature: string, client: string) =>
  json<OpenReply>('POST', `${tabPath(doc, tab)}/features/${seg(feature)}/dialog`, { body: {}, headers: as(client) })
/** Open a dialog that creates a feature of `type`. `picks` are the selection (the refs route's format, indices from 1). */
/** `params` is what the type starts from besides the picks: a Canvas's uploaded image `{blob, filename}` (card I12.5). */
export const openCreateDialog = (doc: string, tab: string, type: string, picks: readonly unknown[], client: string, params?: Record<string, unknown>) =>
  json<OpenReply>('POST', `${tabPath(doc, tab)}/features/dialog`, { body: { type, ...(picks.length > 0 ? { picks } : {}), ...(params !== undefined ? { params } : {}) }, headers: as(client) })
export const updateFeatureDialog = (doc: string, tab: string, dialog: string, client: string, values: Record<string, unknown>) =>
  json<UpdateReply>('POST', at(doc, tab, dialog, 'update'), { body: { values }, headers: as(client) })
export const acceptFeatureDialog = (doc: string, tab: string, dialog: string, client: string) =>
  json<{ rebuilt: string[]; revision: number | null }>('POST', at(doc, tab, dialog, 'accept'), { body: {}, headers: as(client) })
export const cancelFeatureDialog = (doc: string, tab: string, dialog: string, client: string) =>
  json<{ cancelled: boolean }>('POST', at(doc, tab, dialog, 'cancel'), { body: {}, headers: as(client) })

/**
 * The model the dialog picks against (card F3), in the engine wire format, with the committed revision it was built from
 * (X-Revision): the Part Studio before the edited feature, or at the rollback bar for a new one.
 */
export async function fetchBaseBytes(doc: string, tab: string, dialog: string, client: string): Promise<{ bytes: ArrayBuffer; revision: number }> {
  const res = await request('GET', at(doc, tab, dialog, 'base'), { headers: as(client) })
  return { bytes: await res.arrayBuffer(), revision: Number(res.headers.get('X-Revision')) }
}

/** The dialog's newest preview in the engine wire format, with its number (X-Preview); null when there is none (404). */
export async function fetchPreviewBytes(doc: string, tab: string, dialog: string, client: string): Promise<{ bytes: ArrayBuffer; number: number } | null> {
  try {
    const res = await request('GET', at(doc, tab, dialog, 'preview'), { headers: as(client) })
    return { bytes: await res.arrayBuffer(), number: Number(res.headers.get('X-Preview')) }
  } catch (e) {
    if (e instanceof Error && 'status' in e && (e as { status: number }).status === 404) return null
    throw e
  }
}
