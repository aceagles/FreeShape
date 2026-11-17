import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react'
import { Icon, paths } from './icons'
import { commandOf, DESKTOP_ONLY, isRunnable, isWired } from './menus'
import type { Commands, MenuEntry, MenuItem, MenuState } from './menus'

interface Props {
  /** The menu's name: the label of the button, and the accessible name of the list. */
  label: string
  entries: readonly MenuEntry[]
  commands: Commands
  state: MenuState
  /** Where the list opens: under the button (the top bar) or over it (the bottom strip). */
  placement?: 'below' | 'above'
  /** Replaces the button's text (an icon). */
  children?: ReactNode
  className?: string
  /** The accessible name of the button, when it is not its text. */
  ariaLabel?: string
  title?: string
  disabled?: boolean
}

/**
 * A menu button with its drop-down list, drawn from data (menus.ts). It opens on a click; the arrow keys move over the
 * items that can run (Home and End jump), Enter or Space runs one, Esc closes it and gives the focus back to the button,
 * and a press outside closes it. An item the client cannot do is shown disabled with the tooltip "Desktop only for now".
 * The keys are read on the list itself while it has the focus, so the page's keymap (input/keys.ts) is not involved.
 */
export function MenuButton({ label, entries, commands, state, placement = 'below', children, className = 'menu-button', ariaLabel, title, disabled }: Props) {
  const [open, setOpen] = useState(false)
  // keyboard use shows the focused item; a mouse does not (the pointer shows its own)
  const [kbd, setKbd] = useState(false)
  // the submenu opened below its item (View > Mouse style), by command
  const [expanded, setExpanded] = useState<string | null>(null)
  const [at, setAt] = useState<{ left: number; top?: number; bottom?: number } | null>(null)
  const root = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const list = useRef<HTMLDivElement>(null)

  const close = (refocus = false) => {
    setOpen(false)
    setKbd(false)
    setExpanded(null)
    if (refocus) button.current?.focus()
  }

  const runnable = (el: Element): boolean => el instanceof HTMLButtonElement && !el.disabled
  const buttons = (): HTMLButtonElement[] => [...(list.current?.querySelectorAll<HTMLButtonElement>('[role^="menuitem"]') ?? [])].filter(runnable)

  // on open: focus the first item that can run
  useEffect(() => {
    if (!open) return
    const first = [...(list.current?.querySelectorAll<HTMLButtonElement>('[role^="menuitem"]') ?? [])].find((b) => !b.disabled)
    ;(first ?? list.current)?.focus()
  }, [open])

  // a press outside closes the menu
  useEffect(() => {
    if (!open) return
    const outside = (e: Event) => {
      if (e.target instanceof Node && root.current?.contains(e.target)) return
      setOpen(false)
      setKbd(false)
    }
    const events = ['pointerdown', 'mousedown', 'click'] as const
    for (const ev of events) document.addEventListener(ev, outside, true)
    return () => {
      for (const ev of events) document.removeEventListener(ev, outside, true)
    }
  }, [open])

  const toggle = () => {
    if (open) return close()
    const r = button.current?.getBoundingClientRect()
    if (r) setAt(placement === 'above' ? { left: Math.max(4, r.left), bottom: window.innerHeight - r.top } : { left: Math.max(4, r.left), top: r.bottom })
    setOpen(true)
  }

  const onKeyDown = (e: ReactKeyboardEvent) => {
    const all = buttons()
    const now = all.indexOf(document.activeElement as HTMLButtonElement)
    const go = (i: number) => {
      e.preventDefault()
      setKbd(true)
      all[(i + all.length) % all.length]?.focus()
    }
    switch (e.key) {
      case 'ArrowDown':
        return go(now + 1)
      case 'ArrowUp':
        return go(now < 0 ? -1 : now - 1)
      case 'Home':
        return go(0)
      case 'End':
        return go(-1)
      case 'Enter':
      case ' ': {
        e.preventDefault()
        if (now >= 0) all[now]!.click()
        return
      }
      case 'Escape':
        e.preventDefault()
        return close(true)
      case 'Tab':
        return close()
    }
  }

  const run = (item: MenuItem) => {
    if (item.items) return setExpanded(expanded === item.command ? null : item.command)
    close(true)
    commandOf(item, commands)?.()
  }

  return (
    <div className="menu" ref={root}>
      <button
        ref={button}
        type="button"
        className={className}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={ariaLabel}
        title={title}
        disabled={disabled}
        onClick={toggle}
      >
        {children ?? label}
      </button>
      {open && (
        <div className="menu-list" role="menu" aria-label={label} ref={list} tabIndex={-1} data-kbd={kbd ? '' : undefined} data-placement={placement} style={at ?? undefined} onKeyDown={onKeyDown}>
          {entries.map((e, i) =>
            e.kind === 'separator' ? (
              <div key={`s${i}`} className="menu-separator" role="separator" />
            ) : (
              <MenuGroup key={e.command} item={e} open={expanded === e.command} commands={commands} state={state} run={run} />
            ),
          )}
        </div>
      )}
    </div>
  )
}

