import type { StripDef, ToolDef } from '../tools/defs'
import { space } from '../theme/tokens'
import type { ToolState } from '../tools/useToolTypes'

const t = (id: string, label: string): ToolDef => ({ id, label, icon: id })

/**
 * The sketch strip, in the layout's order (plans/browser-client/layout-mock/Main.dc.html, sketch and constrain states):
 * Select, a Draw group, Dimension, a Constrain group, Construct and Delete, then the user's pins. Each tool's icon is its
 * stroke path in tools/inlinePaths.ts. The Constrain list is two columns because it has more than seven rows.
 */
export const SKETCH_STRIP: readonly StripDef[] = [
  { kind: 'tool', tool: t('select', 'Select') },
  { kind: 'group', key: 'draw', name: 'Draw', gap: space.s10, tools: [t('line', 'Line'), t('rectangle', 'Rectangle'), t('circle', 'Circle'), t('arc', 'Arc'), t('point', 'Point')] },
  { kind: 'tool', tool: t('dimension', 'Dimension') },
  {
    kind: 'group',
    key: 'constrain',
    name: 'Constrain',
    gap: space.s10,
    tools: [
      t('coincident', 'Coincident'),
      t('horizontal', 'Horizontal'),
      t('vertical', 'Vertical'),
      t('parallel', 'Parallel'),
      t('perpendicular', 'Perpendicular'),
      t('tangent', 'Tangent'),
      t('equal', 'Equal'),
      t('midpoint', 'Midpoint'),
      t('concentric', 'Concentric'),
      t('symmetric', 'Symmetric'),
      t('fix', 'Fix'),
      t('normal', 'Normal'),
      t('point_on', 'Point on'),
    ],
  },
  { kind: 'tool', tool: t('construct', 'Construct'), gap: space.s10 },
  { kind: 'tool', tool: t('delete', 'Delete') },
]

/** The tools whose cards are done: Select picks and selects in the view (I7.7). Every other button waits for its card (I7.8 on) and stays dimmed. */
const WIRED: ReadonlySet<string> = new Set(['select'])

export const sketchToolState = (id: string): ToolState => (WIRED.has(id) ? 'ok' : 'dimmed')
