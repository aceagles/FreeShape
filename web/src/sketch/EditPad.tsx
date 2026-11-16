import { useSyncExternalStore } from 'react'
import { NumberPad, PadField } from '../pad/NumberPad'
import { usePadEditor } from '../pad/usePadEditor'
import type { ShellApi } from '../shell/ShellContext'
import type { CopyTools } from './tools/copy'
import type { EditTools, FilletPadState } from './tools/edit'
import './sketch.css'

/** The radius of a corner fillet on the pad, in the dialog area as the Dimension tool's value is (I7.12). */
export function FilletPad({ tool, variables }: { tool: EditTools; variables: readonly string[] }) {
  const { pad } = useSyncExternalStore(tool.subscribe, tool.getState)
  if (pad === null) return null
  return <PadSession key={pad.seq} pad={pad} tool={tool} variables={variables} />
}

function PadSession({ pad, tool, variables }: { pad: FilletPadState; tool: EditTools; variables: readonly string[] }) {
  const editor = usePadEditor({ value: pad.value, variables, onCommit: tool.padCommit, onCancel: tool.padCancel, active: !pad.pending })
  return (
    <>
      <PadField editor={editor} label={pad.label} />
      {pad.error !== null && (
        <div className="dimension-refusal" role="alert" data-testid="fillet-refusal">
          {pad.error}
        </div>
      )}
      <NumberPad editor={editor} />
    </>
  )
}

/** What Trim, Fillet or Use edge wants next, over the view's top left under the chips. */
export function EditHint({ tool }: { tool: EditTools }) {
  const { hint } = useSyncExternalStore(tool.subscribe, tool.getState)
  if (hint === null) return null
  return (
    <div className="sketch-hint" role="status" data-testid="edit-hint">
      {hint}
    </div>
  )
}

/** What Mirror wants next, or the server's refusal of it, in the same place as the edit tools' hint. */
export function CopyHint({ tool }: { tool: CopyTools }) {
  const { hint } = useSyncExternalStore(tool.subscribe, tool.getState)
  if (hint === null) return null
  return (
    <div className="sketch-hint" role="status" data-testid="copy-hint">
      {hint}
    </div>
  )
}

/** Opens and closes the radius pad in the shell's dialog area; the shell binds its current API and variables after each render. */
export class EditDock {
  private api: ShellApi | null = null
  private variables: readonly string[] = []

  bind(api: ShellApi, variables: readonly string[]): void {
    this.api = api
    this.variables = variables
  }
  readonly open = (tool: EditTools): void => this.api?.openDialog('Corner fillet', <FilletPad tool={tool} variables={this.variables} />)
  readonly close = (): void => this.api?.closeDialog()
}
