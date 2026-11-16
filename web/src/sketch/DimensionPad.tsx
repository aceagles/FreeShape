import { useSyncExternalStore } from 'react'
import { NumberPad, PadField } from '../pad/NumberPad'
import { usePadEditor } from '../pad/usePadEditor'
import type { ShellApi } from '../shell/ShellContext'
import type { DimensionPadState, DimensionTool } from './dimension'
import './sketch.css'

/**
 * The content of the dialog area while the Dimension tool has a number to take (I7.11): the dimension's value on the pad,
 * docked at the foot of the column as for a feature's number. Enter sends it; a refusal shows the server's words above the
 * pad and keeps what was typed.
 */
export function DimensionPad({ tool, variables }: { tool: DimensionTool; variables: readonly string[] }) {
  const { pad } = useSyncExternalStore(tool.subscribe, tool.getState)
  if (pad === null) return null
  return <PadSession key={pad.seq} pad={pad} tool={tool} variables={variables} />
}

function PadSession({ pad, tool, variables }: { pad: DimensionPadState; tool: DimensionTool; variables: readonly string[] }) {
  const editor = usePadEditor({ value: pad.value, variables, onCommit: tool.padCommit, onCancel: tool.padCancel, active: !pad.pending })
  return (
    <>
      <PadField editor={editor} label={pad.label} />
      {pad.error !== null && (
        <div className="dimension-refusal" role="alert" data-testid="dimension-refusal">
          {pad.error}
        </div>
      )}
      <NumberPad editor={editor} />
    </>
  )
}

/** What the Dimension tool wants next, over the view's top left under the chips; nothing while the pad is open. */
export function DimensionHint({ tool }: { tool: DimensionTool }) {
  const { hint } = useSyncExternalStore(tool.subscribe, tool.getState)
  if (hint === null) return null
  return (
    <div className="sketch-hint" role="status" data-testid="dimension-hint">
      {hint}
    </div>
  )
}

/** Opens and closes the Dimension tool's pad in the shell's dialog area; the shell binds its current API and variables after each render. */
export class DimensionDock {
  private api: ShellApi | null = null
  private variables: readonly string[] = []

  bind(api: ShellApi, variables: readonly string[]): void {
    this.api = api
    this.variables = variables
  }
  readonly open = (tool: DimensionTool): void => this.api?.openDialog('Dimension', <DimensionPad tool={tool} variables={this.variables} />)
  readonly close = (): void => this.api?.closeDialog()
}
