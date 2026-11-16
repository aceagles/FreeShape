import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import { InlineNumber } from '../dialog/InlineNumber'
import { NumberPad, PadField } from '../pad/NumberPad'
import { usePadEditor } from '../pad/usePadEditor'
import { useMode } from '../shell/mode'
import type { ShellApi } from '../shell/ShellContext'
import type { DimensionPadState, DimensionTool } from './dimension'
import type { Uv } from './wire'
import '../dialog/dialog.css'
import './sketch.css'

/** The variables in scope, as the shell last bound them to the dock (the inline box is not opened through the dock). */
let boundVariables: readonly string[] = []

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

/**
 * What the Dimension tool wants next, over the view's top left under the chips; nothing while the pad is open. In Desktop
 * mode it also tells the tool so, and draws the inline box of a mouse's dimension (card I9.16).
 */
export function DimensionHint({ tool }: { tool: DimensionTool }) {
  const { hint, pad } = useSyncExternalStore(tool.subscribe, tool.getState)
  const desktop = useMode() === 'desktop'
  useEffect(() => tool.setDesktop(desktop), [tool, desktop])
  if (pad?.inline) return <InlineDimension tool={tool} pad={pad} />
  if (hint === null) return null
  return (
    <div className="sketch-hint" role="status" data-testid="dimension-hint">
      {hint}
    </div>
  )
}

/** The text the box starts with: the desktop shows a measured value bare ("60"), an expression as typed. */
const UNIT_TAIL = / (mm|deg)$/
const boxText = (pad: DimensionPadState): string => (pad.value === pad.initial && /^[+-]?[\d.]+ (mm|deg)$/.test(pad.initial) ? pad.initial.replace(UNIT_TAIL, '') : pad.value)

/** Width of the box (sketcher.py `_make_editor`: 110 px, centred on the label, 12 px above its middle). */
const BOX_W = 110
const BOX_HALF_H = 13

/**
 * The desktop's inline dimension box (freeshape/ui/sketcher.py `edit_dimension`, `_make_editor`): a 110 px field at the
 * dimension's label (a new dimension: where its label goes), focused with its text selected. Enter sends the value; Esc
 * or leaving the box gives it up; a refusal shows the server's words under the box and keeps what was typed.
 */
function InlineDimension({ tool, pad }: { tool: DimensionTool; pad: DimensionPadState }) {
  // the view the box floats in: found from a hidden anchor where the shell renders the hint
  const [anchor, setAnchor] = useState<HTMLSpanElement | null>(null)
  const host = anchor?.closest<HTMLElement>('.shell-viewport') ?? null
  const boxRef = useRef<HTMLDivElement>(null)
  const shown = boxText(pad)
  // placed before it is painted, at the label; it stays where it opened, as the desktop's does
  useLayoutEffect(() => {
    const el = boxRef.current
    if (host === null || el === null) return
    const box = host.getBoundingClientRect()
    const c = anchorPoint(host, tool) ?? { x: box.width / 2, y: box.height / 2 }
    el.style.left = `${Math.min(Math.max(4, c.x - BOX_W / 2), Math.max(4, box.width - BOX_W - 4))}px`
    el.style.top = `${Math.max(4, c.y - BOX_HALF_H)}px`
  }, [host, tool, pad.mode])
  const field = host !== null && (
    <div ref={boxRef} className="dimension-inline" data-testid="dimension-inline">
      <InlineNumber
        value={shown}
        label={pad.label}
        variables={boundVariables}
        unitLabel={false}
        commitOnBlur={false}
        autoFocus
        width={BOX_W}
        testId="dimension-inline-field"
        onCommit={tool.padCommit}
        // Enter on the text as it opened: a new dimension takes the measure (the server keeps it exact), a changed one nothing
        onEnter={() => {
          const now = tool.getState().pad
          if (now !== null && !now.pending && tool.getState().pad === pad) tool.padCommit(shown === boxText(now) && now.value === now.initial ? now.initial : now.value)
        }}
        onEscape={tool.padCancel}
        onBlur={() => {
          // leaving the box without Enter gives the dimension up (sketcher.py `dismiss`); not while its answer is awaited
          const now = tool.getState().pad
          if (now !== null && !now.pending) tool.padCancel()
        }}
      />
      {pad.error !== null && (
        <div className="dimension-refusal" role="alert" data-testid="dimension-refusal">
          {pad.error}
        </div>
      )}
    </div>
  )
  return (
    <>
      <span ref={setAnchor} hidden />
      {field && createPortal(field, host)}
    </>
  )
}

