import type { PickKind } from '../viewport/pick'

/**
 * The Select menu's filter (card I9.14): what a click or a selection box may pick. The desktop's
 * `MainWindow.sel_actions` and `SELECT_DEFAULTS` (freeshape/ui/mainwindow.py): seven check items, each standing for the
 * kinds the desktop names. The client has no `sketch_line` pick, so Sketches stands for `region` alone.
 */
export type FilterKey = 'vertex' | 'edge' | 'face' | 'part' | 'sketch' | 'plane' | 'axis'

export type SelectFilter = Readonly<Record<FilterKey, boolean>>

/** The menu's order, with the kinds each item stands for. */
export const FILTER_KINDS: Readonly<Record<FilterKey, readonly PickKind[]>> = {
  vertex: ['vertex'],
  edge: ['edge'],
  face: ['face'],
  part: ['part'],
  sketch: ['region'],
  plane: ['plane'],
  axis: ['axis'],
}

export const FILTER_KEYS = Object.keys(FILTER_KINDS) as FilterKey[]

/** `SELECT_DEFAULTS`: vertices and whole parts are off. */
export const DEFAULT_FILTER: SelectFilter = { vertex: false, edge: true, face: true, part: false, sketch: true, plane: true, axis: true }

export const isDefaultFilter = (f: SelectFilter): boolean => FILTER_KEYS.every((k) => f[k] === DEFAULT_FILTER[k])

/**
 * The kinds a filter lets through. A canvas is always one of them (card I12.5): the desktop's Select menu has no item for it,
 * because the desktop picks a Selectable canvas as its feature whatever the menu says (a feature, not a piece of geometry),
 * and `Selectable` on the canvas is what turns that off.
 */
export const filterKinds = (f: SelectFilter): PickKind[] => [...FILTER_KEYS.filter((k) => f[k]).flatMap((k) => FILTER_KINDS[k]), 'canvas']
