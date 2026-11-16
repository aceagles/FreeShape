import { createPortal } from 'react-dom'
import { useDialogDock } from '../shell/DialogArea'
import type { PadEditor } from './usePadEditor'
import { UNITS } from './editor'
import type { PadAction } from './editor'
import './pad.css'

type Key =
  | { label: string; name: string; kind: 'action'; action: PadAction; style: 'digit' | 'plain' }
  | { label: string; name: string; kind: 'variables' | 'cancel' | 'enter'; style: 'plain' | 'enter' }

const ins = (label: string, name: string, text: string, style: 'digit' | 'plain'): Key => ({ label, name, kind: 'action', action: { kind: 'insert', text }, style })
const unit = (u: string): Key => ({ label: u, name: u, kind: 'action', action: { kind: 'unit', unit: u }, style: 'plain' })

/** The keys in the mock's order, five to a row: digits, the four operators, brackets, sign, units, #, Cancel, Enter. */
export const PAD_KEYS: readonly Key[] = [
  ins('7', '7', '7', 'digit'), ins('8', '8', '8', 'digit'), ins('9', '9', '9', 'digit'), ins('÷', 'Divide', '/', 'plain'),
  { label: 'Del', name: 'Backspace', kind: 'action', action: { kind: 'backspace' }, style: 'plain' },
  ins('4', '4', '4', 'digit'), ins('5', '5', '5', 'digit'), ins('6', '6', '6', 'digit'), ins('×', 'Multiply', '*', 'plain'), ins('(', 'Open bracket', '(', 'plain'),
  ins('1', '1', '1', 'digit'), ins('2', '2', '2', 'digit'), ins('3', '3', '3', 'digit'), ins('−', 'Minus', '-', 'plain'), ins(')', 'Close bracket', ')', 'plain'),
  ins('0', '0', '0', 'digit'), ins('.', 'Decimal point', '.', 'digit'),
  { label: '±', name: 'Change sign', kind: 'action', action: { kind: 'sign' }, style: 'plain' },
  ins('+', 'Plus', '+', 'plain'),
  { label: '#', name: 'Variables', kind: 'variables', style: 'plain' },
  ...UNITS.map(unit),
  { label: 'Cancel', name: 'Cancel', kind: 'cancel', style: 'plain' },
  { label: 'Enter', name: 'Enter', kind: 'enter', style: 'enter' },
]

/**
 * The field being edited: its text and a caret, drawn from a session of usePadEditor. It is a div, never an input, a
 * textarea or a contenteditable element, so iPadOS offers neither its keyboard nor handwriting for it.
 */
export function PadField({ editor, label, testId = 'pad-field' }: { editor: PadEditor; label?: string; testId?: string }) {
  const { text, caret, fresh } = editor
  return (
    <div className="pad-field-wrap">
      {label !== undefined && <div className="pad-field-label">{label}</div>}
      <div className="pad-field" role="textbox" aria-readonly="true" aria-label={label ? `${label}, editing on the pad` : 'Editing on the pad'} data-testid={testId}>
        {fresh ? (
          <span className="pad-selected" data-testid="pad-text">{text}</span>
        ) : (
          <span data-testid="pad-text">{text.slice(0, caret)}</span>
        )}
        <span className="pad-caret" aria-hidden="true" />
        {!fresh && <span>{text.slice(caret)}</span>}
      </div>
    </div>
  )
}

/**
 * The pad: a five-column grid of keys, with the list of variables in scope above it while the # key is on. In a dialog it
 * docks at the foot of the dialog column (through a portal into the dialog area's dock); anywhere else it renders in place.
 * It adds no pointer handler of its own beyond the keys' taps, and sits outside the viewport, so a tap never reaches it.
 */
export function NumberPad({ editor }: { editor: PadEditor }) {
  const dock = useDialogDock()
  const press = (k: Key) => {
    if (k.kind === 'action') editor.apply(k.action)
    else if (k.kind === 'variables') editor.toggleVariables()
    else if (k.kind === 'cancel') editor.cancel()
    else editor.commit()
  }
  const pad = (
    <div className="pad" data-testid="pad" data-region="pad">
      {editor.variablesOpen && (
        <div className="pad-vars" role="listbox" aria-label="Variables" data-testid="pad-variables">
          {editor.variables.length === 0 && <div className="pad-vars-empty">No variables in scope</div>}
          {editor.variables.map((name) => (
            <button
              key={name}
              type="button"
              role="option"
              aria-selected="false"
              className="pad-var"
              onPointerDown={(e) => e.preventDefault()}
              onClick={() => editor.pickVariable(name)}
            >
              #{name}
            </button>
          ))}
        </div>
      )}
      <div className="pad-keys">
        {PAD_KEYS.map((k) => (
          <button
            key={k.name}
            type="button"
            className={`pad-key pad-key-${k.style}`}
            data-word={k.label.length > 1 ? (k.label.length > 3 ? 'long' : 'short') : undefined}
            aria-label={k.name}
            aria-pressed={k.kind === 'variables' ? editor.variablesOpen : undefined}
            // keep focus where it is (no focus ring or scroll on a tap) and let nothing else take the touch
            onPointerDown={(e) => e.preventDefault()}
            onClick={() => press(k)}
          >
            {k.label}
          </button>
        ))}
      </div>
    </div>
  )
  return dock !== null ? createPortal(pad, dock) : pad
}
