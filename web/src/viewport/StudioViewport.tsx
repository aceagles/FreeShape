import type { SelectionStore } from '../selection/store'
import type { SketchStore } from '../sketch/store'
import type { StudioOutputs } from '../wire'
import type { PreviewLook } from './controller'
import { Viewport } from './Viewport'

export { fetchStudio } from '../features/studioOutputs'

/**
 * The viewport for the shell's current tab: draws the outputs the shell loaded (see useStudioOutputs), and draws
 * again whenever they change. A tab that is not a Part Studio (an Assembly) draws nothing and says so. `selection` is the store the view draws and picks for (see `ShellApi.selection`).
 */
export function StudioViewport({
  outputs,
  selection,
  tabKind = 'studio',
  preview = null,
  sketch = null,
}: {
  outputs: StudioOutputs | null
  selection?: SelectionStore
  tabKind?: string | null
  preview?: PreviewLook | null
  sketch?: SketchStore | null
}) {
  return <Viewport outputs={outputs} selection={selection} tabKind={tabKind} preview={preview} sketch={sketch} />
}