/** An item, and below it, when it is open, its submenu's items (indented). */
function MenuGroup({ item, open, commands, state, run }: { item: MenuItem; open: boolean; commands: Commands; state: MenuState; run: (item: MenuItem) => void }) {
  const row = <MenuRow item={item} checked={checkedOf(item, state)} on={isRunnable(item, commands, state)} wired={isWired(item, commands)} expanded={item.items ? open : undefined} onRun={() => run(item)} />
  if (!item.items || !open) return row
  return (
    <>
      {row}
      {item.items.map((s, i) =>
        s.kind === 'separator' ? (
          <div key={`s${i}`} className="menu-separator" role="separator" />
        ) : (
          <MenuRow key={s.command} item={s} checked={checkedOf(s, state)} sub on={isRunnable(s, commands, state)} wired={isWired(s, commands)} onRun={() => run(s)} />
        ),
      )}
    </>
  )
}

/** A check item's mark: the state the shell keeps for its command, else the item's own. */
const checkedOf = (item: MenuItem, state: MenuState): boolean => state.checks?.[item.command] ?? item.checked === true

function MenuRow({ item, checked, on, wired, onRun, sub, expanded }: { item: MenuItem; checked: boolean; on: boolean; wired: boolean; onRun: () => void; sub?: boolean; expanded?: boolean }) {
  return (
    <button
      type="button"
      role={item.check ? 'menuitemcheckbox' : 'menuitem'}
      aria-checked={item.check ? checked : undefined}
      aria-haspopup={item.submenu ? 'menu' : undefined}
      aria-expanded={expanded}
      className={sub ? 'menu-item menu-subitem' : 'menu-item'}
      // a submenu's items sit one mark's width in from their item
      style={sub ? { paddingLeft: 24 } : undefined}
      data-command={item.command}
      disabled={!on}
      title={wired ? undefined : (item.note ?? DESKTOP_ONLY)}
      tabIndex={-1}
      onClick={onRun}
    >
      <span className="menu-mark">{item.check && checked && <Icon d={paths.check} px={14} sw={2.2} />}</span>
      <span className="menu-label">{item.label}</span>
      {item.chord && <span className="menu-chord">{item.chord}</span>}
      {item.submenu && (
        <span className="menu-chevron" style={{ display: 'flex', transform: expanded ? 'rotate(90deg)' : undefined }}>
          <Icon d={paths.chevronRight} px={14} />
        </span>
      )}
    </button>
  )
}

/** Where a context menu opens (the pointer, in window coordinates), what it lists and what its items run. */
export interface ContextSpec {
  x: number
  y: number
  /** The accessible name of the list. */
  label: string
  entries: readonly MenuEntry[]
  commands: Commands
  /** What the items' enabled rules read; none of the context menus' rules needs more than this. */
  state?: MenuState
}

const NO_STATE: MenuState = { readOnly: false, tabKind: null }
/** The gap a context menu keeps from the window's edge, px. */
const EDGE = 4

/**
 * A context menu (card I9.10): the Menu component's list, opened at the pointer and kept inside the window. It opens
 * under the pointer, over it when there is no room below, and is moved in from the right and the bottom edge when it
 * still does not fit. The items, keys and tooltips are the menu bar's (MenuGroup); a press outside, Esc, a scroll or a
 * resize closes it.
 */
