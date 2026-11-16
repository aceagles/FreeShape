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
