import type { Frame, SketchData, SketchStatus, SketchView } from './wire'

/** A 60 x 48 rectangle with a circle in it, a 60 mm dimension, a horizontal and a vertical constraint, and a construction diagonal. */
export function rectAndCircle(): SketchData {
  return {
    points: { origin: [0, 0], p1: [0, 0], p2: [60, 0], p3: [60, 48], p4: [0, 48], c1: [30, 24] },
    entities: {
      e1: { type: 'line', p1: 'p1', p2: 'p2' },
      e2: { type: 'line', p1: 'p2', p2: 'p3' },
      e3: { type: 'line', p1: 'p3', p2: 'p4' },
      e4: { type: 'line', p1: 'p4', p2: 'p1' },
      e5: { type: 'circle', c: 'c1', r: 10 },
      e6: { type: 'line', p1: 'p1', p2: 'p3', construction: true },
    },
    constraints: [
      { id: 'k1', type: 'horizontal', refs: ['e1'] },
      { id: 'k2', type: 'vertical', refs: ['e2'] },
      { id: 'k3', type: 'distance', refs: ['e1'], value: 60 },
    ],
  }
}

export function statusOf(data: SketchData, entity: 'free' | 'defined' | 'conflicting' = 'free', overrides: Record<string, 'free' | 'defined' | 'conflicting'> = {}): SketchStatus {
  return {
    ok: true,
    dof: 3,
    entities: Object.fromEntries(Object.keys(data.entities).map((id) => [id, overrides[id] ?? entity])),
    points: Object.fromEntries(Object.keys(data.points).map((id) => [id, overrides[id] ?? entity])),
    constraints: Object.fromEntries(data.constraints.map((c) => [c.id, 'ok' as const])),
  }
}

/** The Front plane: x to the right, z up, looking from -y. */
export const FRONT: Frame = { origin: [0, 0, 0], xdir: [1, 0, 0], ydir: [0, 0, 1], normal: [0, -1, 0] }
/** A plane that is square to no axis: a face tilted about x and shifted. */
export const TILTED: Frame = { origin: [5, 7, 11], xdir: [1, 0, 0], ydir: [0, Math.SQRT1_2, Math.SQRT1_2], normal: [0, -Math.SQRT1_2, Math.SQRT1_2] }

export function openedMessage(data: SketchData = rectAndCircle(), frame: Frame = FRONT, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: 'sketch.opened',
    document: 'd1',
    tab: 't1',
    feature: null,
    name: 'Sketch 2',
    plane: { kind: 'std', name: 'Front' },
    sketch: data,
    status: statusOf(data),
    can_undo: false,
    can_redo: false,
    frame,
    edges: [],
    revision: 3,
    ...extra,
  }
}

export type { SketchView }
