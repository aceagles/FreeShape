// Builds web/design/layout.html: the approved tablet layout as one static page, every state at both
// screen sizes. A port of plans/browser-client/layout-mock/Main.dc.html (its template and the
// renderVals() that fills it). Every size, space, radius and colour comes from ./tokens.ts.
// Run `npm run design:build` after a change; src/theme/layout.test.ts fails if the file is stale.
import { color as c, fontFamily, fontSize as fs, maxPinned, radius as r, screens, size as z, space as sp } from './tokens.ts'
import type { ScreenKey } from './tokens.ts'

export const modes = ['browsing', 'flyout', 'dialog', 'sketch', 'constrain', 'radial', 'collapsed'] as const
export type Mode = (typeof modes)[number]

export const modeTitles: Record<Mode, string> = {
  browsing: 'Browsing',
  flyout: 'Tool group open',
  dialog: 'Feature dialog open',
  sketch: 'Sketching',
  constrain: 'Constraint group open',
  radial: 'Sketch quick menu',
  collapsed: 'Panel collapsed',
}

/** The anchor id of one state at one size, e.g. `dialog-1024`. */
export const frameId = (mode: Mode, key: ScreenKey): string => `${mode}-${key}`

const px = (n: number): string => `${n}px`

// Icon paths, one per tool (copied from the mock).
const D: Record<string, string> = {
  Sketch: 'M4 20l4-1 11-11-3-3L5 16l-1 4z',
  Extrude: 'M5 14h14v6H5z M12 11V3 M9 6l3-3 3 3',
  Revolve: 'M12 3v18 M12 6c5 0 7 2 7 6s-2 6-7 6',
  Fillet: 'M5 19v-8a6 6 0 0 1 6-6h8',
  Chamfer: 'M5 19v-8l6-6h8',
  Shell: 'M4 4h16v16H4z M8 8h8v8H8z',
  Hole: 'M12 12m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0 M12 12m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0',
  Plane: 'M3 16l6-9h12l-6 9z',
  Mirror: 'M12 3v18 M9 7l-5 5 5 5z M15 7l5 5-5 5z',
  Pattern: 'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z',
  Select: 'M6 3l12 9-6 1-3 6z',
  Line: 'M5 19L19 5',
  Rectangle: 'M4 6h16v12H4z',
  Circle: 'M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0',
  Arc: 'M4 18A14 14 0 0 1 18 4',
  Point: 'M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0',
  Dimension: 'M4 6v12 M20 6v12 M4 12h16 M7 9l-3 3 3 3 M17 9l3 3-3 3',
  Construct: 'M4 20l3-3 M10 14l4-4 M17 7l3-3',
  Delete: 'M5 7h14 M9 7V4h6v3 M7 7l1 13h8l1-13',
  Coincident: 'M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0 M4 20l5-5 M20 4l-5 5',
  Horizontal: 'M4 12h16',
  Vertical: 'M12 4v16',
  Parallel: 'M6 20l6-16 M12 20l6-16',
  Perpendicular: 'M4 19h16 M12 19V5',
  Tangent: 'M4 5h16 M12 13m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0',
  Equal: 'M5 9h14 M5 15h14',
  Midpoint: 'M4 18h16 M12 18l-4-7h8z',
  Concentric: 'M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0 M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0',
  Symmetric: 'M12 3v18 M5 8l3 4-3 4 M19 8l-3 4 3 4',
  Fix: 'M12 4v12 M6 16h12 M8 20l2-4 M12 20l2-4 M16 20l2-4',
  Normal: 'M4 19c4-10 12-10 16 0 M12 11V3',
  'Point on': 'M4 18L20 6 M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0',
}
const ICON_FOLDER = 'M3 7h7l2 2h9v10H3z'

interface IconOpts {
  size?: number
  sw?: number
  cap?: boolean
  join?: boolean
  stroke?: string
  extra?: string
}
const ico = (d: string, o: IconOpts = {}): string => {
  const n = o.size ?? z.icon
  const style = [
    `stroke-width: ${o.sw ?? 1.8}`,
    o.cap === false ? '' : 'stroke-linecap: round',
    o.join === false ? '' : 'stroke-linejoin: round',
    o.extra ?? '',
  ]
    .filter(Boolean)
    .join('; ')
  return `<svg width="${n}" height="${n}" viewBox="0 0 24 24" fill="none" stroke="${o.stroke ?? 'currentColor'}" style="${style}" aria-hidden="true"><path d="${d}"></path></svg>`
}

interface ButtonOpts {
  /** Extra leading style declarations. */
  extra?: string
  attrs?: string
  bg?: string
  color?: string
  cursor?: string
}
/** A 44 x 44 icon button, the shape most buttons in the mock take. */
const iconButton = (label: string, svg: string, o: ButtonOpts = {}): string =>
  `<button aria-label="${label}"${o.attrs ?? ''} style="${o.extra ?? ''}width: ${px(z.touchMin)}; height: ${px(z.touchMin)}; flex: none; display: flex; align-items: center; justify-content: center; border: none; border-radius: ${px(r.sm)}; background: ${o.bg ?? 'transparent'}; color: ${o.color ?? c.inkSoft}; cursor: ${o.cursor ?? 'pointer'}">${svg}</button>`

interface Item {
  label: string
  d: string
  op: number
  bg: string
  pinned: boolean
  pinName: string
  pinBg: string
  pinFg: string
  pinFill: string
  pinOp: number
}
interface StripEntry {
  label: string
  d: string
  gap: number
  multi: boolean
  open: boolean
  cols: number
  bg: string
  bd: string
  fg: string
  op: number
  full: boolean
  items: Item[]
}
interface Def {
  key?: string
  name?: string
  cols?: number
  gap?: number
  items: string[]
}

