import { paths } from '../shell/icons'

/**
 * THE one table from a feature type to its icon (a 24 px stroke path, 1.8 stroke: the layout's style). Card I6.2
 * replaces these paths with the desktop's own icons; nothing else in the client names an icon by feature type.
 *
 * The layout has paths for ten tools. Every other type (axis, boolean, import, variable, derived, mate_connector,
 * context, sweep, rib, loft, draft, split, move, thread, the sm_* sheet-metal types, delete_face, move_face,
 * offset_surface, thicken, enclose, fill, delete_part, variable_studio_ref and every `plugin:<id>`) shares the
 * generic diamond until I6.2.
 */
const ICONS: Readonly<Record<string, string>> = {
  sketch: paths.sketch,
  extrude: paths.extrude,
  revolve: paths.revolve,
  fillet: paths.fillet,
  chamfer: paths.chamfer,
  shell: paths.shell,
  hole: paths.hole,
  plane: paths.plane,
  mirror: paths.mirror,
  linear_pattern: paths.pattern,
  circular_pattern: paths.pattern,
}

export const GENERIC_ICON = paths.generic

/** The icon path of a feature type: its own, or the generic one. */
export function featureIcon(type: string): string {
  return Object.hasOwn(ICONS, type) ? (ICONS[type] as string) : GENERIC_ICON
}

/** Whether the type has an icon of its own (false: it draws the generic one). */
export function hasOwnIcon(type: string): boolean {
  return Object.hasOwn(ICONS, type)
}
