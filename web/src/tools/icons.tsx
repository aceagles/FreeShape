/// <reference types="vite/client" />
import { size } from '../theme/tokens'

// The desktop's icons, exported to SVG by scripts/web/export_icons.py and committed in src/icons.
const urls = import.meta.glob<string>('../icons/*.svg', { eager: true, query: '?url', import: 'default' })

/** The URL of a tool's icon, or null when there is no file for it. */
export function iconUrl(name: string): string | null {
  return urls[`../icons/${name}.svg`] ?? null
}

/** The names of the exported icons. */
export const iconNames = (): string[] => Object.keys(urls).map((k) => k.slice('../icons/'.length, -'.svg'.length))

/** A tool's icon at the strip's icon size. A tool with no icon file shows an empty box of the same size. */
export function ToolIcon({ name, px = size.icon }: { name: string; px?: number }) {
  const src = iconUrl(name)
  return src === null ? <span style={{ width: px, height: px, flex: 'none' }} /> : <img src={src} width={px} height={px} alt="" draggable={false} style={{ flex: 'none', pointerEvents: 'none' }} />
}