const stripButton = (g: StripEntry): string => {
  const expanded = g.multi ? ` aria-expanded="${g.open}"` : ''
  const corner = g.multi
    ? `<svg width="8" height="8" viewBox="0 0 8 8" style="position: absolute; right: 5px; bottom: 5px; pointer-events: none" aria-hidden="true"><path d="M8 0v8H0z" fill="${c.inkSoft}"></path></svg>`
    : ''
  const list = g.open ? groupList(g) : ''
  return `<div style="position: relative; flex: none; margin-left: ${px(g.gap)}">
        <button${expanded} style="position: relative; width: ${px(z.toolButton.width)}; height: ${px(z.toolButton.height)}; box-sizing: border-box; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: ${px(sp.s3)}; border: 1px solid ${g.bd}; border-radius: ${px(r.sm)}; background: ${g.bg}; color: ${g.fg}; opacity: ${g.op}; font: inherit; font-size: ${px(fs.caption)}; font-weight: 500; cursor: pointer">
          ${ico(g.d, { extra: 'pointer-events: none' })}
          <span>${g.label}</span>
          ${corner}
        </button>
        ${list}
      </div>`
}

const groupList = (g: StripEntry): string => {
  const rows = g.items
    .map(
      (it) => `<div style="display: flex; align-items: center; border-radius: ${px(r.sm)}; background: ${it.bg}">
            <button style="flex: 1; min-width: 0; height: ${px(z.groupRow)}; display: flex; align-items: center; gap: ${px(sp.s12)}; padding: 0 ${px(sp.s4)} 0 ${px(sp.s12)}; border: none; border-radius: ${px(r.sm)}; background: transparent; color: ${c.ink}; opacity: ${it.op}; font: inherit; font-size: ${px(fs.title)}; font-weight: 500; text-align: left; white-space: nowrap; cursor: pointer">
              ${ico(it.d, { stroke: c.inkSoft, extra: 'flex: none' })}
              <span>${it.label}</span>
            </button>
            <button aria-label="${it.pinName}" aria-pressed="${it.pinned}" style="width: ${px(z.pinButton)}; height: ${px(z.pinButton)}; flex: none; margin-right: ${px(sp.s4)}; display: flex; align-items: center; justify-content: center; border: none; border-radius: ${px(r.sm)}; background: ${it.pinBg}; color: ${it.pinFg}; opacity: ${it.pinOp}; cursor: pointer">
              <svg width="${z.iconSmall}" height="${z.iconSmall}" viewBox="0 0 24 24" fill="${it.pinFill}" stroke="currentColor" style="stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round" aria-hidden="true"><path d="M9 4h6l-1 6 3 3H7l3-3z M12 13v7"></path></svg>
            </button>
          </div>`,
    )
    .join('\n          ')
  const full = g.full
    ? `<div style="grid-column: 1 / -1; padding: ${px(sp.s8)} ${px(sp.s12)}; font-size: ${px(fs.small)}; color: ${c.muted}">The strip is full. Unpin a tool to pin another.</div>`
    : ''
  return `<div data-region="group-list" style="position: absolute; left: 0; top: ${px(z.groupListOffset)}; z-index: 10; display: grid; grid-template-columns: repeat(${g.cols}, ${px(z.groupColumnWidth)}); gap: ${px(sp.s2)}; padding: ${px(sp.s4)}; border: 1px solid ${c.line}; border-radius: ${px(r.lg)}; background: ${c.white}; box-shadow: 0 8px 24px ${c.shadow}">
          ${rows}
          ${full}
        </div>`
}

