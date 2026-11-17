import { useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { ToolIcon } from '../tools/icons'
import { searchCommands } from './paletteSearch'
import type { PaletteEntry } from './paletteEntries'
import './palette.css'

/** The width of the panel, px: the desktop's palette is 520 wide with a 230 list; the chord column takes 60 more. */
const WIDTH = 580

interface Props {
  entries: readonly PaletteEntry[]
  /** The view the panel floats over: it opens at the view's top right, as the desktop's does. */
  anchor: HTMLElement | null
  onClose: () => void
}

/**
 * The Search tools palette (card I9.11; freeshape/ui/palette.py `CommandPalette`): a text box over a list of the tools and
 * menu commands, a name and description on the right. Typing filters and ranks (`searchCommands`); Up and Down move and wrap,
 * Enter runs, Esc closes. A command that cannot run is listed dimmed with its reason and Enter on it does nothing. It is a
 * real text field, so the keymap stands aside while it has the focus (input/keys.ts `isTyping`) and Esc is read here only.
 * Desktop mode draws it; Tablet mode has no key or button that opens it.
 */
export function Palette({ entries, anchor, onClose }: Props) {
  const [text, setText] = useState('')
  const [row, setRow] = useState(0)
  // where it opens: the view's top right, read once when it opens
  const [at] = useState(() => {
    const r = anchor?.getBoundingClientRect()
    return r === undefined ? { left: Math.max(8, window.innerWidth - WIDTH - 16), top: 12 } : { left: Math.max(8, r.right - WIDTH - 16), top: r.top + 12 }
  })
  const root = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const shown = useMemo(() => searchCommands(entries, text), [entries, text])
  const current = shown[Math.min(row, shown.length - 1)]

  useEffect(() => input.current?.focus(), [])
  // a press outside closes it (the desktop's palette hides when the focus leaves it); the Search tools button toggles it itself
  useEffect(() => {
    const outside = (e: Event) => {
      if (!(e.target instanceof Node) || root.current?.contains(e.target)) return
      if (e.target instanceof Element && e.target.closest('[data-palette-toggle]') !== null) return
      onClose()
    }
    document.addEventListener('pointerdown', outside, true)
    return () => document.removeEventListener('pointerdown', outside, true)
  }, [onClose])
  useEffect(() => {
    root.current?.querySelector('[aria-selected="true"]')?.scrollIntoView?.({ block: 'nearest' })
  }, [row, text])

  const run = (entry: PaletteEntry | undefined) => {
    if (entry === undefined || entry.disabled !== '') return
    onClose()
    entry.run()
  }
  const onKeyDown = (e: ReactKeyboardEvent) => {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Enter') {
      e.preventDefault()
      run(current)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      onClose()
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const n = shown.length
      if (n > 0) setRow((Math.min(row, n - 1) + (e.key === 'ArrowDown' ? 1 : -1) + n) % n)
    }
  }

  return (
    <div ref={root} className="palette" role="dialog" aria-label="Search tools" data-testid="palette" style={at}>
      <input
        ref={input}
        className="palette-input"
        type="text"
        role="combobox"
        aria-label="Search tools"
        aria-expanded="true"
        aria-controls="palette-list"
        aria-activedescendant={current === undefined ? undefined : `palette-${current.id}`}
        placeholder="Search tools…"
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setRow(0)
        }}
        onKeyDown={onKeyDown}
      />
      <div className="palette-body">
        <ul className="palette-list" id="palette-list" role="listbox" aria-label="Tools" data-testid="palette-list">
          {shown.map((c, i) => (
            <li
              key={c.id}
              id={`palette-${c.id}`}
              role="option"
              className="palette-row"
              aria-selected={c === current}
              aria-disabled={c.disabled !== '' ? true : undefined}
              data-entry={c.id}
              title={c.disabled !== '' ? c.disabled : undefined}
              // the box keeps the focus: a press on a row must not take it
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                setRow(i)
                run(c)
              }}
            >
              <span className="palette-icon">{c.icon !== null && <ToolIcon name={c.icon} px={18} />}</span>
              <span className="palette-name">{c.name}</span>
              {c.chord !== undefined && <span className="palette-chord">{c.chord}</span>}
            </li>
          ))}
        </ul>
        <div className="palette-about" data-testid="palette-about">
          {current === undefined ? (
            <>
              <div className="palette-title">No tool found</div>
              <div className="palette-desc">Try another word, for example “round” for Fillet or “hole”.</div>
            </>
          ) : (
            <>
              <div className="palette-title">{current.name}</div>
              <div className="palette-desc">
                {current.description}
                {current.disabled !== '' && <p className="palette-reason">{current.disabled}</p>}
              </div>
            </>
          )}
        </div>
      </div>
      <div className="palette-foot">Enter: start the tool  ·  ↑ ↓: choose  ·  Esc: close</div>
    </div>
  )
}
