import { maxPinned, size, space } from '../theme/tokens'

/** One tool of a strip: the feature type it starts (the id the types route uses), its label, and the name of its icon file in src/icons. */
export interface ToolDef {
  id: string
  label: string
  icon: string
}

/** A single tool, or a group: a button that opens a list of tools. `gap` is the extra space before it (the layout's 10 px between clusters). */
export type StripDef =
  | { kind: 'tool'; tool: ToolDef; gap?: number }
  | { kind: 'group'; key: string; name: string; tools: readonly ToolDef[]; gap?: number }

const t = (id: string, label: string, icon: string = id): ToolDef => ({ id, label, icon })

/**
 * The feature strip, in the layout's order: Sketch, Create, Modify, Plane, Copy. A tool the layout does not name is in the
 * group nearest its place on the desktop's feature toolbar (freeshape/ui/mainwindow.py `_build_feature_toolbar`), which runs
 *   Sketch | Extrude Revolve Sweep Rib Loft Boundary-surface | Fillet Chamfer Draft Shell Hole Thread Delete-face Move-face
 *   Offset-surface Thicken Enclose Fill Delete-part | the sheet metal tools | Plane Axis | Mirror Linear-pattern
 *   Circular-pattern Boolean Split Move
 * so Create takes what the desktop has up to Fillet, Modify takes everything from Fillet to the sheet metal tools, Axis
 * (next to Plane) goes in Copy after the patterns (Mirror leads, so the group's icon is a copy), with Boolean, Split and Move. The labels are the
 * feature labels the server has, except the two patterns, shortened to fit a strip button (freeshape/features.py FEATURE_LABELS).
 */
export const FEATURE_STRIP: readonly StripDef[] = [
  { kind: 'tool', tool: t('sketch', 'Sketch') },
  {
    kind: 'group',
    key: 'create',
    name: 'Create',
    gap: space.s10,
    tools: [t('extrude', 'Extrude'), t('revolve', 'Revolve'), t('sweep', 'Sweep'), t('rib', 'Rib'), t('loft', 'Loft'), t('boundary_surface', 'Boundary surface')],
  },
  {
    kind: 'group',
    key: 'modify',
    name: 'Modify',
    tools: [
      t('fillet', 'Fillet'),
      t('chamfer', 'Chamfer'),
      t('draft', 'Draft'),
      t('shell', 'Shell'),
      t('hole', 'Hole'),
      t('thread', 'Thread'),
      t('delete_face', 'Delete face'),
      t('move_face', 'Move face'),
      t('offset_surface', 'Offset surface'),
      t('thicken', 'Thicken'),
      t('enclose', 'Enclose'),
      t('fill', 'Fill'),
      t('delete_part', 'Delete part'),
      t('sm_base', 'Sheet metal'),
      t('sm_flange', 'Flange'),
      t('sm_hem', 'Hem'),
      t('sm_jog', 'Jog'),
      t('sm_tab', 'Tab'),
      t('sm_convert', 'Convert'),
    ],
  },
  { kind: 'tool', tool: t('plane', 'Plane'), gap: space.s10 },
  {
    kind: 'group',
    key: 'copy',
    name: 'Copy',
    tools: [t('mirror', 'Mirror'), t('linear_pattern', 'Linear'), t('circular_pattern', 'Circular'), t('axis', 'Axis'), t('boolean', 'Boolean'), t('split', 'Split'), t('move', 'Move')],
  },
]

/** The tools of the groups: the ones that have a pin button. */
export const groupTools = (defs: readonly StripDef[]): ToolDef[] => defs.flatMap((d) => (d.kind === 'group' ? [...d.tools] : []))

export type StripKind = 'feature' | 'sketch'

/** How many pins a strip takes, so it never meets the controls on the right: 6 and 10 for the feature strip, 2 and 6 for the sketch strip. */
export function pinCap(strip: StripKind, windowWidth: number): number {
  const caps = strip === 'feature' ? maxPinned.browsing : maxPinned.sketching
  return windowWidth >= 1366 ? caps['1366'] : caps['1024']
}

/** The width the strip's buttons take with `pinned` pins (the sum the layout's CSS makes: padding, 72 px buttons, 2 px gaps, the cluster margins, 18 px before the first pin). */
export function stripWidth(defs: readonly StripDef[], pinned: number): number {
  const buttons = defs.length + pinned
  const gaps = (buttons - 1) * space.s2
  const margins = defs.reduce((n, d) => n + (d.gap ?? 0), 0) + (pinned > 0 ? space.s18 : 0)
  return 2 * space.s8 + buttons * size.toolButton.width + gaps + margins
}

/** The types of the strip that no pin cap can leave out: every id in the defs, once. */
export const toolIds = (defs: readonly StripDef[]): string[] => defs.flatMap((d) => (d.kind === 'tool' ? [d.tool.id] : d.tools.map((x) => x.id)))

// ---------------------------------------------------------------------------------------------------------------------
// The Desktop-mode feature toolbar (card I9.3): one row of icon-only buttons in the desktop's order. Data, as the strip is.
// ---------------------------------------------------------------------------------------------------------------------

