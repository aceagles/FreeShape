import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { ReactNode } from 'react'
import type { MouseEvent, PointerEvent } from 'react'
import { Icon, paths } from '../shell/icons'
import { StripButton } from '../shell/StripButton'
import { groupTools, pinCap } from './defs'
import type { StripDef, StripKind, ToolDef } from './defs'
import { ToolIcon } from './icons'
import { userPins } from './pins'
import type { PinStore } from './pins'
import type { ToolState } from './useToolTypes'
import './tools.css'

export const DESKTOP_ONLY_NOTE = 'Desktop only for now'
export const STRIP_FULL_TEXT = 'The strip is full. Unpin a tool to pin another.'

/** More rows than this and a group's list is two columns, as the layout's Constrain list is. */
const ONE_COLUMN_ROWS = 7

export interface ToolStripProps {
  strip: StripKind
  defs: readonly StripDef[]
  /** What a tool can do now (see useToolTypes). */
  stateOf: (id: string) => ToolState
  /** The whole strip is off (a dialog is open, no Part Studio is on screen). */
  disabled?: boolean
  /** The window's width: it sets how many pins fit. */
  width: number
  /** The tool that is on (a sketch strip's drawing tool). */
  active?: string | null
  /** Whether a toggle tool (the sketch strip's Construct) is on: true or false for a toggle, undefined for any other tool. A toggle that is on is lit and aria-pressed. */
  toggled?: (id: string) => boolean | undefined
  /** Tools to mark with a small dot (a hint only: the Constrain tools that fit the selection). */
  marked?: (id: string) => boolean
  /** A tool starts: a tap on it, or a release on its row. */
  onStart: (id: string) => void
  pins?: PinStore
  /** What goes at the right end of the strip (the sketch strip's 2D / 3D toggle, Normal to, Cancel and Done). */
  trailing?: ReactNode
}

interface Open {
  key: string
  left: number
}

/**
 * The tool strip: single tools, groups and pins, each button with an icon and a label. A group button opens its list
 * when pressed; the row under the pen or finger is lit while the press is held, and releasing on a row starts that tool.
 * Releasing on the group button leaves the list open, so a tap and then a tap on a row works too; releasing anywhere
 * else closes it. Each row has a pin button: a pinned tool is also a button on the strip after the groups, up to a cap
 * that keeps the strip from overflowing. One component for the feature strip and, later, the sketch strip.
 */