/** The strip's entries for one state. Mirrors renderVals() in the mock. */
const stripFor = (mode: Mode, key: ScreenKey, sketching: boolean): StripEntry[] => {
  const dimmed: Record<string, true> = sketching
    ? { Horizontal: true, Vertical: true, Parallel: true, Perpendicular: true, Tangent: true, Equal: true, Concentric: true, Symmetric: true, Normal: true }
    : { Revolve: true }
  const active = sketching ? 'Select' : ''
  const defs: Def[] = sketching
    ? [
        { items: ['Select'] },
        { key: 'draw', name: 'Draw', items: ['Line', 'Rectangle', 'Circle', 'Arc', 'Point'], gap: sp.s10 },
        { items: ['Dimension'] },
        { key: 'constrain', name: 'Constrain', cols: 2, gap: sp.s10, items: ['Coincident', 'Horizontal', 'Vertical', 'Parallel', 'Perpendicular', 'Tangent', 'Equal', 'Midpoint', 'Concentric', 'Symmetric', 'Fix', 'Normal', 'Point on'] },
        { items: ['Construct'], gap: sp.s10 },
        { items: ['Delete'] },
      ]
    : [
        { items: ['Sketch'] },
        { key: 'create', name: 'Create', items: ['Extrude', 'Revolve'], gap: sp.s10 },
        { key: 'modify', name: 'Modify', items: ['Fillet', 'Chamfer', 'Shell', 'Hole'] },
        { items: ['Plane'], gap: sp.s10 },
        { key: 'copy', name: 'Copy', items: ['Mirror', 'Pattern'] },
      ]
  const open = mode === 'flyout' ? 'modify' : mode === 'constrain' ? 'constrain' : ''
  const aim = mode === 'flyout' ? 'Chamfer' : mode === 'constrain' ? 'Midpoint' : ''
  const pins: Record<string, true> = { Fillet: true, Line: true }

  const maxPins = maxPinned[sketching ? 'sketching' : 'browsing'][key]
  let pinnedCount = 0
  for (const g of defs) if (g.items.length > 1) for (const l of g.items) if (pins[l]) pinnedCount += 1
  const full = pinnedCount >= maxPins

  const toolButton = (label: string, gap: number): StripEntry => {
    const on = label === active
    return {
      label, d: D[label] ?? '', gap, multi: false, open: false, cols: 1, items: [], full: false,
      bg: on ? c.accentSoft : 'transparent', bd: on ? c.accentLine : 'transparent', fg: on ? c.accentDark : c.inkSoft,
      op: dimmed[label] ? 0.35 : 1,
    }
  }

  const out: StripEntry[] = []
  const pinned: string[] = []
  for (const g of defs) {
    const first = g.items[0] ?? ''
    if (g.items.length === 1) {
      out.push(toolButton(first, g.gap ?? 0))
      continue
    }
    const isOpen = open === g.key
    out.push({
      label: g.name ?? '', d: D[first] ?? '', gap: g.gap ?? 0, multi: true, open: isOpen, cols: g.cols ?? 1,
      bg: isOpen ? c.accentSoft : 'transparent', bd: isOpen ? c.accentLine : 'transparent', fg: c.inkSoft, op: 1, full,
      items: g.items.map((label) => {
        const dim = !!dimmed[label]
        const isPinned = !!pins[label]
        if (isPinned) pinned.push(label)
        return {
          label, d: D[label] ?? '', op: dim ? 0.4 : 1,
          bg: aim === label && !dim ? c.accentSoft : 'transparent',
          pinned: isPinned,
          pinName: (isPinned ? 'Unpin ' : 'Pin ') + label,
          pinBg: isPinned ? c.accentPinned : 'transparent',
          pinFg: isPinned ? c.accentDark : c.muted,
          pinFill: isPinned ? c.accentDark : 'none',
          pinOp: full && !isPinned ? 0.35 : 1,
        }
      }),
    })
  }
  pinned.forEach((label, i) => out.push(toolButton(label, i === 0 ? sp.s18 : 0)))
  return out
}

const sketchControls = (): string => `<div style="flex: 1"></div>
      <div style="display: flex; flex: none; border: 1px solid ${c.line}; border-radius: ${px(r.sm)}; overflow: hidden; background: ${c.white}">
        <button aria-pressed="true" style="width: ${px(z.navToggleWidth)}; height: ${px(z.touchMin)}; border: none; background: ${c.accent}; color: ${c.white}; font: inherit; font-weight: 600; cursor: pointer">2D</button>
        <button aria-pressed="false" style="width: ${px(z.navToggleWidth)}; height: ${px(z.touchMin)}; border: none; background: ${c.white}; color: ${c.ink}; font: inherit; font-weight: 600; cursor: pointer">3D</button>
      </div>
      <button style="width: ${px(z.toolButtonNarrow.width)}; height: ${px(z.toolButtonNarrow.height)}; flex: none; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: ${px(sp.s3)}; border: none; border-radius: ${px(r.sm)}; background: transparent; color: ${c.inkSoft}; font: inherit; font-size: ${px(fs.caption)}; font-weight: 500; cursor: pointer">
        ${ico('M3 18l6-8h12l-6 8z M12 13V3 M9 6l3-3 3 3')}
        <span>Normal to</span>
      </button>
      <button style="height: ${px(z.touchMin)}; flex: none; padding: 0 ${px(sp.s16)}; margin-left: ${px(sp.s6)}; border: 1px solid ${c.line}; border-radius: ${px(r.sm)}; background: ${c.white}; color: ${c.ink}; font: inherit; font-weight: 600; cursor: pointer">Cancel</button>
      <button style="height: ${px(z.touchMin)}; flex: none; padding: 0 ${px(sp.s20)}; margin-left: ${px(sp.s4)}; border: 1px solid ${c.accentDark}; border-radius: ${px(r.sm)}; background: ${c.accent}; color: ${c.white}; font: inherit; font-weight: 600; cursor: pointer">Done</button>`