export function ContextMenu({ at, onClose }: { at: ContextSpec; onClose: () => void }) {
  const list = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  const [kbd, setKbd] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const state = at.state ?? NO_STATE

  // measured once the list is drawn (hidden until then), and again when a submenu opens in it
  useLayoutEffect(() => {
    const r = list.current?.getBoundingClientRect()
    if (!r) return
    const w = window.innerWidth
    const h = window.innerHeight
    const left = Math.max(EDGE, Math.min(at.x, w - r.width - EDGE))
    const below = at.y + r.height + EDGE <= h
    const top = below ? at.y : at.y - r.height >= EDGE ? at.y - r.height : Math.max(EDGE, h - r.height - EDGE)
    setPos((p) => (p && p.left === left && p.top === top ? p : { left, top }))
  }, [at, expanded])

  useEffect(() => {
    const first = [...(list.current?.querySelectorAll<HTMLButtonElement>('[role^="menuitem"]') ?? [])].find((b) => !b.disabled)
    ;(first ?? list.current)?.focus({ preventScroll: true })
  }, [at])

  useEffect(() => {
    const outside = (e: Event) => {
      if (e.target instanceof Node && list.current?.contains(e.target)) return
      onClose()
    }
    const events = ['pointerdown', 'mousedown', 'contextmenu'] as const
    for (const ev of events) document.addEventListener(ev, outside, true)
    // Esc closes it wherever the focus is (a mouse press on a row may leave the focus outside the list)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', esc)
    window.addEventListener('resize', onClose)
    window.addEventListener('blur', onClose)
    window.addEventListener('wheel', onClose, { passive: true })
    return () => {
      for (const ev of events) document.removeEventListener(ev, outside, true)
      document.removeEventListener('keydown', esc)
      window.removeEventListener('resize', onClose)
      window.removeEventListener('blur', onClose)
      window.removeEventListener('wheel', onClose)
    }
  }, [onClose])

  const run = (item: MenuItem) => {
    if (item.items) return setExpanded(expanded === item.command ? null : item.command)
    onClose()
    commandOf(item, at.commands)?.()
  }

  const onKeyDown = (e: ReactKeyboardEvent) => {
    const all = [...(list.current?.querySelectorAll<HTMLButtonElement>('[role^="menuitem"]') ?? [])].filter((b) => !b.disabled)
    const now = all.indexOf(document.activeElement as HTMLButtonElement)
    const go = (i: number) => {
      e.preventDefault()
      setKbd(true)
      all[(i + all.length) % all.length]?.focus()
    }
    switch (e.key) {
      case 'ArrowDown':
        return go(now + 1)
      case 'ArrowUp':
        return go(now < 0 ? -1 : now - 1)
      case 'Home':
        return go(0)
      case 'End':
        return go(-1)
      case 'Enter':
      case ' ':
        e.preventDefault()
        if (now >= 0) all[now]!.click()
        return
      case 'Escape':
      case 'Tab':
        e.preventDefault()
        return onClose()
    }
  }

  return (
    <div
      className="menu-list context-menu"
      role="menu"
      aria-label={at.label}
      ref={list}
      tabIndex={-1}
      data-kbd={kbd ? '' : undefined}
      data-testid="context-menu"
      style={pos ?? { left: at.x, top: at.y, visibility: 'hidden' }}
      onKeyDown={onKeyDown}
    >
      {at.entries.map((e, i) =>
        e.kind === 'separator' ? (
          <div key={`s${i}`} className="menu-separator" role="separator" />
        ) : (
          <MenuGroup key={e.command} item={e} open={expanded === e.command} commands={at.commands} state={state} run={run} />
        ),
      )}
    </div>
  )
}

/** The place to open a context menu from, and the list to draw: `open` shows one at the pointer, `node` goes once in the owner's output. */
export function useContextMenu(): { open: (spec: ContextSpec) => void; node: ReactNode } {
  const [at, setAt] = useState<ContextSpec | null>(null)
  const close = useCallback(() => setAt(null), [])
  return { open: setAt, node: at === null ? null : <ContextMenu at={at} onClose={close} /> }
}