export function ToolStrip({ strip, defs, stateOf, disabled, width, active, toggled, marked, onStart, pins = userPins(), trailing }: ToolStripProps) {
  const pinned = useSyncExternalStore(pins.subscribe, pins.getSnapshot)[strip]
  const [openRaw, setOpen] = useState<Open | null>(null)
  // a strip that is switched off (a dialog opened) shows no list
  const open = disabled ? null : openRaw
  const [aim, setAim] = useState<string | null>(null)
  // whether a press that opened a list is still held (read by handlers only, so it is not state)
  const held = useRef(false)

  const cap = pinCap(strip, width)
  // the pins that have a button, in the order of the groups; a stored pin the strip no longer has is not counted
  const shown = groupTools(defs).filter((t) => pinned.includes(t.id))
  const full = shown.length >= cap
  const pinnedButtons = shown.slice(0, cap)

  // a release anywhere that no handler below took (outside the list, off the screen) ends the press and closes the list
  useEffect(() => {
    if (open === null) return
    const end = () => {
      if (!held.current) return
      held.current = false
      setAim(null)
      setOpen(null)
    }
    window.addEventListener('pointerup', end)
    window.addEventListener('pointercancel', end)
    return () => {
      window.removeEventListener('pointerup', end)
      window.removeEventListener('pointercancel', end)
    }
  }, [open])

  const close = () => {
    held.current = false
    setAim(null)
    setOpen(null)
  }
  const start = (tool: ToolDef) => {
    close()
    if (!disabled && stateOf(tool.id) === 'ok') onStart(tool.id)
  }
  const togglePin = (tool: ToolDef) => {
    if (full && !pinned.includes(tool.id)) return
    pins.toggle(strip, tool.id)
  }
  const release = () => {
    held.current = false
    setAim(null)
  }

  const toolButton = (tool: ToolDef, extra: { gap?: number; pinned?: boolean } = {}) => {
    const state = disabled ? 'off' : stateOf(tool.id)
    return (
      <StripButton
        key={`tool:${tool.id}${extra.pinned ? ':pin' : ''}`}
        label={tool.label}
        icon={<ToolIcon name={tool.icon} />}
        active={active === tool.id || toggled?.(tool.id) === true}
        aria-pressed={toggled?.(tool.id)}
        gap={(extra.gap ?? 0) > 0}
        pinned={extra.pinned && pinnedButtons[0]?.id === tool.id}
        data-tool={tool.id}
        data-state={state}
        title={state === 'desktop' ? `${tool.label}: ${DESKTOP_ONLY_NOTE.toLowerCase()}` : undefined}
        disabled={state === 'off' || state === 'desktop'}
        aria-disabled={state === 'dimmed' ? true : undefined}
        onClick={() => start(tool)}
      />
    )
  }

  const groupButton = (d: Extract<StripDef, { kind: 'group' }>) => {
    const isOpen = open?.key === d.key
    const usable = !disabled && d.tools.some((t) => stateOf(t.id) !== 'off')
    return (
      <StripButton
        key={`group:${d.key}`}
        label={d.name}
        icon={<ToolIcon name={d.tools[0]!.icon} />}
        group
        gap={(d.gap ?? 0) > 0}
        active={isOpen || (active != null && d.tools.some((t) => t.id === active))}
        data-group={d.key}
        data-group-part=""
        aria-expanded={isOpen}
        disabled={!usable}
        onPointerDown={(e: PointerEvent<HTMLButtonElement>) => {
          if (e.pointerType === 'mouse' && e.button !== 0) return
          // the touch that pressed is captured by this button: let go of it, so the rows under the finger see it move
          try {
            e.currentTarget.releasePointerCapture(e.pointerId)
          } catch {
            // no capture to release
          }
          held.current = true
          setAim(null)
          setOpen({ key: d.key, left: e.currentTarget.offsetLeft })
        }}
        onPointerUp={release}
        onClick={(e: MouseEvent<HTMLButtonElement>) => {
          // a click with no pointer (the keyboard) opens it too
          if (e.detail === 0) setOpen({ key: d.key, left: e.currentTarget.offsetLeft })
        }}
      />
    )
  }

  const list = (d: Extract<StripDef, { kind: 'group' }>, left: number) => (
    <div
      key="list"
      className="group-list"
      data-region="group-list"
      data-group-part=""
      role="menu"
      aria-label={d.name}
      style={{ left, gridTemplateColumns: `repeat(${d.tools.length > ONE_COLUMN_ROWS ? 2 : 1}, 216px)` }}
    >
      {d.tools.map((tool) => {
        const state = stateOf(tool.id)
        const on = pinned.includes(tool.id)
        const blocked = full && !on
        return (
          <div
            key={tool.id}
            className="group-row"
            data-tool={tool.id}
            data-state={state}
            data-fit={marked?.(tool.id) ? '' : undefined}
            data-lit={active === tool.id ? '' : undefined}
            data-aim={aim === tool.id && state === 'ok' ? '' : undefined}
            onPointerEnter={() => held.current && setAim(tool.id)}
            onPointerMove={() => held.current && aim !== tool.id && setAim(tool.id)}
          >
            <button
              type="button"
              role="menuitem"
              className="group-tool"
              aria-disabled={state !== 'ok' ? true : undefined}
              onPointerUp={(e) => {
                e.stopPropagation()
                // eslint-disable-next-line react-hooks/refs -- called from the handler, not in render
                release()
                // eslint-disable-next-line react-hooks/refs -- the same
                start(tool)
              }}
              onClick={(e) => {
                if (e.detail === 0) start(tool)
              }}
            >
              <ToolIcon name={tool.icon} />
              <span className="group-text">
                <span className="group-name">{tool.label}</span>
                {state === 'desktop' && <span className="group-note">{DESKTOP_ONLY_NOTE}</span>}
              </span>
            </button>
            <button
              type="button"
              className="pin-button"
              aria-label={`${on ? 'Unpin' : 'Pin'} ${tool.label}`}
              aria-pressed={on}
              data-dim={blocked ? '' : undefined}
              onPointerUp={(e) => {
                e.stopPropagation()
                release()
                togglePin(tool)
              }}
              onClick={(e) => {
                if (e.detail === 0) togglePin(tool)
              }}
            >
              <Icon d={paths.pin} px={22} sw={1.8} fill={on ? 'currentColor' : 'none'} />
            </button>
          </div>
        )
      })}
      {full && (
        <div className="group-full" data-testid="strip-full">
          {STRIP_FULL_TEXT}
        </div>
      )}
    </div>
  )

  const openDef = open === null ? null : defs.find((d): d is Extract<StripDef, { kind: 'group' }> => d.kind === 'group' && d.key === open.key)

  return (
    <div
      className="tool-strip"
      data-region="tool-strip"
      data-strip={strip}
      role="toolbar"
      aria-label="Tools"
      // a press on a button of the strip that is not part of the open group ends it
      onPointerDown={(e) => {
        if (open !== null && !(e.target as Element).closest('[data-group-part]')) close()
      }}
    >
      {defs.map((d) => (d.kind === 'tool' ? toolButton(d.tool, { gap: d.gap }) : groupButton(d)))}
      {pinnedButtons.map((t) => toolButton(t, { pinned: true }))}
      {trailing}
      {openDef && open !== null && list(openDef, open.left)}
      {open !== null && <div className="strip-scrim" data-testid="strip-scrim" onPointerDown={close} />}
    </div>
  )
}
