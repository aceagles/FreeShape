import type { ViewContextEvent } from '../selection/aimCommit'
import type { SelectionStore } from '../selection/store'
import type { SketchStore } from '../sketch/store'
import type { StudioOutputs } from '../wire'
import type { PreviewLook } from './controller'
import type { ImageFetcher } from './canvases'
import { Viewport } from './Viewport'
import type { ViewState } from './visibility'

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
  visibility,
  images,
  onContext,
}: {
  outputs: StudioOutputs | null
  selection?: SelectionStore
  tabKind?: string | null
  preview?: PreviewLook | null
  sketch?: SketchStore | null
  visibility?: ViewState
  /** Where the canvases' pictures come from (card I12.5). */
  images?: ImageFetcher | null
  /** Desktop mode: a right click on the view (card I9.10). */
  onContext?: (e: ViewContextEvent) => void
}) {
  return <Viewport outputs={outputs} selection={selection} tabKind={tabKind} preview={preview} sketch={sketch} visibility={visibility} images={images} onContext={onContext} />
}
