import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'

/** What later cards use to reach the shell: the dialog area over the feature panel, and the panel's collapsed state. */
export interface ShellApi {
  /** Open the dialog area over the feature panel (expanding the panel if it is collapsed). `content` fills its body. */
  openDialog(title: string, content?: ReactNode): void
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
   * draws it highlighted. One thing is selected at a time for now: selecting a part clears the selected feature, and
   * selecting a feature clears the selected part. Slice 4 (card I4.2) replaces both with real selection.
   */
  selectedPart: string | null
  selectPart(id: string | null): void
}

export const ShellContext = createContext<ShellApi | null>(null)

export function useShell(): ShellApi {
  const api = useContext(ShellContext)
  if (!api) throw new Error('useShell needs a <Shell> above it')
  return api
}
