import { ApiError } from '../net/api'
import type { Holder } from '../net/api'
import { holderPlace } from './holder'

function holderOf(e: ApiError): Holder | null {
  const h = e.body.holder
  return typeof h === 'object' && h !== null && typeof (h as { program?: unknown }).program === 'string' ? (h as Holder) : null
}

/** A server error in plain words that say what to do. `name` is the Document's name. */
export function explainError(e: unknown, name: string): string {
  if (!(e instanceof ApiError)) return e instanceof Error ? e.message : 'Something went wrong.'
  if (e.status === 423) {
    return `"${name}" is being edited in ${holderPlace(holderOf(e))}, so nothing was changed. Use Take over in the banner to edit it here.`
  }
  if (e.status === 409 && e.linkedBy.length > 0) {
    const names = e.linkedBy.map((t) => `"${t.name}"`).join(', ')
    return `The tab cannot be deleted because ${names} uses it. Delete the link in ${e.linkedBy.length === 1 ? 'that tab' : 'those tabs'} first.`
  }
  return e.message
}
