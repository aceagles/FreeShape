import { useState } from 'react'
import type { TabInfo } from '../net/connection'
import { Icon, paths } from './icons'
import { useLongPress } from './longPress'
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
}

type Open = { kind: 'tab'; tab: TabInfo; anchor: Anchor } | { kind: 'add'; anchor: Anchor }

function TabButton({ tab, current, canMenu, onSelect, onMenu }: { tab: TabInfo; current: boolean; canMenu: boolean; onSelect: () => void; onMenu: (el: HTMLElement) => void }) {
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
    >
      {tab.name}
    </button>
  )
}

/**
 * The tab switcher and the add-tab button of the top bar. A short press switches tab; a press held for 500 ms opens
 * the tab's menu (Rename, Delete); a read-only Document opens no menu. The add-tab button opens a choice between a Part Studio and an Assembly.
 */
export function TabBar({ tabs, currentTab, readOnly, onSelect, onAdd, onRename, onDelete }: Props) {
  const [open, setOpen] = useState<Open | null>(null)
  const close = () => setOpen(null)
  return (
    <nav className="top-bar-tabs" aria-label="Tabs">
      {tabs.map((t) => (
        <TabButton
          key={t.id}
          tab={t}
          current={t.id === currentTab}
          canMenu={!readOnly}
          onSelect={() => onSelect(t.id)}
          onMenu={(el) => setOpen({ kind: 'tab', tab: t, anchor: anchorOf(el) })}
        />
      ))}
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
