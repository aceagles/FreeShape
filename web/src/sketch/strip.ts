import type { StripDef, ToolDef } from '../tools/defs'
import { space } from '../theme/tokens'
import type { ToolState } from '../tools/useToolTypes'

const t = (id: string, label: string): ToolDef => ({ id, label, icon: id })

/**
 * The sketch strip, in the layout's order (plans/browser-client/layout-mock/Main.dc.html, sketch and constrain states):
 * Select, a Draw group (the drawing tools, then Trim, Corner fillet and Use edge, which change what is drawn), Dimension, a Constrain group, Construct and Delete, then the user's pins. Each tool's icon is its
 * stroke path in tools/inlinePaths.ts. The Constrain list is two columns because it has more than seven rows. It has the
 * twelve tools the server has: the mock's 13th, Point on, is Coincident on a point and a curve there.
 */
export const SKETCH_STRIP: readonly StripDef[] = [
  { kind: 'tool', tool: t('select', 'Select') },
  { kind: 'group', key: 'draw', name: 'Draw', gap: space.s10, tools: [t('line', 'Line'), t('rectangle', 'Rectangle'), t('circle', 'Circle'), t('arc', 'Arc'), t('point', 'Point'), t('trim', 'Trim'), t('corner_fillet', 'Corner fillet'), t('use_edge', 'Use edge')] },
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
    ],
  },
  { kind: 'tool', tool: t('construct', 'Construct'), gap: space.s10 },
  { kind: 'tool', tool: t('delete', 'Delete') },
]

/** The tools whose cards are done: Select picks and selects in the view (I7.7); the drawing tools place points (I7.8); Dimension is I7.11; Construct and Delete act on the selection (I7.9). Every other button waits for its card and stays dimmed. */
const WIRED: ReadonlySet<string> = new Set(['select', 'dimension', 'line', 'rectangle', 'circle', 'arc', 'point', 'construct', 'delete', 'trim', 'corner_fillet', 'use_edge'])

/** The strip button that is lit for a tool: the Rectangle stays lit in its centre form. */
export const stripActive = (tool: string): string => (tool === 'center_rectangle' ? 'rectangle' : tool)

export const sketchToolState = (id: string): ToolState => (WIRED.has(id) ? 'ok' : 'dimmed')

/** What a tap on a wired sketch tool does. Select is a mode, not an action, and stays on. */
export function runSketchTool(store: { deleteSelected(): void; toggleConstruction(): void }, id: string): void {
  if (id === 'delete') store.deleteSelected()
  else if (id === 'construct') store.toggleConstruction()
}
