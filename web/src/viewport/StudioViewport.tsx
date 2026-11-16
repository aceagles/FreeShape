import type { StudioOutputs } from '../wire'
import { Viewport } from './Viewport'

export { fetchStudio } from '../features/studioOutputs'

/**
 * The viewport for the shell's current tab: draws the outputs the shell loaded (see useStudioOutputs), and draws
 * again whenever they change. A tab that is not a Part Studio (an Assembly) draws nothing and says so. `selectedPart` is the id of the part drawn highlighted (see `ShellApi.selectedPart`).
 */
export function StudioViewport({ outputs, selectedPart = null, tabKind = 'studio' }: { outputs: StudioOutputs | null; selectedPart?: string | null; tabKind?: string | null }) {
  return <Viewport outputs={outputs} selectedPart={selectedPart} tabKind={tabKind} />
}
