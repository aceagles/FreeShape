/// <reference types="vite/client" />
import { size } from '../theme/tokens'
import { inlinePaths } from './inlinePaths'

// The desktop's icons, exported to SVG by scripts/web/export_icons.py and committed in src/icons.
const urls = import.meta.glob<string>('../icons/*.svg', { eager: true, query: '?url', import: 'default' })

/** The URL of a tool's icon, or null when there is no file for it. */
export function iconUrl(name: string): string | null {
  return urls[`../icons/${name}.svg`] ?? null
}

/** The names of the exported icons. */
export const iconNames = (): string[] => Object.keys(urls).map((k) => k.slice('../icons/'.length, -'.svg'.length))

/** A tool's icon at the strip's icon size: its exported file, else its stroke path (inlinePaths), else an empty box of the same size. */
export function ToolIcon({ name, px = size.icon }: { name: string; px?: number }) {
  const src = iconUrl(name)
  if (src === null) {
    const d = inlinePaths[name]
    if (d === undefined) return <span style={{ width: px, height: px, flex: 'none' }} />
    return (
      <svg width={px} height={px} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" style={{ flex: 'none', pointerEvents: 'none' }} aria-hidden="true">
        <path d={d} />
      </svg>
    )
  }
  return <img src={src} width={px} height={px} alt="" draggable={false} style={{ flex: 'none', pointerEvents: 'none' }} />
}
