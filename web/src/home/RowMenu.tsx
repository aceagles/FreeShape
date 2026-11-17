import { useState } from 'react'
import { Icon, paths } from '../shell/icons'
import { anchorOf, Popover, PopoverItem } from '../shell/Popover'
import type { Anchor } from '../shell/Popover'

/**
 * The 44 px menu button at the end of a Document's row on the home screen (card I11.10): Download (.fsdoc). The row's name element
 * (`nameId`) describes it, so the list does not repeat the Document's name in every button's label.
 */
export function RowMenu({ name, nameId, disabled, onDownload }: { name: string; nameId: string; disabled?: boolean; onDownload: () => void }) {
  const [anchor, setAnchor] = useState<Anchor | null>(null)
  return (
    <>
      <button
        type="button"
        className="icon-button row-menu-button"
        aria-label="Document menu"
        aria-describedby={nameId}
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        disabled={disabled}
        onClick={(e) => setAnchor(anchorOf(e.currentTarget))}
      >
        <Icon d={paths.more} sw={3} />
      </button>
      {anchor !== null && (
        <Popover anchor={anchor} label={`${name} menu`} onClose={() => setAnchor(null)}>
          <PopoverItem
            label="Download (.fsdoc)"
            icon={<Icon d={paths.download} />}
            onClick={() => {
              setAnchor(null)
              onDownload()
            }}
          />
        </Popover>
      )}
    </>
  )
}
