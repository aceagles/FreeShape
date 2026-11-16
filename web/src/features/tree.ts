import type { FeatureEntry, FolderEntry, StudioOutputs } from '../wire'

/** One drawn row of the feature list, top to bottom. */
export type Row =
  | { kind: 'folder'; folder: FolderEntry; /** feature indices [start, end) of its members */ start: number; end: number; open: boolean }
  | { kind: 'feature'; feature: FeatureEntry; index: number; inFolder: boolean }

/** The folder's members are features[start, end) (the engine keeps them contiguous). */
function folderSpan(features: readonly FeatureEntry[], id: string): { start: number; end: number } {
  const start = features.findIndex((f) => f.folder === id)
  let end = start
  while (end < features.length && features[end]?.folder === id) end++
  return { start, end }
}

/**
 * The rows to draw. A folder is a header above its first member; its members follow when it is open. `open` is the
 * person's own choice per folder (a view setting kept here, never sent); without one the file's `open` is used. A
 * folder the rollback bar lies inside is always open, so the bar has somewhere to sit.
 */
export function buildRows(outputs: Pick<StudioOutputs, 'features' | 'folders' | 'rollback'>, openChoice: Readonly<Record<string, boolean>>): Row[] {
  const { features, folders, rollback } = outputs
  const rows: Row[] = []
  const done = new Set<string>()
  features.forEach((feature, index) => {
    const folder = feature.folder === null ? undefined : folders.find((f) => f.id === feature.folder)
    if (!folder) {
      rows.push({ kind: 'feature', feature, index, inFolder: false })
      return
    }
    const { start, end } = folderSpan(features, folder.id)
    const open = (openChoice[folder.id] ?? folder.open) || (rollback > start && rollback < end)
    if (!done.has(folder.id)) {
      done.add(folder.id)
      rows.push({ kind: 'folder', folder, start, end, open })
    }
    if (open) rows.push({ kind: 'feature', feature, index, inFolder: true })
  })
  return rows
}

/** The feature index a bar placed before this row would roll back to, or null when the bar cannot sit before it. */
export function slotBefore(row: Row, rows: readonly Row[], at: number): number | null {
  if (row.kind === 'folder') return row.start
  // the first member of an open folder shares its slot with the folder's header above it
  const prev = rows[at - 1]
  if (row.inFolder && prev?.kind === 'folder' && prev.start === row.index) return null
  return row.index
}

/** Where the bar is drawn: before the row at this position, or `rows.length` for the end of the list. */
export function barPosition(rows: readonly Row[], rollback: number, total: number): number {
  if (rollback >= total) return rows.length
  const at = rows.findIndex((r, i) => slotBefore(r, rows, i) === rollback)
  return at < 0 ? rows.length : at
}

/** The slot (a feature index 0..n) whose line is nearest to `y`; `lines` maps each slot to its y. */
export function nearestSlot(lines: ReadonlyArray<{ slot: number; y: number }>, y: number): number {
  let best = lines[0]
  for (const l of lines) if (best === undefined || Math.abs(l.y - y) < Math.abs(best.y - y)) best = l
  return best?.slot ?? 0
}