const topBar = (): string => `<div data-region="top-bar" style="flex: none; height: ${px(z.topBar)}; box-sizing: border-box; display: flex; align-items: center; gap: ${px(sp.s8)}; padding: 0 ${px(sp.s8)}; background: ${c.white}; border-bottom: 1px solid ${c.line}">
    ${iconButton('Back to Documents', ico('M15 5l-7 7 7 7'))}
    <div style="font-weight: 600; font-size: ${px(fs.title)}; white-space: nowrap; margin-right: ${px(sp.s8)}">Gearbox bracket</div>
    <div style="display: flex; align-items: center; gap: ${px(sp.s4)}; min-width: 0">
      <button style="height: ${px(z.touchMin)}; padding: 0 ${px(sp.s14)}; border: 1px solid ${c.accentLine}; border-radius: ${px(r.sm)}; background: ${c.accentSoft}; color: ${c.accentDark}; font: inherit; font-weight: 600; white-space: nowrap; cursor: pointer">Part Studio 1</button>
      <button style="height: ${px(z.touchMin)}; padding: 0 ${px(sp.s14)}; border: 1px solid transparent; border-radius: ${px(r.sm)}; background: transparent; color: ${c.inkSoft}; font: inherit; font-weight: 500; white-space: nowrap; cursor: pointer">Assembly 1</button>
      ${iconButton('Add tab', ico('M12 5v14 M5 12h14', { join: false }))}
    </div>
    <div style="flex: 1"></div>
    ${iconButton('Undo', ico('M8 5L3 10l5 5 M3 10h11a5 5 0 0 1 0 10h-3'))}
    ${iconButton('Redo', ico('M16 5l5 5-5 5 M21 10H10a5 5 0 0 0 0 10h3'), { extra: 'opacity: 0.35; ', attrs: ' disabled', cursor: 'default' })}
    ${iconButton(
      'Save, unsaved changes',
      `${ico('M5 4h11l3 3v13H5z M8 4v5h7V4 M8 20v-6h8v6')}<span style="position: absolute; top: 7px; right: 7px; width: ${px(z.statusDot)}; height: ${px(z.statusDot)}; border-radius: 50%; background: ${c.accent}; border: 2px solid ${c.white}"></span>`,
      { extra: 'position: relative; ' },
    )}
    <div style="display: flex; align-items: center; gap: ${px(sp.s6)}; height: ${px(z.touchMin)}; padding: 0 ${px(sp.s8)}; white-space: nowrap; font-size: ${px(fs.tab)}; color: ${c.inkSoft}">
      <span style="width: ${px(z.statusDot)}; height: ${px(z.statusDot)}; border-radius: 50%; background: ${c.ok}"></span>
      <span>Connected</span>
    </div>
  </div>`

const featureRows = (sketching: boolean) =>
  [
    { label: 'Origin', d: ICON_FOLDER, sel: false },
    { label: 'Sketch 1', d: D.Sketch ?? '', sel: false },
    { label: 'Extrude 1', d: D.Extrude ?? '', sel: true },
    { label: 'Fillet 1', d: D.Fillet ?? '', sel: false },
  ].map((f) => ({ label: f.label, d: f.d, bg: f.sel && !sketching ? c.selected : 'transparent' }))

const holeD = D.Hole ?? ''

const featurePanel = (sketching: boolean): string => {
  const rows = featureRows(sketching)
    .map(
      (f) => `<button style="flex: none; height: ${px(z.featureRow)}; display: flex; align-items: center; gap: ${px(sp.s10)}; padding: 0 ${px(sp.s14)}; border: none; background: ${f.bg}; color: ${c.ink}; font: inherit; text-align: left; cursor: pointer">
            ${ico(f.d, { size: z.iconSmall, stroke: c.inkSoft, extra: 'flex: none' })}
            <span>${f.label}</span>
          </button>`,
    )
    .join('\n          ')
  return `<div style="flex: none; height: ${px(z.panelHeader)}; display: flex; align-items: center; padding: 0 ${px(sp.s4)} 0 ${px(sp.s14)}; border-bottom: 1px solid ${c.lineSoft}">
          <div style="flex: 1; font-weight: 600">Features</div>
          ${iconButton('Collapse feature panel', ico('M4 5h16v14H4z M9 5v14 M16 9l-3 3 3 3'))}
        </div>
        <div style="flex: 1; min-height: 0; overflow: hidden; display: flex; flex-direction: column">
          ${rows}
          <button style="flex: none; display: flex; align-items: flex-start; gap: ${px(sp.s10)}; padding: ${px(sp.s10)} ${px(sp.s14)}; border: none; background: ${c.dangerSoft}; color: ${c.ink}; font: inherit; text-align: left; cursor: pointer">
            ${ico(holeD, { size: z.iconSmall, stroke: c.danger, extra: 'flex: none' })}
            <span style="display: flex; flex-direction: column; gap: ${px(sp.s2)}">
              <span style="font-weight: 600; color: ${c.danger}">Hole 1 failed</span>
              <span style="font-size: ${px(fs.small)}; color: ${c.inkSoft}">The point is not on the chosen face.</span>
            </span>
          </button>
          <div style="flex: none; height: ${px(z.featureRow)}; display: flex; align-items: center; gap: ${px(sp.s8)}; padding-left: ${px(sp.s14)}">
            <div style="flex: 1; height: 3px; border-radius: 2px; background: ${c.accent}"></div>
            ${iconButton('Rollback bar, drag to move', ico('M5 9h14 M5 15h14 M12 3v3 M12 18v3', { sw: 2, join: false }), { color: c.accent, cursor: 'grab' })}
          </div>
          <div style="flex: none; padding: ${px(sp.s12)} ${px(sp.s14)} ${px(sp.s4)}; font-size: ${px(fs.small)}; font-weight: 600; color: ${c.muted}">Parts (1)</div>
          <button style="flex: none; height: ${px(z.featureRow)}; display: flex; align-items: center; gap: ${px(sp.s10)}; padding: 0 ${px(sp.s14)}; border: none; background: transparent; color: ${c.ink}; font: inherit; text-align: left; cursor: pointer">
            ${ico('M12 3l8 4.5v9L12 21l-8-4.5v-9z M12 12l8-4.5 M12 12v9 M12 12L4 7.5', { size: z.iconSmall, stroke: c.inkSoft, cap: false, extra: 'flex: none' })}
            <span>Part 1</span>
          </button>
        </div>`
}

const rail = (): string => {
  const rows = featureRows(false)
    .map((f) => iconButton(f.label, ico(f.d, { size: z.iconSmall }), { bg: f.bg }))
    .join('\n          ')
  return `<div style="display: flex; flex-direction: column; align-items: center; gap: ${px(sp.s2)}; padding-top: ${px(sp.s2)}">
          ${iconButton('Expand feature panel', ico('M4 5h16v14H4z M9 5v14 M13 9l3 3-3 3'))}
          <div style="width: 32px; height: 1px; background: ${c.lineSoft}; margin: ${px(sp.s2)} 0"></div>
          ${rows}
          ${iconButton('Hole 1 failed', ico(holeD, { size: z.iconSmall }), { bg: c.dangerSoft, color: c.danger })}
        </div>`
}

