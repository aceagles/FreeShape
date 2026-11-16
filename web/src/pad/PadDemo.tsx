import { useShell } from '../shell/ShellContext'
import { NumberPad, PadField } from './NumberPad'
import { usePadEditor } from './usePadEditor'

export interface PadDemoOptions {
  title?: string
  label?: string
  value?: string
  variables?: readonly string[]
}

/**
 * Test-only content for the dialog area: one number field with the pad docked under it, until a dialog uses the pad
 * (I5.6). Reached from `window.__freeshape.openPadDemo()`, which tests and the screenshot script call; nothing in the
 * user's screen opens it. Commit and Cancel close the dialog; `window.__freeshape.padResult()` then gives what the pad ended with.
 */
export interface PadResult {
  committed: boolean
  text: string
}
let lastResult: PadResult | null = null
/** What the last demo pad ended with, or null while none has. */
export const padResult = (): PadResult | null => lastResult

export function PadDemo({ label, value, variables }: Required<Omit<PadDemoOptions, 'title'>>) {
  const shell = useShell()
  const finish = (result: PadResult) => {
    lastResult = result
    shell.closeDialog()
  }
  const editor = usePadEditor({
    value,
    variables,
    onCommit: (text) => finish({ committed: true, text }),
    onCancel: () => finish({ committed: false, text: value }),
  })
  return (
    <>
      <PadField editor={editor} label={label} />
      <NumberPad editor={editor} />
    </>
  )
}
