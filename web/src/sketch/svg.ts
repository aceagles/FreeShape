import { color } from '../theme/tokens'
import { inlinePaths } from '../tools/inlinePaths'
import type { P, ScreenDim, ScreenGlyph, ScreenLayout, ScreenStroke } from './layout'
import { DIM } from './layout'
import { handleMarkup } from './handle'
import type { HandleSpot } from './handle'
import type { SketchHit } from './pick'
import type { Status } from './wire'

/** Colours of the sketch: the desktop's (freeshape/ui/theme.py SKETCH_*) as the layout mock's sketch state draws them. */
export const sketchColor = {
  free: color.accent,
  defined: color.ink,
  conflicting: '#d92620',
  construction: '#73808f',
  region: '#669ef2',
  regionAlpha: 0.16,
  axis: '#b9c1cb',
  dim: '#3c424c',
  selected: color.selection,
  aimed: '#ff9e33',
  glyph: color.ok,
  glyphInk: color.okDark,
} as const

const STROKE_WIDTH = 2
const POINT_RADIUS = 4

const n = (v: number): string => String(Math.round(v * 100) / 100)
const path = (list: readonly P[], closed: boolean): string => `M${list.map((p) => `${n(p.x)} ${n(p.y)}`).join('L')}${closed ? 'Z' : ''}`
const esc = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export interface SketchLook {
  selected: readonly SketchHit[]
  aimed: SketchHit | null
  /** The Drag handles of the selected items, and the one held. */
  handles?: readonly HandleSpot[]
  grabbed?: SketchHit | null
}

const colourOf = (s: ScreenStroke | { status: Status; construction?: boolean }): string =>
  s.status === 'conflicting' ? sketchColor.conflicting : s.construction ? sketchColor.construction : s.status === 'defined' ? sketchColor.defined : sketchColor.free

function strokeMarkup(s: ScreenStroke, look: SketchLook): string {
  const here = (h: SketchHit | null) => h !== null && h.kind === 'entity' && h.id === s.id
  const sel = look.selected.some(here)
  const aim = here(look.aimed)
  const d = s.pts.length === 1 ? '' : path(s.pts, s.closed)
  const attrs = `data-kind="${s.kind}" data-id="${esc(s.id)}" data-status="${s.status}"${s.construction ? ' data-dashed=""' : ''}`
  if (s.kind === 'point') {
    const p = s.pts[0]!
    return `<circle ${attrs} cx="${n(p.x)}" cy="${n(p.y)}" r="${POINT_RADIUS + 1}" fill="${colourOf(s)}" stroke="${sel ? sketchColor.selected : 'none'}" stroke-width="3"/>`
  }
  const under = sel || aim ? `<path d="${d}" fill="none" stroke="${sel ? sketchColor.selected : sketchColor.aimed}" stroke-width="7" stroke-opacity="0.6" stroke-linecap="round" stroke-linejoin="round"/>` : ''
  const dash = s.construction ? ' stroke-dasharray="8 6"' : ''
  return `${under}<path ${attrs} d="${d}" fill="none" stroke="${colourOf(s)}" stroke-width="${s.construction ? 1.5 : STROKE_WIDTH}"${dash} stroke-linecap="round" stroke-linejoin="round"/>`
}

function arrow(tip: P, dir: P): string {
  const base = { x: tip.x - dir.x * DIM.arrowLength, y: tip.y - dir.y * DIM.arrowLength }
  const a = { x: base.x - dir.y * DIM.arrowHalf, y: base.y + dir.x * DIM.arrowHalf }
  const b = { x: base.x + dir.y * DIM.arrowHalf, y: base.y - dir.x * DIM.arrowHalf }
  return `M${n(a.x)} ${n(a.y)}L${n(tip.x)} ${n(tip.y)}L${n(b.x)} ${n(b.y)}`
}

function dimMarkup(d: ScreenDim, look: SketchLook): string {
  const sel = look.selected.some((h) => h.kind === 'dimension' && h.id === d.id)
  const aim = look.aimed?.kind === 'dimension' && look.aimed.id === d.id
  const ink = d.ok ? sketchColor.dim : sketchColor.conflicting
  const lines = d.segs.map(([a, b]) => `M${n(a.x)} ${n(a.y)}L${n(b.x)} ${n(b.y)}`).join('')
  const arc = d.arc.length > 1 ? path(d.arc, false) : ''
  const heads = d.arrows.map((a) => arrow(a.tip, a.dir)).join('')
  const l = d.label
  const edge = sel ? color.accent : aim ? sketchColor.aimed : d.ok ? color.line : sketchColor.conflicting
  return (
    `<g data-kind="dimension" data-id="${esc(d.id)}"${d.ok ? '' : ' data-status="conflicting"'}>` +
    `<path d="${lines}${arc}${heads}" fill="none" stroke="${ink}" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>` +
    `<rect x="${n(l.x)}" y="${n(l.y)}" width="${n(l.w)}" height="${n(l.h)}" rx="6" fill="#fff" stroke="${edge}" stroke-width="${sel ? 2 : 1}"/>` +
    `<text x="${n(l.x + l.w / 2)}" y="${n(l.y + l.h / 2 + 5)}" fill="${d.ok ? color.ink : sketchColor.conflicting}" font-size="14" font-weight="500" text-anchor="middle">${esc(l.text)}</text></g>`
  )
}

