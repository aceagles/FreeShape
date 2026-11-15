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
}

export const ShellContext = createContext<ShellApi | null>(null)

export function useShell(): ShellApi {
  const api = useContext(ShellContext)
  if (!api) throw new Error('useShell needs a <Shell> above it')
  return api
}
