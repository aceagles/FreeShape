import { useState } from 'react'
import { NumberPad, PadField } from '../pad/NumberPad'
import { usePadEditor } from '../pad/usePadEditor'
import { AXIS_NAMES, HANDLE_R, RING_R, padText, parseTyped, readoutText } from './gizmo'
import type { AxisShape, GizmoShape, HandleKind, HandleRef } from './gizmo'
import './gizmo.css'

const HEAD = 11
const same = (a: HandleRef | null, b: HandleRef): boolean => a !== null && a.kind === b.kind && a.axis === b.axis

/** The arrow of one axis: a shaft from the origin and a head at the tip, in the axis colour. */
function Arrow({ shape, axis }: { shape: GizmoShape; axis: AxisShape }) {
  if (!axis.tip || !axis.dir) return null
  const { tip, dir } = axis
  const o = shape.origin
  const nx = -dir.y
  const ny = dir.x
  const head = `${tip.x + dir.x * 2},${tip.y + dir.y * 2} ${tip.x - dir.x * HEAD + nx * 6},${tip.y - dir.y * HEAD + ny * 6} ${tip.x - dir.x * HEAD - nx * 6},${tip.y - dir.y * HEAD - ny * 6}`
  return (
    <g data-axis={AXIS_NAMES[axis.axis]}>
      <line x1={o.x} y1={o.y} x2={tip.x} y2={tip.y} stroke={axis.colour} strokeWidth={3} strokeLinecap="round" />
      <polygon points={head} fill={axis.colour} stroke={axis.colour} strokeWidth={1.5} strokeLinejoin="round" />
    </g>
  )
}

/** A Drag handle in the mock's look: a white disc ringed in the accent, with chevrons along the way it drags. */
function Handle({ at, dir, r, kind, active, colour, name }: { at: { x: number; y: number }; dir: { x: number; y: number }; r: number; kind: HandleKind; active: boolean; colour: string; name: string }) {
  const nx = -dir.y
  const ny = dir.x
  const p = (a: number, b: number) => `${at.x + dir.x * a + nx * b},${at.y + dir.y * a + ny * b}`
  const chev = kind === 'move' ? `M${p(-9, 0)}L${p(9, 0)}M${p(-4, -5)}L${p(-9, 0)}L${p(-4, 5)}M${p(4, -5)}L${p(9, 0)}L${p(4, 5)}` : ''
  return (
    <g data-handle={`${kind}-${name}`} className={active ? 'gizmo-handle gizmo-handle-active' : 'gizmo-handle'}>
      <circle cx={at.x} cy={at.y} r={r} fill={active ? 'var(--c-accentSoft)' : '#ffffff'} stroke={colour} strokeWidth={2.5} />
      {kind === 'move' ? (
        <path d={chev} fill="none" stroke={colour} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      ) : (
        <path d={`M${at.x - 6},${at.y + 1} A6.5 6.5 0 1 1 ${at.x + 3},${at.y + 6} M${at.x + 3},${at.y + 1} L${at.x + 3},${at.y + 6} L${at.x - 2},${at.y + 6}`} fill="none" stroke={colour} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      )}
    </g>
  )
}

/** The pad on the value of the last drag: a typed distance (mm, cm, m, in) or angle (deg, rad) moves by the difference. */
export function GizmoPad({ kind, value, onApply, onClose }: { kind: HandleKind; value: number; onApply: (total: number) => void; onClose: () => void }) {
  const [error, setError] = useState<string | null>(null)
  const editor = usePadEditor({
    value: padText(kind, value),
    onCommit: (text) => {
      const n = parseTyped(text, kind)
      if (n === null) {
        setError(kind === 'move' ? 'Type a distance, such as 12 mm or 0.5 in.' : 'Type an angle, such as 30 deg.')
        return
      }
      onApply(n)
      onClose()
    },
    onCancel: onClose,
  })
  return (
    <>
      <PadField editor={editor} label={kind === 'move' ? 'Distance' : 'Angle'} />
      {error && <p className="gizmo-pad-error" role="alert">{error}</p>}
      <NumberPad editor={editor} />
    </>
  )
}

/**
 * The gizmo drawn over an Assembly view: arrows, rings and the value label. It never takes a pointer itself (the pointer
 * module hands presses to the gizmo controller); only the value label is a button, which opens the pad.
 */
export function GizmoOverlay({ shape, onLabel }: { shape: GizmoShape | null; onLabel: () => void }) {
  if (!shape) return null
  const label = shape.readout
  return (
    <div className="gizmo" data-testid="gizmo" data-instance={shape.instance}>
      <svg className="gizmo-svg" aria-hidden="true">
        {shape.axes.map((a) => (
          <Arrow key={`a${a.axis}`} shape={shape} axis={a} />
        ))}
        {shape.axes.map((a) =>
          a.move && a.turn && a.dir ? (
            <g key={`h${a.axis}`}>
              <Handle at={a.turn} dir={a.dir} r={RING_R} kind="turn" active={same(shape.active, { kind: 'turn', axis: a.axis })} colour={a.colour} name={AXIS_NAMES[a.axis]} />
              <Handle at={a.move} dir={a.dir} r={HANDLE_R} kind="move" active={same(shape.active, { kind: 'move', axis: a.axis })} colour={a.colour} name={AXIS_NAMES[a.axis]} />
            </g>
          ) : null,
        )}
        <circle cx={shape.origin.x} cy={shape.origin.y} r={5} fill="#ffffff" stroke="#3b4250" strokeWidth={2} />
      </svg>
      {label && (
        <button type="button" className="gizmo-label" data-testid="gizmo-label" style={{ left: label.at.x + (label.handle.kind === 'move' ? HANDLE_R : RING_R) + 6, top: label.at.y }} onClick={onLabel}>
          {readoutText(label.handle.kind, label.value)}
        </button>
      )}
    </div>
  )
}
