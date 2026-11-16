import type { SelectionItem, SelectionStore } from '../selection/store'
import { idOfItem } from '../selection/store'
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
    if (!part || it.kind === 'plane' || it.kind === 'axis' || it.kind === 'region') continue
    if (it.name !== null) {
      const index = indexOf(part, it.kind, it.name)
      if (index !== undefined) keep.push(index === it.index ? it : { ...it, index })
    } else keep.push(it)
  }
  const items = selection.getState().items
  if (keep.length === items.length && keep.every((k, i) => k === items[i])) selection.aim(null)
  else selection.set(keep)
}
