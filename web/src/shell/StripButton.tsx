import { Icon } from './icons'

interface Props {
  label: string
  icon: string
  /** A group button: it opens a list of tools and carries a corner mark. */
  group?: boolean
  disabled?: boolean
  active?: boolean
  /** Start a new cluster: 10 px more space before the button, as in the layout. */
  gap?: boolean
  onClick?: () => void
}

/**
 * A 72 x 56 button of the tool strip: icon over label. The group mark sits in the top right corner, above the label's
 * line and beside the icon, so a long label ("Constrain") cannot touch it; the label is clipped to the button's width.
 */
export function StripButton({ label, icon, group, disabled, active, gap, onClick }: Props) {
  return (
    <button
      type="button"
      className="strip-button"
      data-active={active ? '' : undefined}
      data-gap={gap ? '' : undefined}
      aria-haspopup={group ? 'true' : undefined}
      disabled={disabled}
      onClick={onClick}
    >
      <Icon d={icon} />
      <span className="strip-label">{label}</span>
      {group && (
        <svg className="strip-mark" width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
          <path d="M8 0v8H0z" fill="currentColor" />
        </svg>
      )}
    </button>
  )
}
