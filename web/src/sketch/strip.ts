import type { DesktopEntry, DesktopLayout, StripDef, ToolDef } from '../tools/defs'
import type { ToolbarCommand } from '../tools/DesktopToolbar'
import { space } from '../theme/tokens'
import { CONSTRAIN_TOOLS as CONSTRAIN } from './constraints'
import type { ToolState } from '../tools/useToolTypes'

const t = (id: string, label: string): ToolDef => ({ id, label, icon: id })

/**
 * The sketch strip, in the layout's order (plans/browser-client/layout-mock/Main.dc.html, sketch and constrain states):
 * Select, a Draw group (the drawing tools, with the Rectangle in both forms, then Trim, Corner fillet and Use edge, which change what is drawn, then Mirror and the two patterns, which copy it; a Copy group of their own between Constrain and Construct made the strip 58 px too wide for 1024, decision d-s14-strip), Dimension, a Constrain group, Construct and Delete, then the user's pins. Each tool's icon is its
 * stroke path in tools/inlinePaths.ts. The Constrain list is two columns because it has more than seven rows. It has the
 * twelve tools the server has: the mock's 13th, Point on, is Coincident on a point and a curve there.
 */
export const SKETCH_STRIP: readonly StripDef[] = [
  { kind: 'tool', tool: t('select', 'Select') },
  { kind: 'group', key: 'draw', name: 'Draw', gap: space.s10, tools: [t('line', 'Line'), t('rectangle', 'Rectangle'), t('center_rectangle', 'Center rectangle'), t('circle', 'Circle'), t('arc', 'Arc'), t('point', 'Point'), t('trim', 'Trim'), t('corner_fillet', 'Corner fillet'), t('use_edge', 'Use edge'), { ...t('sk_mirror', 'Mirror'), icon: 'mirror' }, { ...t('sk_lpattern', 'Linear pattern'), icon: 'linear_pattern' }, { ...t('sk_cpattern', 'Circular pattern'), icon: 'circular_pattern' }] },
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
const WIRED: ReadonlySet<string> = new Set(['select', 'dimension', 'line', 'rectangle', 'center_rectangle', 'circle', 'arc', 'point', 'construct', 'delete', 'trim', 'corner_fillet', 'use_edge', 'sk_mirror', 'sk_lpattern', 'sk_cpattern'])

/** The strip button that is lit for a tool: the tool's own (the Center rectangle has its own row, as on the Desktop toolbar). */
export const stripActive = (tool: string): string => tool

export const sketchToolState = (id: string): ToolState => (WIRED.has(id) ? 'ok' : 'dimmed')

/** What a tap on a wired sketch tool does. Select is a mode, not an action, and stays on. */
export function runSketchTool(store: { deleteSelected(): void; toggleConstruction(): void }, id: string): void {
  if (id === 'delete') store.deleteSelected()
  else if (id === 'construct') store.toggleConstruction()
}

// ---------------------------------------------------------------------------------------------------------------------
// The Desktop-mode sketch toolbar (card I9.4): freeshape/ui/mainwindow.py `_build_sketch_toolbar`, drawn by DesktopToolbar.
// ---------------------------------------------------------------------------------------------------------------------

const SEP: DesktopEntry = { kind: 'separator' }
/** A button of the sketch row. All are commands: the shell supplies the ones the client has, the rest are disabled. `icon` names the picture when it differs from the id. */
const b = (id: string, label: string, key?: string, icon: string = id, tip?: string): DesktopEntry => ({ kind: 'command', tool: { id, label, icon }, key, tip })

/**
 * The desktop's sketch toolbar, in its order and with its separators; the tip is the desktop's tooltip text before its
 * dash and the key is the desktop's sketch key (which card I9.7 makes work). Left out: the "Both sides" toggle, which the
 * desktop shows only while Offset is on. Ids are the tablet strip's where the tool exists there; the desktop's Sketch
 * fillet is the strip's Corner fillet, its Use is the strip's Use edge, and its Construction is the strip's Construct.
 */
export const DESKTOP_SKETCH_TOOLBAR: DesktopLayout = {
  items: [
    b('undo', 'Undo', 'Ctrl+Z'),
    b('redo', 'Redo', 'Ctrl+Y'),
    SEP,
    b('select', 'Select', 'S'),
    b('line', 'Line', 'L'),
    b('rectangle', 'Corner rectangle', 'G'),
    b('center_rectangle', 'Center rectangle', 'R'),
    b('circle', 'Circle', 'C'),
    b('polygon', 'Polygon'),
    b('arc', '3-point arc', 'A', 'arc3'),
    b('arc_center', 'Center arc'),
    b('spline', 'Spline'),
    b('ellipse', 'Ellipse'),
    b('text', 'Text'),
    b('point', 'Point', 'P'),
    SEP,
    b('construct', 'Construction', 'Q', 'construction'),
    b('dimension', 'Dimension', 'D'),
    SEP,
    b('coincident', 'Coincident', 'I'),
    b('horizontal', 'Horizontal', 'H'),
    b('vertical', 'Vertical', 'V'),
    b('parallel', 'Parallel'),
    b('perpendicular', 'Perpendicular'),
    b('tangent', 'Tangent', 'T'),
    b('equal', 'Equal', 'E'),
    b('midpoint', 'Midpoint'),
    b('concentric', 'Concentric'),
    b('symmetric', 'Symmetric'),
    b('fix', 'Fix'),
    b('normal', 'Normal'),
    SEP,
    b('corner_fillet', 'Sketch fillet'),
    b('offset', 'Offset', 'O'),
    b('slot', 'Slot'),
    b('sk_mirror', 'Mirror', undefined, 'mirror'),
    b('sk_lpattern', 'Linear pattern', undefined, 'linear_pattern'),
    b('sk_cpattern', 'Circular pattern', undefined, 'circular_pattern'),
    SEP,
    b('use_edge', 'Use', 'U'),
    b('trim', 'Trim'),
    b('extend', 'Extend'),
    b('insert_file', 'Insert DXF/SVG', undefined, 'import'),
    b('delete', 'Delete', 'Del'),
  ],
  trailing: [
    b('normal_to', 'Normal to', 'N'),
    b('fit', 'Zoom to fit', 'F'),
    { kind: 'command', tool: { id: 'finish', label: 'Finish sketch', icon: 'finish' }, text: '\u2713  Finish sketch', accent: true },
  ],
}

/** What the shell knows of the sketch, for the commands of the sketch row. */
export interface SketchRowContext {
  /** The tool that is on: a strip id, 'center_rectangle', or the Constrain tool that is collecting picks. */
  active: string
  /** The shape in progress has points placed (the store's `drawing`). */
  drawing: boolean
  canUndo: boolean
  canRedo: boolean
  /** The Construct button is pressed (store.constructionPressed). */
  constructing: boolean
  /** A drawing tool, Select or Dimension chosen; a Constrain tool starts as the tablet's does. */
  onTool: (id: string) => void
  onUndo: () => void
  onRedo: () => void
  onDelete: () => void
  onConstruct: () => void
  onNormalTo: () => void
  onFit: () => void
  onFinish: () => void
}

/** The tools of the sketch row the client has: the ones the server and the sketch store do (sketch/store.ts, constraints.ts). */
const CHOOSABLE: readonly string[] = ['select', 'line', 'rectangle', 'center_rectangle', 'circle', 'arc', 'point', 'dimension', 'corner_fillet', 'use_edge', 'trim', 'sk_mirror', 'sk_lpattern', 'sk_cpattern', ...CONSTRAIN]

/** The commands DesktopToolbar needs to draw the sketch row: every tool the client has, the active one pressed. */
export function sketchRowCommands(c: SketchRowContext): Record<string, ToolbarCommand> {
  const out: Record<string, ToolbarCommand> = {}
  for (const id of CHOOSABLE) {
    // the tool that is on, chosen again, goes back to Select (store.chooseTool)
    out[id] = { run: () => c.onTool(id), pressed: c.active === id }
  }
  out.undo = { run: c.onUndo, enabled: c.canUndo }
  out.redo = { run: c.onRedo, enabled: c.canRedo }
  out.delete = { run: c.onDelete }
  out.construct = { run: c.onConstruct, pressed: c.constructing }
  out.normal_to = { run: c.onNormalTo }
  out.fit = { run: c.onFit }
  out.finish = { run: c.onFinish }
  return out
}
