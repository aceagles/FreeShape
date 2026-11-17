import type { Object3D } from 'three'
import type { BoxDrag } from '../input/pointer'
import type { SelectionItem, SelectionStore } from '../selection/store'
import { idOfItem } from '../selection/store'
import { isCrossing, normRect, pointsInRect, projectPoints, trianglesSelected } from '../viewport/boxSelect'
import type { ViewCamera } from '../viewport/camera'
import type { AssemblyOutputs, ElementKind, Part } from '../wire'

/** A whole instance, as the instance list selects it. `part` is the part's hash, as in a pick. */
export const instanceItem = (instance: string, part: string | null): SelectionItem => ({
  id: idOfItem('part', part, null, -1, null, instance),
  kind: 'part',
  part,
  index: -1,
  name: null,
  key: null,
  instance,
})

/**
 * The instances a selection box takes in an Assembly (I9.9): each drawn instance whose part's triangles, where the view
 * draws them now, lie wholly inside the box, or (dragged right to left) touch it. `objects` are the drawn instances (an
 * instance the eye hides is not there); their matrices are where they are drawn, a drag in progress included.
 */
export function assemblyBoxHits(outputs: AssemblyOutputs, objects: ReadonlyMap<string, Pick<Object3D, 'matrix'>>, view: ViewCamera, box: BoxDrag): SelectionItem[] {
  const r = normRect(box)
  const crossing = isCrossing(box)
  const out: SelectionItem[] = []
  for (const inst of outputs.instances) {
    const obj = objects.get(inst.id)
    const mesh = inst.part === null ? undefined : outputs.parts[inst.part]?.mesh
    if (!obj || !mesh) continue
    const S = projectPoints(view, mesh.positions, obj.matrix.elements)
    if (trianglesSelected(S, mesh.indices, 0, mesh.indices.length / 3, pointsInRect(S, r), r, crossing)) out.push(instanceItem(inst.id, inst.part))
  }
  return out
}

/** The instance selected when exactly one instance has anything selected on it, or null. */
export function selectedInstance(items: readonly SelectionItem[]): string | null {
  const ids = new Set(items.map((i) => i.instance ?? ''))
  return ids.size === 1 && !ids.has('') ? [...ids][0]! : null
}

/**
 * Carry the selection over new Assembly outputs: an item stays when its instance is still there and shows the same part,
 * and (for a face, edge or vertex) its Element name is in that part, with its index brought up to date. Items of a Part Studio go.
 */
export function reconcileAssembly(selection: SelectionStore, outputs: AssemblyOutputs): void {
  const byId = new Map(outputs.instances.map((i) => [i.id, i]))
  const indexOf = (part: Part, kind: ElementKind, name: string): number | undefined => {
    for (const [i, n] of part.names[kind]) if (n === name) return i
    return undefined
  }
  const keep: SelectionItem[] = []
  for (const it of selection.getState().items) {
    const inst = it.instance === undefined ? undefined : byId.get(it.instance)
    if (!inst || inst.part !== it.part || inst.part === null) continue
    if (it.kind === 'part') {
      keep.push(it)
      continue
    }
    const part = outputs.parts[inst.part]
    if (!part || it.kind === 'plane' || it.kind === 'axis' || it.kind === 'region' || it.kind === 'connector') continue
    if (it.name !== null) {
      const index = indexOf(part, it.kind, it.name)
      if (index !== undefined) keep.push(index === it.index ? it : { ...it, index })
    } else keep.push(it)
  }
  const items = selection.getState().items
  if (keep.length === items.length && keep.every((k, i) => k === items[i])) selection.aim(null)
  else selection.set(keep)
}
