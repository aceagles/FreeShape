import type { Holder } from '../net/api'
import { readOnlyText } from './holder'

/** Shown under the top bar while the Document is open read-only. Take over (after a confirmation) makes it editable here. */
export function ReadOnlyBanner({ name, holder, onTakeOver }: { name: string; holder: Holder | null; onTakeOver: () => void }) {
  return (
    <div className="read-only-banner" role="status" data-testid="read-only-banner">
      <span className="banner-text">
        {readOnlyText(name, holder)}
        {holder?.stale ? ' It has not been heard from for a while, so it may have stopped.' : ''}
      </span>
      <button type="button" className="banner-button" onClick={onTakeOver}>
        Take over
      </button>
    </div>
  )
}