function glyphMarkup(g: ScreenGlyph, look: SketchLook): string {
  const sel = look.selected.some((h) => h.kind === 'constraint' && h.id === g.id)
  const aim = look.aimed?.kind === 'constraint' && look.aimed.id === g.id
  const edge = !g.ok ? sketchColor.conflicting : sel ? color.accent : aim ? sketchColor.aimed : sketchColor.glyph
  const ink = g.ok ? sketchColor.glyphInk : sketchColor.conflicting
  const d = inlinePaths[g.type]
  const half = g.size / 2
  // the tool's 24 px icon at three quarters, centred in the box
  const icon = d ? `<path d="${d}" transform="translate(${n(g.x)} ${n(g.y)}) scale(0.75) translate(-12 -12)" fill="none" stroke="${ink}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>` : ''
  return (
    `<g data-kind="constraint" data-id="${esc(g.id)}" data-type="${esc(g.type)}"${g.ok ? '' : ' data-status="conflicting"'}>` +
    `<rect x="${n(g.x - half)}" y="${n(g.y - half)}" width="${g.size}" height="${g.size}" rx="4" fill="#fff" stroke="${edge}" stroke-width="${sel ? 2 : 1}"/>${icon}</g>`
  )
}

/** The sketch as SVG markup for a view of this size (CSS pixels). */
export function sketchMarkup(layout: ScreenLayout, look: SketchLook, width: number, height: number): string {
  const out: string[] = []
  for (const [a, b] of layout.axes) out.push(`<path d="M${n(a.x)} ${n(a.y)}L${n(b.x)} ${n(b.y)}" fill="none" stroke="${sketchColor.axis}" stroke-width="1" stroke-dasharray="2 6" data-kind="axis"/>`)
  if (layout.regions.length > 0) {
    const d = layout.regions.map((r) => path(r, true)).join('')
    out.push(`<path d="${d}" fill="${sketchColor.region}" fill-opacity="${sketchColor.regionAlpha}" fill-rule="evenodd" stroke="none" data-kind="regions"/>`)
  }
  // construction below, real curves above
  for (const s of layout.strokes) if (s.construction) out.push(strokeMarkup(s, look))
  for (const s of layout.strokes) if (!s.construction) out.push(strokeMarkup(s, look))
  for (const p of layout.points) {
    const isSel = look.selected.some((h) => h.kind === 'point' && h.id === p.id)
    const isAim = look.aimed?.kind === 'point' && look.aimed.id === p.id
    const c = p.status === 'conflicting' ? sketchColor.conflicting : p.status === 'defined' ? sketchColor.defined : sketchColor.free
    if (isSel) out.push(`<circle data-kind="point" data-id="${esc(p.id)}" data-selected="" cx="${n(p.x)}" cy="${n(p.y)}" r="9" fill="${sketchColor.selected}" stroke="${color.ink}" stroke-width="2"/>`)
    else if (isAim) out.push(`<circle data-kind="point" data-id="${esc(p.id)}" data-aimed="" cx="${n(p.x)}" cy="${n(p.y)}" r="8" fill="${sketchColor.aimed}" stroke="${color.ink}" stroke-width="1.5"/>`)
    else if (p.origin) out.push(`<circle data-kind="point" data-id="${esc(p.id)}" cx="${n(p.x)}" cy="${n(p.y)}" r="${POINT_RADIUS}" fill="#fff" stroke="${c}" stroke-width="2"/>`)
    else out.push(`<circle data-kind="point" data-id="${esc(p.id)}" data-status="${p.status}" cx="${n(p.x)}" cy="${n(p.y)}" r="${POINT_RADIUS}" fill="${c}"/>`)
  }
  for (const d of layout.dims) out.push(dimMarkup(d, look))
  for (const g of layout.glyphs) out.push(glyphMarkup(g, look))
  if (look.handles) out.push(handleMarkup(look.handles, look.grabbed ?? null))
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Sketch">${out.join('')}</svg>`
}