const padKeys = (): string => {
  const keys = ['7', '8', '9', '÷', 'Del', '4', '5', '6', '×', '(', '1', '2', '3', '−', ')', '0', '.', '±', '+', '#', 'mm', 'deg', 'in', 'Cancel', 'Enter']
  const names: Record<string, string> = { '÷': 'Divide', '×': 'Multiply', '−': 'Minus', '+': 'Plus', '±': 'Change sign', '#': 'Variables', Del: 'Backspace', '.': 'Decimal point', '(': 'Open bracket', ')': 'Close bracket' }
  return keys
    .map((k) => {
      const digit = /^[0-9.]$/.test(k)
      const enter = k === 'Enter'
      const word = k.length > 1
      const fsz = word ? (k.length > 3 ? fs.small : fs.body) : fs.padKey
      const bg = enter ? c.accent : digit ? c.white : c.key
      const bd = enter ? c.accentDark : c.line
      return `<button aria-label="${names[k] ?? k}" style="height: ${px(z.padKeyHeight)}; min-width: ${px(z.touchMin)}; padding: 0; border: 1px solid ${bd}; border-radius: ${px(r.sm)}; background: ${bg}; color: ${enter ? c.white : c.ink}; font: inherit; font-size: ${px(fsz)}; font-weight: 500; cursor: pointer">${k}</button>`
    })
    .join('\n            ')
}

const segment = (labels: string[], on: number): string =>
  `<div style="display: flex; border: 1px solid ${c.line}; border-radius: ${px(r.sm)}; overflow: hidden">
                ${labels
                  .map((l, i) => `<button style="flex: 1; height: ${px(z.touchMin)}; border: none;${i ? ` border-left: 1px solid ${c.line};` : ''} background: ${i === on ? c.accent : c.white}; color: ${i === on ? c.white : c.ink}; font: inherit;${i === on ? ' font-weight: 600;' : ''} cursor: pointer">${l}</button>`)
                  .join('\n                ')}
              </div>`

const label = (text: string, color: string = c.muted): string => `<div style="font-size: ${px(fs.small)}; font-weight: 600; color: ${color}">${text}</div>`
const field = (inner: string): string => `<div style="display: flex; flex-direction: column; gap: ${px(sp.s4)}">${inner}</div>`

const dialog = (): string => `<div data-region="dialog" style="position: absolute; inset: 0; display: flex; flex-direction: column; background: ${c.white}">
          <div style="flex: none; height: ${px(z.dialogHeader)}; box-sizing: border-box; display: flex; align-items: center; gap: ${px(sp.s4)}; padding: 0 ${px(sp.s6)} 0 ${px(sp.s14)}; background: ${c.accentSoft}; border-bottom: 1px solid ${c.accentLine}">
            <div style="flex: 1; font-weight: 600; font-size: ${px(fs.title)}">Extrude 2</div>
            <button aria-label="Cancel" style="width: ${px(z.touchMin)}; height: ${px(z.touchMin)}; display: flex; align-items: center; justify-content: center; border: 1px solid ${c.line}; border-radius: ${px(r.sm)}; background: ${c.white}; color: ${c.ink}; cursor: pointer">${ico('M6 6l12 12 M18 6L6 18', { sw: 2, join: false })}</button>
            <button aria-label="Accept" style="width: ${px(z.touchMin)}; height: ${px(z.touchMin)}; display: flex; align-items: center; justify-content: center; border: 1px solid ${c.accentDark}; border-radius: ${px(r.sm)}; background: ${c.accent}; color: ${c.white}; cursor: pointer">${ico('M5 12l5 5 9-10', { sw: 2.2 })}</button>
          </div>
          <div style="flex: 1; min-height: 0; overflow: hidden; display: flex; flex-direction: column; gap: ${px(sp.s10)}; padding: ${px(sp.s12)}">
            ${field(`${label('Profile')}
              <button style="height: ${px(z.touchMin)}; display: flex; align-items: center; justify-content: space-between; padding: 0 ${px(sp.s12)}; border: 1px solid ${c.line}; border-radius: ${px(r.sm)}; background: ${c.white}; color: ${c.ink}; font: inherit; text-align: left; cursor: pointer">
                <span>Region of Sketch 1</span>
                <span style="font-size: ${px(fs.small)}; color: ${c.muted}">1 item</span>
              </button>`)}
            ${field(`${label('End')}
              ${segment(['Blind', 'Through', 'Up to'], 0)}`)}
            ${field(`${label('Depth', c.accentDark)}
              <div style="display: flex; gap: ${px(sp.s6)}">
                <button aria-label="Depth, editing on the pad" style="flex: 1; height: ${px(z.touchMin)}; display: flex; align-items: center; padding: 0 ${px(sp.s12)}; border: 2px solid ${c.accent}; border-radius: ${px(r.sm)}; background: ${c.accentField}; color: ${c.ink}; font: inherit; font-size: ${px(fs.field)}; font-weight: 500; text-align: left; cursor: pointer">
                  <span>25 mm</span>
                  <span style="width: 2px; height: 22px; margin-left: 2px; background: ${c.accent}"></span>
                </button>
                <button aria-label="Flip direction" style="width: ${px(z.touchMin)}; height: ${px(z.touchMin)}; flex: none; display: flex; align-items: center; justify-content: center; border: 1px solid ${c.line}; border-radius: ${px(r.sm)}; background: ${c.white}; color: ${c.inkSoft}; cursor: pointer">${ico('M8 20V5 M5 8l3-3 3 3 M16 4v15 M13 16l3 3 3-3')}</button>
              </div>`)}
            ${field(`${label('Result')}
              ${segment(['New', 'Add', 'Remove'], 1)}`)}
          </div>
          <!-- Numeric and expression pad, docked at the foot of the dialog column -->
          <div data-region="pad" style="flex: none; display: grid; grid-template-columns: repeat(${z.padColumns}, minmax(0, 1fr)); gap: ${px(sp.s6)}; padding: ${px(sp.s12)}; background: ${c.surface}; border-top: 1px solid ${c.line}">
            ${padKeys()}
          </div>
        </div>`

