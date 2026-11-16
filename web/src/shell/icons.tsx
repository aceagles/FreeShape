import { size } from '../theme/tokens'

/** Icon paths on a 24 x 24 grid, copied from the approved layout. */
export const paths = {
  sketch: 'M4 20l4-1 11-11-3-3L5 16l-1 4z',
  extrude: 'M5 14h14v6H5z M12 11V3 M9 6l3-3 3 3',
  fillet: 'M5 19v-8a6 6 0 0 1 6-6h8',
  plane: 'M3 16l6-9h12l-6 9z',
  mirror: 'M12 3v18 M9 7l-5 5 5 5z M15 7l5 5-5 5z',
  undo: 'M8 5L3 10l5 5 M3 10h11a5 5 0 0 1 0 10h-3',
  redo: 'M16 5l5 5-5 5 M21 10H10a5 5 0 0 0 0 10h3',
  save: 'M5 4h11l3 3v13H5z M8 4v5h7V4 M8 20v-6h8v6',
  collapse: 'M4 5h16v14H4z M9 5v14 M16 9l-3 3 3 3',
  expand: 'M4 5h16v14H4z M9 5v14 M13 9l3 3-3 3',
  close: 'M6 6l12 12 M18 6L6 18',
  tab: 'M4 6h16v12H4z M4 10h7',
  back: 'M15 5l-7 7 7 7',
  plus: 'M12 5v14 M5 12h14',
  trash: 'M5 7h14 M9 7V4h6v3 M7 7l1 13h8l1-13',
  assembly: 'M4 8l8-4 8 4-8 4z M4 8v8l8 4 8-4V8 M12 12v8',
  document: 'M6 3h8l4 4v14H6z M14 3v4h4',
  refresh: 'M20 11a8 8 0 1 0-2 6 M20 4v7h-7',
  lock: 'M6 11h12v9H6z M8 11V8a4 4 0 0 1 8 0v3',
  revolve: 'M12 3v18 M12 6c5 0 7 2 7 6s-2 6-7 6',
  chamfer: 'M5 19v-8l6-6h8',
  shell: 'M4 4h16v16H4z M8 8h8v8H8z',
  hole: 'M12 12m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0 M12 12m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0',
  pattern: 'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z',
  folder: 'M3 7h7l2 2h9v10H3z',
  /** The stand-in for a feature type with no icon of its own yet. */
  generic: 'M12 3l9 9-9 9-9-9z',
  chevronRight: 'M9 6l6 6-6 6',
  chevronDown: 'M6 9l6 6 6-6',
  /** The rollback bar's Drag handle. */
  handle: 'M5 9h14 M5 15h14 M12 3v3 M12 18v3',
} as const

export function Icon({ d, px = size.icon, stroke = 'currentColor', sw = 1.8, round = true }: { d: string; px?: number; stroke?: string; sw?: number; round?: boolean }) {
  return (
    <svg
      width={px}
      height={px}
      viewBox="0 0 24 24"
      fill="none"
      stroke={stroke}
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin={round ? 'round' : undefined}
      aria-hidden="true"
      style={{ flex: 'none', pointerEvents: 'none' }}
    >
      <path d={d} />
    </svg>
  )
}
