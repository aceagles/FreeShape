import type { Holder } from '../net/api'

/** Where a Document is being edited, as a phrase after "in": the desktop, another browser client. */
export function holderPlace(holder: Holder | null): string {
  if (holder === null) return 'another program'
  if (holder.program === 'desktop') return 'the desktop'
  if (holder.program === 'browser') return 'another browser client'
  return holder.program
}

/** The read-only banner's sentence, worded like the desktop's ("<name>" is being edited in the browser client, ...). */
export function readOnlyText(name: string, holder: Holder | null): string {
  return `"${name}" is being edited in ${holderPlace(holder)}, so it is open read-only here.`
}