const S: string = c.inkSoft
const poly = (points: string, fill: string, stroke = S, extra = ''): string =>
  `<polygon points="${points}" fill="${fill}" stroke="${stroke}" style="${extra}stroke-width: 1.5; stroke-linejoin: round"></polygon>`
const svgOpen = (label: string): string => `<svg viewBox="0 0 744 600" width="100%" height="100%" style="display: block" role="img" aria-label="${label}">`
const FONT = `font-family: ${fontFamily}`

// Scene drawings, copied from the mock as they are: they stand in for the model, not for the chrome.
const sceneModel = (): string => `${svgOpen('Part in the viewport with one face selected')}
          ${poly('372,170 560,260 372,350 184,260', '#dfe5ec')}
          ${poly('184,260 372,350 372,470 184,380', '#c3ccd7')}
          ${poly('372,350 560,260 560,380 372,470', c.selection)}
          <ellipse cx="372" cy="260" rx="52" ry="25" fill="#8e99a6" stroke="${S}" style="stroke-width: 1.5"></ellipse>
        </svg>`

const sceneDialog = (): string => `${svgOpen('Extrude preview with a Drag handle on its depth arrow')}
          ${poly('372,230 560,320 372,410 184,320', '#dfe5ec')}
          ${poly('184,320 372,410 372,530 184,440', '#c3ccd7')}
          ${poly('372,410 560,320 560,440 372,530', '#aab5c2')}
          ${poly('278,320 372,365 372,275 278,230', c.accent, c.accent, 'fill-opacity: 0.22; ')}
          ${poly('372,365 466,320 466,230 372,275', c.accent, c.accent, 'fill-opacity: 0.32; ')}
          ${poly('372,185 466,230 372,275 278,230', c.accent, c.accent, 'fill-opacity: 0.14; ')}
          <path d="M372 230V118 M362 132l10-14 10 14" fill="none" stroke="${c.accentDark}" style="stroke-width: 3; stroke-linecap: round; stroke-linejoin: round"></path>
          <circle cx="372" cy="92" r="22" fill="${c.white}" stroke="${c.accentDark}" style="stroke-width: 2.5"></circle>
          <path d="M372 80v24 M364 87l8-8 8 8 M364 97l8 8 8-8" fill="none" stroke="${c.accentDark}" style="stroke-width: 2; stroke-linecap: round; stroke-linejoin: round"></path>
          <rect x="404" y="78" width="66" height="28" rx="6" fill="${c.white}" stroke="${c.accentLine}"></rect>
          <text x="437" y="97" fill="${c.accentDark}" style="font-size: 14px; font-weight: 600; text-anchor: middle; ${FONT}">25 mm</text>
        </svg>`

const sceneSketch = (): string => `${svgOpen('Sketch with a selected point and its Drag handle')}
          <path d="M372 40v470 M90 290h564" fill="none" stroke="#b9c1cb" style="stroke-width: 1; stroke-dasharray: 2 6"></path>
          <rect x="222" y="170" width="300" height="240" fill="#669ef2" stroke="none" style="fill-opacity: 0.16"></rect>
          <path d="M222 170H522V410H222Z" fill="none" stroke="${c.ink}" style="stroke-width: 2; stroke-linejoin: round"></path>
          <path d="M222 410L522 170" fill="none" stroke="#73808f" style="stroke-width: 1.5; stroke-dasharray: 8 6"></path>
          <circle cx="372" cy="290" r="58" fill="${c.viewport}" stroke="${c.accent}" style="stroke-width: 2"></circle>
          <circle cx="372" cy="290" r="4" fill="${c.accent}"></circle>
          <path d="M222 446H522 M222 424v32 M522 424v32 M234 440l-12 6 12 6 M510 440l12 6-12 6" fill="none" stroke="#3c424c" style="stroke-width: 1.2; stroke-linecap: round; stroke-linejoin: round"></path>
          <rect x="340" y="432" width="64" height="28" rx="6" fill="${c.white}" stroke="${c.line}"></rect>
          <text x="372" y="451" fill="${c.ink}" style="font-size: 14px; font-weight: 500; text-anchor: middle; ${FONT}">60 mm</text>
          <path d="M186 170V410 M176 170h32 M176 410h32 M180 182l6-12 6 12 M180 398l6 12 6-12" fill="none" stroke="#3c424c" style="stroke-width: 1.2; stroke-linecap: round; stroke-linejoin: round"></path>
          <rect x="154" y="276" width="64" height="28" rx="6" fill="${c.white}" stroke="${c.line}"></rect>
          <text x="186" y="295" fill="${c.ink}" style="font-size: 14px; font-weight: 500; text-anchor: middle; ${FONT}">48 mm</text>
          <rect x="362" y="142" width="20" height="20" rx="4" fill="${c.white}" stroke="${c.ok}"></rect>
          <path d="M366 152h12" fill="none" stroke="${c.okDark}" style="stroke-width: 2; stroke-linecap: round"></path>
          <rect x="530" y="280" width="20" height="20" rx="4" fill="${c.white}" stroke="${c.ok}"></rect>
          <path d="M540 284v12" fill="none" stroke="${c.okDark}" style="stroke-width: 2; stroke-linecap: round"></path>
          <circle cx="522" cy="170" r="9" fill="${c.selection}" stroke="${c.ink}" style="stroke-width: 2"></circle>
          <circle cx="566" cy="126" r="22" fill="${c.white}" stroke="${c.accentDark}" style="stroke-width: 2.5"></circle>
          <path d="M566 113v26 M553 126h26 M562 117l4-4 4 4 M562 135l4 4 4-4 M557 122l-4 4 4 4 M575 122l4 4-4 4" fill="none" stroke="${c.accentDark}" style="stroke-width: 2; stroke-linecap: round; stroke-linejoin: round"></path>
          <path d="M531 161l18-18" fill="none" stroke="${c.accentDark}" style="stroke-width: 1.5; stroke-dasharray: 3 4"></path>
        </svg>`