/**
 * One button of the desktop row. `kind: 'feature'` starts the feature type `tool.id` and takes its state from the types
 * route; `kind: 'command'` is any other button (the shell supplies the ones the client can do, the rest are disabled).
 * `tip` replaces the tool's label in the tooltip where the desktop words it differently; `key` is the desktop's shortcut.
 * `text` makes a command a text button (the sketch row's Finish sketch) and `accent` fills it with the accent colour.
 */
export type DesktopEntry =
  | { kind: 'feature' | 'command'; tool: ToolDef; key?: string; tip?: string; text?: string; accent?: boolean }
  | { kind: 'separator' }

/** A desktop toolbar: the buttons from the left, and the ones kept at the right end. */
export interface DesktopLayout {
  items: readonly DesktopEntry[]
  trailing: readonly DesktopEntry[]
}

const strip = new Map<string, ToolDef>([...groupTools(FEATURE_STRIP), ...FEATURE_STRIP.flatMap((d) => (d.kind === 'tool' ? [d.tool] : []))].map((x) => [x.id, x]))
/** A ToolDef the strip already has, so the row and the strip share one. */
const known = (id: string): ToolDef => {
  const found = strip.get(id)
  if (found === undefined) throw new Error(`no strip tool ${id}`)
  return found
}
const SEP: DesktopEntry = { kind: 'separator' }
const feature = (tool: ToolDef, key?: string, tip?: string): DesktopEntry => ({ kind: 'feature', tool, key, tip })
const command = (tool: ToolDef, key?: string, tip?: string): DesktopEntry => ({ kind: 'command', tool, key, tip })
const fromStrip = (id: string, key?: string, tip?: string): DesktopEntry => feature(known(id), key, tip)

/**
 * The desktop's feature toolbar (freeshape/ui/mainwindow.py `_build_feature_toolbar`), in its order and with its
 * separators. The tip is the desktop's tooltip text before its dash. Left out: the Python-feature plugin buttons (one per
 * installed Plugin, made at run time; the Plugins button stands for them) and the hidden configuration box.
 */
export const DESKTOP_TOOLBAR: DesktopLayout = {
  items: [
    command(t('undo', 'Undo'), 'Ctrl+Z'),
    command(t('redo', 'Redo'), 'Ctrl+Y'),
    SEP,
    command(t('variable', 'Variable')),
    feature(t('variable_studio_ref', 'Variable Studio reference')),
    command(t('configs', 'Configurations')),
    feature(t('derived', 'Derived')),
    command(t('history', 'Compare Versions')),
    command(t('plugin', 'Plugins')),
    SEP,
    fromStrip('sketch', 'Shift+S'),
    SEP,
    fromStrip('extrude', 'Shift+E'),
    fromStrip('revolve', 'Shift+W'),
    fromStrip('sweep'),
    fromStrip('rib'),
    fromStrip('loft'),
    fromStrip('boundary_surface'),
    fromStrip('fillet', 'Shift+F'),
    fromStrip('chamfer'),
    fromStrip('draft'),
    fromStrip('shell'),
    fromStrip('hole'),
    fromStrip('thread'),
    fromStrip('delete_face'),
    fromStrip('move_face'),
    fromStrip('offset_surface'),
    fromStrip('thicken'),
    fromStrip('enclose'),
    fromStrip('fill'),
    fromStrip('delete_part'),
    SEP,
    fromStrip('sm_base'),
    fromStrip('sm_flange'),
    fromStrip('sm_hem'),
    fromStrip('sm_jog'),
    fromStrip('sm_tab'),
    fromStrip('sm_convert'),
    command(t('flat', 'Flat pattern')),
    command(t('flat_dxf', 'Export the flat pattern as DXF')),
    command(t('nest', 'Nest for cutting')),
    SEP,
    fromStrip('plane'),
    fromStrip('axis'),
    SEP,
    fromStrip('mirror'),
    fromStrip('linear_pattern', undefined, 'Linear pattern'),
    fromStrip('circular_pattern', undefined, 'Circular pattern'),
    fromStrip('boolean'),
    fromStrip('split'),
    fromStrip('move'),
    feature(t('mate_connector', 'Mate connector')),
    SEP,
    command(t('import', 'Import STEP')),
    command(t('export', 'Export')),
  ],
  trailing: [command(t('section', 'Section view'), 'Shift+X'), command(t('interference', 'Interference detection')), command(t('fit', 'Zoom to fit'), 'F'), command(t('iso', 'Isometric'), 'Shift+7')],
}

/** The tool ids of a desktop row, in order, separators left out. */
export const desktopToolIds = (layout: DesktopLayout): string[] => [...layout.items, ...layout.trailing].flatMap((e) => (e.kind === 'separator' ? [] : [e.tool.id]))

/** What a desktop button's tooltip says: `<label> (<key>)` where the desktop has a key. */
export const desktopTip = (e: Exclude<DesktopEntry, { kind: 'separator' }>): string => `${e.tip ?? e.tool.label}${e.key !== undefined ? ` (${e.key})` : ''}`
