import { useState } from 'react'
import type { TabInfo } from '../net/connection'
import { Icon, paths } from './icons'
import { useLongPress } from './longPress'
import { MenuButton } from './Menu'
import { createMenu } from './menus'
import type { Commands, MenuState } from './menus'
import { anchorOf, Popover, PopoverItem } from './Popover'
import type { Anchor } from './Popover'

interface Props {
  tabs: readonly TabInfo[]
  currentTab: string | null
  /** The Document is open read-only: adding, renaming and deleting are off. */
  readOnly: boolean
  onSelect: (id: string) => void
  onAdd: (kind: 'studio' | 'assembly') => void
  onRename: (tab: TabInfo) => void
  onDelete: (tab: TabInfo) => void
  /**
   * Desktop mode: the strip at the foot of the window (+, the Document name, the tabs, the units). Absent: the tablet's
   * tab switcher in the top bar.
   */
  strip?: { documentName: string; commands: Commands; state: MenuState }
}

type Open = { kind: 'tab'; tab: TabInfo; anchor: Anchor } | { kind: 'add'; anchor: Anchor }

function TabButton({ tab, current, canMenu, icon, onSelect, onMenu }: { tab: TabInfo; current: boolean; canMenu: boolean; icon: boolean; onSelect: () => void; onMenu: (el: HTMLElement) => void }) {
  // read-only: no menu, as for the feature and part rows (every item would be off)
  const press = useLongPress((el) => canMenu && onMenu(el))
  return (
    <button
      type="button"
      className="tab-button"
      aria-current={current ? 'page' : undefined}
      aria-haspopup={canMenu ? 'menu' : undefined}
      data-kind={tab.kind}
      onClick={onSelect}
      {...press}
      // a right click opens the same menu on a mouse (the press's own handler only keeps the browser's menu away)
      onContextMenu={(e) => {
        e.preventDefault()
        if (canMenu) onMenu(e.currentTarget)
      }}
    >
      {icon && <Icon d={tab.kind === 'assembly' ? paths.assembly : paths.tab} px={16} sw={1.5} />}
      {tab.name}
    </button>
  )
}

/**
 * The tab switcher and the add-tab button of the top bar. A short press switches tab; a press held for 500 ms opens
 * the tab's menu (Rename, Delete); a read-only Document opens no menu. The add-tab button opens a choice between a Part Studio and an Assembly.
 */
export function TabBar({ tabs, currentTab, readOnly, onSelect, onAdd, onRename, onDelete, strip }: Props) {
  const [open, setOpen] = useState<Open | null>(null)
  const close = () => setOpen(null)
  const tabButtons = tabs.map((t) => (
    <TabButton
      key={t.id}
      tab={t}
      current={t.id === currentTab}
      canMenu={!readOnly}
      icon={strip !== undefined}
      onSelect={() => onSelect(t.id)}
      onMenu={(el) => setOpen({ kind: 'tab', tab: t, anchor: anchorOf(el) })}
    />
  ))
  return (
    <nav className={strip ? 'tab-strip' : 'top-bar-tabs'} aria-label="Tabs" data-region={strip ? 'tab-strip' : undefined}>
      {strip && (
        <>
          <MenuButton label="Add tab" entries={createMenu} commands={strip.commands} state={strip.state} placement="above" className="strip-plus" ariaLabel="Add tab" title="Create a Part Studio, an Assembly or a Drawing" disabled={readOnly}>
            <Icon d={paths.plus} px={20} sw={1.6} />
          </MenuButton>
          <div className="strip-document" data-testid="strip-document" title={strip.documentName}>
            {strip.documentName}
          </div>
        </>
      )}
      {strip ? <div className="strip-tabs">{tabButtons}</div> : tabButtons}
      {strip && <div className="strip-gap" />}
      {strip && (
        <div className="strip-units" data-testid="strip-units">
          Units: millimetre
        </div>
      )}
      {!strip && (
        <button
          type="button"
          className="icon-button"
          aria-label="Add tab"
          aria-haspopup="menu"
          disabled={readOnly}
          onClick={(e) => setOpen({ kind: 'add', anchor: anchorOf(e.currentTarget) })}
        >
          <Icon d={paths.plus} />
        </button>
      )}
      {open?.kind === 'tab' && (
        <Popover anchor={open.anchor} label={`${open.tab.name} menu`} onClose={close}>
          <PopoverItem
            label="Rename"
            icon={<Icon d={paths.sketch} />}
            onClick={() => {
              close()
              onRename(open.tab)
            }}
          />
          <PopoverItem
            label="Delete"
            icon={<Icon d={paths.trash} />}
            danger
            onClick={() => {
              close()
              onDelete(open.tab)
            }}
          />
        </Popover>
      )}
      {open?.kind === 'add' && (
        <Popover anchor={open.anchor} label="Add tab" onClose={close}>
          <PopoverItem
            label="Part Studio"
            icon={<Icon d={paths.tab} />}
            onClick={() => {
              close()
              onAdd('studio')
            }}
          />
          <PopoverItem
            label="Assembly"
            icon={<Icon d={paths.assembly} />}
            onClick={() => {
              close()
              onAdd('assembly')
            }}
          />
        </Popover>
      )}
    </nav>
  )
}