const quickMenu = (x: number, y: number): string => {
  const labels = ['Line', 'Rectangle', 'Circle', 'Arc', 'Dimension', 'Point', 'Select', 'Construct']
  const half = z.quickMenuDisc / 2
  const hub = z.quickMenuHub / 2
  const wedges = labels
    .map((l, i) => {
      const a = (i * Math.PI) / 4
      const left = Math.round(z.quickMenuRadius * Math.sin(a) - z.quickMenuButton.width / 2)
      const top = Math.round(-z.quickMenuRadius * Math.cos(a) - z.quickMenuButton.height / 2)
      const on = i === 0
      return `<button style="position: absolute; left: ${px(left)}; top: ${px(top)}; width: ${px(z.quickMenuButton.width)}; height: ${px(z.quickMenuButton.height)}; box-sizing: border-box; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: ${px(sp.s3)}; border: 1px solid ${on ? c.accentDark : c.line}; border-radius: ${px(r.quick)}; background: ${on ? c.accent : c.white}; color: ${on ? c.white : c.inkSoft}; font: inherit; font-size: ${px(fs.caption)}; font-weight: 500; cursor: pointer">
            ${ico(D[l] ?? '', { extra: 'pointer-events: none' })}
            <span>${l}</span>
          </button>`
    })
    .join('\n          ')
  return `<div data-region="quick-menu" style="position: absolute; left: ${px(x)}; top: ${px(y)}; width: 0; height: 0; z-index: 4">
          <div style="position: absolute; left: ${px(-half)}; top: ${px(-half)}; width: ${px(z.quickMenuDisc)}; height: ${px(z.quickMenuDisc)}; box-sizing: border-box; border-radius: 50%; background: ${c.quickMenuDisc}; border: 1px solid ${c.line}"></div>
          <div style="position: absolute; left: ${px(-hub)}; top: ${px(-hub)}; width: ${px(z.quickMenuHub)}; height: ${px(z.quickMenuHub)}; box-sizing: border-box; border-radius: 50%; background: ${c.white}; border: 2px solid ${c.accentDark}"></div>
          ${wedges}
        </div>`
}

