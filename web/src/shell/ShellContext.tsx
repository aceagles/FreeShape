import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'
import type { SelectionStore } from '../selection/store'

/** What later cards use to reach the shell: the dialog area over the feature panel, and the panel's collapsed state. */
export interface ShellApi {
  /** Open the dialog area over the feature panel (expanding the panel if it is collapsed). `content` fills its body; `actions`, when given, replace the header's close button (a sketch pattern's Cancel and Accept, I14.3). */
  openDialog(title: string, content?: ReactNode, actions?: ReactNode): void
  closeDialog(): void
  dialogOpen: boolean
  panelCollapsed: boolean
  setPanelCollapsed(collapsed: boolean): void
  /**
   * The feature selected in the feature panel (a feature id such as "F3") in the current Part Studio, or null. Slice 4
   * reads it to highlight the feature's geometry and sets it when a feature is picked in the view.
   */
  selectedFeature: string | null
  selectFeature(id: string | null): void
  /**
   * The part selected in the Parts list (a part id such as "F2/1") in the current Part Studio, or null; the viewport
   * draws it highlighted. It reads and writes the shared selection (`selection`): a part is one kind of item in it, and
   * `selectedPart` is the part when it is the one thing selected. `selectPart(id)` replaces the selection with that
   * part; `selectPart(null)` clears it when it holds only parts. A selected feature and the selection clear each
   * other: selecting a feature empties the selection, and any change of the selection deselects the feature.
   */
  selectedPart: string | null
  selectPart(id: string | null): void
  /** The selection of the view: what the pen and finger picked, the Multi mode and the Aim. Read it with `useSelection`. */
  selection: SelectionStore
}

export const ShellContext = createContext<ShellApi | null>(null)

export function useShell(): ShellApi {
  const api = useContext(ShellContext)
  if (!api) throw new Error('useShell needs a <Shell> above it')
  return api
}