/** The middle of an element, in the view's CSS pixels. */
function middleOf(el: Element, host: DOMRect): { x: number; y: number } {
  const r = el.getBoundingClientRect()
  return { x: r.left + r.width / 2 - host.left, y: r.top + r.height / 2 - host.top }
}

/**
 * Where the box goes, in the view's CSS pixels: the label of the dimension being changed; for a new one the point where
 * its label goes, carried to the screen by the map that takes the sketch's points to where the sketch layer draws them.
 */
function anchorPoint(host: HTMLElement, tool: DimensionTool): { x: number; y: number } | null {
  const a = tool.inlineAnchor()
  if (a === null) return null
  const box = host.getBoundingClientRect()
  const layer = host.querySelector('.sketch-layer')
  if ('constraint' in a) {
    const label = layer?.querySelector(`g[data-kind="dimension"][data-id="${CSS.escape(a.constraint)}"] rect`)
    return label ? middleOf(label, box) : null
  }
  const pairs: Array<[Uv, { x: number; y: number }]> = []
  for (const el of layer?.querySelectorAll<SVGElement>('circle[data-kind="point"][data-id]') ?? []) {
    const uv = a.points[el.dataset['id'] ?? '']
    if (uv) pairs.push([uv, middleOf(el, box)])
  }
  const map = fitAffine(pairs)
  if (map) return map(a.uv)
  // too few points to fit: the middle of the picked items
  const picked = a.picks.map((id) => layer?.querySelector(`[data-id="${CSS.escape(id)}"]`)).filter((el): el is Element => !!el)
  if (picked.length === 0) return null
  const ms = picked.map((el) => middleOf(el, box))
  return { x: ms.reduce((t, m) => t + m.x, 0) / ms.length, y: ms.reduce((t, m) => t + m.y, 0) / ms.length }
}

/** The least-squares affine map from sketch coordinates to the screen through these pairs; null with fewer than three points not on one line. */
function fitAffine(pairs: ReadonlyArray<[Uv, { x: number; y: number }]>): ((uv: Uv) => { x: number; y: number }) | null {
  if (pairs.length < 3) return null
  // normal equations: [Σuu Σuv Σu; Σuv Σvv Σv; Σu Σv n] · [a b c] = [Σu·t Σv·t Σt], for t = x and t = y
  let suu = 0, suv = 0, svv = 0, su = 0, sv = 0
  let sux = 0, svx = 0, sx = 0, suy = 0, svy = 0, sy = 0
  for (const [[u, v], { x, y }] of pairs) {
    suu += u * u; suv += u * v; svv += v * v; su += u; sv += v
    sux += u * x; svx += v * x; sx += x; suy += u * y; svy += v * y; sy += y
  }
  const n = pairs.length
  const det3 = (m: number[]) => m[0]! * (m[4]! * m[8]! - m[5]! * m[7]!) - m[1]! * (m[3]! * m[8]! - m[5]! * m[6]!) + m[2]! * (m[3]! * m[7]! - m[4]! * m[6]!)
  const M = [suu, suv, su, suv, svv, sv, su, sv, n]
  const d = det3(M)
  const scale = Math.max(1, Math.abs(suu * svv * n))
  if (Math.abs(d) < 1e-9 * scale) return null
  const solve = (r: [number, number, number]) => [0, 1, 2].map((col) => det3(M.map((m, i) => (i % 3 === col ? r[Math.floor(i / 3)]! : m))) / d)
  const [ax, bx, cx] = solve([sux, svx, sx])
  const [ay, by, cy] = solve([suy, svy, sy])
  return ([u, v]) => ({ x: ax! * u + bx! * v + cx!, y: ay! * u + by! * v + cy! })
}

/** Opens and closes the Dimension tool's pad in the shell's dialog area; the shell binds its current API and variables after each render. */
export class DimensionDock {
  private api: ShellApi | null = null
  private variables: readonly string[] = []

  bind(api: ShellApi, variables: readonly string[]): void {
    this.api = api
    this.variables = variables
    boundVariables = variables
  }
  readonly open = (tool: DimensionTool): void => this.api?.openDialog('Dimension', <DimensionPad tool={tool} variables={this.variables} />)
  readonly close = (): void => this.api?.closeDialog()
}