const frame = (mode: Mode, key: ScreenKey): string => {
  const screen = screens[key]
  const sketching = mode === 'sketch' || mode === 'constrain' || mode === 'radial'
  const showDialog = mode === 'dialog'
  const collapsed = mode === 'collapsed'
  const colW = collapsed ? z.rail : screen.panelWidth
  const multiOn = showDialog
  const chipText = sketching ? '1 point' : showDialog ? '1 region' : '1 face'

  const strip = stripFor(mode, key, sketching).map(stripButton).join('\n    ')
  const vpW = screen.width - colW
  const vpH = screen.height - (z.topBar + z.toolStrip)
  const scene = sketching ? sceneSketch() : showDialog ? sceneDialog() : sceneModel()

  return `<div style="width: ${px(screen.width)}; height: ${px(screen.height)}; box-sizing: border-box; display: flex; flex-direction: column; overflow: hidden; background: ${c.white}; color: ${c.ink}; font-family: ${fontFamily}; font-size: ${px(fs.body)}; line-height: 1.3">
  ${topBar()}
  <div data-region="tool-strip" style="position: relative; z-index: 5; flex: none; height: ${px(z.toolStrip)}; box-sizing: border-box; display: flex; align-items: center; gap: ${px(sp.s2)}; padding: 0 ${px(sp.s8)}; background: ${c.surface}; border-bottom: 1px solid ${c.line}; border-top: 1px solid ${c.line}; margin-top: -1px">
    ${strip}
    ${sketching ? sketchControls() : ''}
  </div>
  <div style="flex: 1; min-height: 0; display: flex">
    <div data-region="left-column" style="position: relative; flex: none; width: ${px(colW)}; box-sizing: border-box; border-right: 1px solid ${c.line}; background: ${c.white}; display: flex; flex-direction: column; min-height: 0">
      ${collapsed ? rail() : featurePanel(sketching)}
      ${showDialog ? dialog() : ''}
    </div>
    <div data-region="viewport" style="position: relative; flex: 1; min-width: 0; background: ${c.viewport}; overflow: hidden">
      ${scene}
      <div data-region="selection-chip" style="position: absolute; top: ${px(z.viewportInset)}; left: ${px(z.viewportInset)}; display: flex; align-items: center; gap: ${px(sp.s8)}">
        <div style="display: flex; align-items: center; gap: ${px(sp.s4)}; height: ${px(z.selectionChipHeight)}; box-sizing: border-box; padding: 0 ${px(sp.s4)} 0 ${px(sp.s14)}; border: 1px solid ${c.line}; border-radius: ${px(r.lg)}; background: ${c.white}">
          <span style="display: flex; align-items: center; gap: ${px(sp.s8)}; margin-right: ${px(sp.s6)}; font-weight: 600; white-space: nowrap">
            <span style="width: 12px; height: 12px; border-radius: 3px; background: ${c.selection}; border: 1px solid ${c.ink}"></span>
            <span>${chipText}</span>
          </span>
          <button aria-pressed="${multiOn}" style="height: ${px(z.touchMin)}; padding: 0 ${px(sp.s14)}; border: 1px solid ${multiOn ? c.accentDark : c.line}; border-radius: ${px(r.sm)}; background: ${multiOn ? c.accent : c.white}; color: ${multiOn ? c.white : c.ink}; font: inherit; font-weight: 600; cursor: pointer">Multi</button>
          <button style="height: ${px(z.touchMin)}; padding: 0 ${px(sp.s12)}; border: none; border-radius: ${px(r.sm)}; background: transparent; color: ${c.accentDark}; font: inherit; font-weight: 600; cursor: pointer">Clear</button>
        </div>
        ${sketching ? `<div style="display: flex; align-items: center; height: ${px(z.selectionChipHeight)}; box-sizing: border-box; padding: 0 ${px(sp.s14)}; border: 1px solid ${c.line}; border-radius: ${px(r.lg)}; background: ${c.white}; white-space: nowrap">
          <span style="font-weight: 600">Sketch 2</span>
          <span style="margin-left: ${px(sp.s8)}; color: ${c.accentDark}">3 items free</span>
        </div>` : ''}
      </div>
      <button data-region="view-cube" aria-label="View cube, showing an isometric view" style="position: absolute; top: ${px(z.viewportInset)}; right: ${px(z.viewportInset)}; width: ${px(z.viewCube)}; height: ${px(z.viewCube)}; display: flex; align-items: center; justify-content: center; border: 1px solid ${c.line}; border-radius: ${px(r.lg)}; background: ${c.white}; color: ${c.inkSoft}; cursor: pointer">
        <svg width="${z.viewCubeGlyph}" height="${z.viewCubeGlyph}" viewBox="0 0 52 52" aria-hidden="true">
          <polygon points="26,5 46,15 26,25 6,15" fill="${c.surface}" stroke="${S}" style="stroke-width: 1.4; stroke-linejoin: round"></polygon>
          <polygon points="6,15 26,25 26,47 6,37" fill="${c.accentSoft}" stroke="${S}" style="stroke-width: 1.4; stroke-linejoin: round"></polygon>
          <polygon points="26,25 46,15 46,37 26,47" fill="${c.accentPinned}" stroke="${S}" style="stroke-width: 1.4; stroke-linejoin: round"></polygon>
        </svg>
      </button>
      ${mode === 'radial' ? quickMenu(Math.round(vpW * 0.6), Math.round(vpH * 0.56)) : ''}
    </div>
  </div>
</div>`
}

export function buildLayoutPage(): string {
  const keys = Object.keys(screens) as ScreenKey[]
  const sheets = keys
    .flatMap((key) =>
      modes.map(
        (mode) => `<section class="sheet" id="${frameId(mode, key)}" data-state="${mode}" data-size="${key}">
<h2 class="cap">${modeTitles[mode]} &middot; ${screens[key].width} x ${screens[key].height}</h2>
${frame(mode, key)}
</section>`,
      ),
    )
    .join('\n')
  const nav = keys
    .map((key) => `<li>${screens[key].width} x ${screens[key].height}: ${modes.map((m) => `<a href="#${frameId(m, key)}">${modeTitles[m]}</a>`).join(' &middot; ')}</li>`)
    .join('\n')
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FreeShape tablet layout</title>
<!-- Generated by \`npm run design:build\` from src/theme/tokens.ts. Do not edit by hand. -->
<style>
body { margin: 0; padding: 24px; background: #d9dde2; font-family: ${fontFamily}; color: ${c.ink}; }
h1 { margin: 0 0 8px; font-size: 20px; }
ul { margin: 0 0 24px; padding-left: 20px; line-height: 1.8; font-size: 14px; }
.sheet { margin: 0 0 32px; width: max-content; }
.cap { margin: 0 0 8px; font-size: 14px; font-weight: 600; }
.sheet > div { box-shadow: 0 2px 12px rgba(0, 0, 0, 0.25); }
/* Open one state alone, at its real size and with no page chrome: layout.html#dialog-1024 */
body:has(.sheet:target) { padding: 0; background: ${c.white}; }
body:has(.sheet:target) > :not(.sheet), body:has(.sheet:target) .sheet:not(:target), body:has(.sheet:target) .cap { display: none; }
body:has(.sheet:target) .sheet { margin: 0; }
body:has(.sheet:target) .sheet > div { box-shadow: none; }
</style>
</head>
<body>
<h1>FreeShape tablet layout</h1>
<ul>
${nav}
</ul>
${sheets}
</body>
</html>
`
}
