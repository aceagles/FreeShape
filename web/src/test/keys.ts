/** A keydown as a keyboard makes it for a chord of bindings.ts: Shift+1 types "!", Space is " ". */
export function keyOf(chord: string): KeyboardEventInit {
  const parts = chord.split('+')
  const name = parts.pop()!
  const mods = new Set(parts)
  const shiftDigits = ')!@#$%^&*('
  let key = name
  let code: string | undefined
  if (/^\d$/.test(name)) {
    code = `Digit${name}`
    key = mods.has('Shift') ? shiftDigits[Number(name)]! : name
  } else if (/^[A-Z]$/.test(name)) {
    code = `Key${name}`
    key = mods.has('Shift') ? name : name.toLowerCase()
  } else if (name === 'Space') key = ' '
  return { key, code, ctrlKey: mods.has('Ctrl'), altKey: mods.has('Alt'), shiftKey: mods.has('Shift'), metaKey: mods.has('Meta') }
}
