/**
 * Stroke paths (24 x 24 box) for tools that have no exported desktop icon: the sketch tools, as the approved layout draws
 * them (plans/browser-client/layout-mock/Main.dc.html, the `D` map). The constraint paths also draw the glyphs beside
 * sketch items. `ToolIcon` uses a path when the icons folder has no file of that name.
 */
export const inlinePaths: Readonly<Record<string, string>> = {
  select: 'M6 3l12 9-6 1-3 6z',
  line: 'M5 19L19 5',
  rectangle: 'M4 6h16v12H4z',
  circle: 'M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0',
  arc: 'M4 18A14 14 0 0 1 18 4',
  point: 'M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0',
  dimension: 'M4 6v12 M20 6v12 M4 12h16 M7 9l-3 3 3 3 M17 9l3 3-3 3',
  construct: 'M4 20l3-3 M10 14l4-4 M17 7l3-3',
  delete: 'M5 7h14 M9 7V4h6v3 M7 7l1 13h8l1-13',
  coincident: 'M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0 M4 20l5-5 M20 4l-5 5',
  horizontal: 'M4 12h16',
  vertical: 'M12 4v16',
  parallel: 'M6 20l6-16 M12 20l6-16',
  perpendicular: 'M4 19h16 M12 19V5',
  tangent: 'M4 5h16 M12 13m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0',
  equal: 'M5 9h14 M5 15h14',
  midpoint: 'M4 18h16 M12 18l-4-7h8z',
  concentric: 'M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0 M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0',
  symmetric: 'M12 3v18 M5 8l3 4-3 4 M19 8l-3 4 3 4',
  fix: 'M12 4v12 M6 16h12 M8 20l2-4 M12 20l2-4 M16 20l2-4',
  normal: 'M4 19c4-10 12-10 16 0 M12 11V3',
  point_on: 'M4 18L20 6 M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0',
}
