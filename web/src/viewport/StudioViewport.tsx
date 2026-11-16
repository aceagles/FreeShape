import type { StudioOutputs } from '../wire'
import { Viewport } from './Viewport'

export { fetchStudio } from '../features/studioOutputs'

/**
 * The viewport for the shell's current tab: draws the outputs the shell loaded (see useStudioOutputs), and draws
 * again whenever they change. A tab that is not a Part Studio (an Assembly) leaves the last view in place until the
 * Assembly view exists. `selectedPart` is the id of the part drawn highlighted (see `ShellApi.selectedPart`).
 */
export function StudioViewport({ outputs, selectedPart = null }: { outputs: StudioOutputs | null; selectedPart?: string | null }) {
  return <Viewport outputs={outputs} selectedPart={selectedPart} />
}
