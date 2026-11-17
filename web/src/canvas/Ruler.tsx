import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { evaluateNumber, formatNumber } from '../pad/editor'
import { NumberPad, PadField } from '../pad/NumberPad'
import { usePadEditor } from '../pad/usePadEditor'
import { InlineNumber } from '../dialog/InlineNumber'
import { viewOnScreen } from '../viewport/viewCommands'
import { endOnScreen, RulerTool } from './ruler'
import type { CalibrateBody, End, RulerState, RulerView } from './ruler'
import './ruler.css'

/*
 * Calibrate in the Canvas dialog (card I12.6). `CalibrateControls` is the one thing the generic feature dialog draws for a Canvas
 * besides its rows: the Calibrate button, the two options and, while the ruler runs, what to do next and the distance. The marks
 * on the view are a layer this component puts over it (`RulerMarks`); every press on the view is the pointer module's (ruler.ts).
 */

/** The ruler that is running, so that Esc (the dialog's key) can end it before it cancels the dialog. */
let running: { stop: () => void } | null = null

/** Whether a ruler is running: the dialog's Enter does nothing then, and its Esc ends the ruler instead of the dialog. */
export const rulerRunning = (): boolean => running !== null

/** Ends a running ruler with nothing changed; false when none is running. */
export function endRuler(): boolean {
  if (running === null) return false
  running.stop()
  return true
}

export interface CalibrateControlsProps {
  /** The feature id of the canvas being edited; null while one is being made (the preview is then the only canvas that changes). */
  editing: string | null
  desktop: boolean
  /** Sends the calibrate request; true once the dialog has taken the new size. A refusal is the dialog's own banner. */
  calibrate: (body: CalibrateBody) => Promise<boolean>
  /** The ruler is starting: the dialog closes a field's pad, so two pads are never open. */
  onBegin: () => void
  /** A field's pad is open: the ruler ends, since the pad dock is the one place a number is taken. */
  fieldPad: boolean
  /** The dialog is open (not opening, failed or accepting). */
  ready: boolean
}

export const CENTRE_LABEL = 'Centre on midpoint'
export const SNAP_LABEL = 'Snap rotation to line'

const HELP = {
  first: { desktop: 'Click the first end of a length you know on the picture.', tablet: 'Tap the first end of a length you know on the picture, or put the pen down on it and drag to the second end.' },
  second: { desktop: 'Click the second end. Hold Shift to snap the line to 5 degree steps.', tablet: 'Tap the second end, or lift the pen on it. Drag the picture with two fingers to move the view.' },
  set: { desktop: 'Type the real distance between the ends. Drag an end to move it.', tablet: 'Enter the real distance between the ends on the pad. Drag an end to move it.' },
} as const

export function CalibrateControls({ editing, desktop, calibrate, onBegin, fieldPad, ready }: CalibrateControlsProps) {
  const [centre, setCentre] = useState(false)
  const [snap, setSnap] = useState(false)
  const [tool, setTool] = useState<RulerTool | null>(null)
  const [view, setView] = useState<RulerView | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Desktop mode: a pen or a finger on the distance box opens the pad instead
  const [padFor, setPadFor] = useState(false)
  const [, redraw] = useReducer((n: number) => n + 1, 0)

  const stop = useCallback(() => {
    setTool((t) => {
      t?.stop()
      return null
    })
    setView(null)
    setPadFor(false)
    setMessage(null)
  }, [])

  useEffect(() => {
    if (tool === null) return
    const me = { stop }
    running = me
    const off = tool.subscribe(redraw)
    return () => {
      off()
      if (running === me) running = null
    }
  }, [tool, stop])
  // leaving the dialog ends the ruler (the pointer module gets its handlers back)
  const toolRef = useRef<RulerTool | null>(null)
  useEffect(() => {
    toolRef.current = tool
  })
  useEffect(() => () => toolRef.current?.stop(), [])
  // a field's pad opened under the ruler: the ruler gives way
  useEffect(() => {
    if (fieldPad) endRuler()
  }, [fieldPad])
  useEffect(() => {
    if (!ready) endRuler()
  }, [ready])

  const root = useRef<HTMLDivElement>(null)
  const phaseNow = tool?.state.phase ?? null
  useEffect(() => {
    if (phaseNow !== null) root.current?.scrollIntoView?.({ block: 'nearest' })
  }, [phaseNow])

  const begin = () => {
    const v = viewOnScreen()?.rulerHost?.(editing) ?? null
    if (v === null) {
      setMessage('The view is not ready: try again in a moment.')
      return
    }
    onBegin()
    const t = new RulerTool(v)
    t.start()
    setView(v)
    setTool(t)
    setMessage(null)
  }

  const send = async (text: string): Promise<boolean> => {
    if (tool === null || busy) return false
    const r = evaluateNumber(text)
    if (!r.ok || r.value <= 0) {
      setMessage('The distance must be a number above 0.')
      return false
    }
    const body = tool.body(r.value, centre, snap)
    if (body === null) return false
    setBusy(true)
    setMessage(null)
    const ok = await calibrate(body)
    setBusy(false)
    if (ok) stop()
    return ok
  }

  const st = tool?.state ?? null
  const phase = st?.phase ?? null
  const mode = desktop ? 'desktop' : 'tablet'
  const length = st?.length ?? null
  const measured = length === null ? '' : formatNumber(length)
  const note = message ?? st?.note ?? null

  return (
    <div ref={root} className="calibrate" data-testid="calibrate" data-phase={phase ?? 'off'} data-a={st?.a ? `${st.a.u},${st.a.v}` : undefined} data-b={st?.b ? `${st.b.u},${st.b.v}` : undefined}>
      <div className="calibrate-title">Calibrate</div>
      {tool === null ? (
        <button type="button" className="calibrate-button" data-testid="calibrate-start" disabled={!ready} onClick={begin}>
          Calibrate with a ruler
        </button>
      ) : (
        <>
          <p className="calibrate-help" data-testid="calibrate-help" role="status">
            {HELP[phase ?? 'first'][mode]}
            {length !== null && (
              <>
                {' '}
                <span className="calibrate-length" data-testid="ruler-length" data-mm={length}>
                  Length now {formatNumber(length, 2)} mm
                </span>
              </>
            )}
          </p>
          {phase === 'set' && desktop && !padFor && <DistanceBox key={measured} value={measured} onSend={send} onCancel={stop} onPad={() => setPadFor(true)} />}
          {phase === 'set' && (!desktop || padFor) && <DistancePad key={measured} value={measured} onSend={send} onCancel={stop} />}
          {note !== null && (
            <p className="calibrate-note" role="alert" data-testid="calibrate-note">
              {note}
            </p>
          )}
          <div className="calibrate-actions">
            <button type="button" className="calibrate-button" data-quiet="" data-testid="calibrate-cancel" aria-label="Cancel ruler" onClick={stop}>
              Cancel
            </button>
          </div>
        </>
      )}
      <Option label={CENTRE_LABEL} on={centre} desktop={desktop} onToggle={() => setCentre((v) => !v)} />
      <Option label={SNAP_LABEL} on={snap} desktop={desktop} onToggle={() => setSnap((v) => !v)} />
      {tool !== null && view !== null && st !== null && <RulerMarks view={view} state={st} />}
    </div>
  )
}

/** One of the two options, as the dialog's own checks: a switch in Tablet mode, a checkbox in Desktop mode. */
function Option({ label, on, desktop, onToggle }: { label: string; on: boolean; desktop: boolean; onToggle: () => void }) {
  if (desktop)
    return (
      <label className="dlg-check">
        <input type="checkbox" checked={on} onChange={onToggle} />
        <span>{label}</span>
      </label>
    )
  return (
    <button type="button" role="switch" aria-checked={on} className="dlg-switch" onClick={onToggle}>
      <span>{label}</span>
      <span className="dlg-track" aria-hidden="true">
        <span className="dlg-thumb" />
      </span>
    </button>
  )
}

/** Desktop mode: the distance typed in the dialog's own inline box, prefilled with the measured length; Enter sends it, Esc ends the ruler. */
function DistanceBox({ value, onSend, onCancel, onPad }: { value: string; onSend: (text: string) => Promise<boolean>; onCancel: () => void; onPad: () => void }) {
  // the text the box holds: what it opened with, or what was last committed (Enter and Tab commit, then Enter sends)
  const [text, setText] = useState(value)
  const latest = useRef(value)
  return (
    <div className="dlg-row dlg-row-inline">
      <div className="dlg-row-line">
        <div className="dlg-label">Distance</div>
        <InlineNumber
          value={text}
          label="Distance"
          unit="mm"
          autoFocus
          commitOnBlur={false}
          onCommit={(t) => {
            latest.current = t
            setText(t)
          }}
          onEnter={() => void onSend(latest.current)}
          onEscape={onCancel}
          onPad={onPad}
          fieldKey="ruler-distance"
          testId="ruler-distance"
        />
      </div>
    </div>
  )
}

/** Tablet mode: the distance on the pad (docked at the foot of the dialog), prefilled with the measured length; Enter sends it, Cancel ends the ruler. */
function DistancePad({ value, onSend, onCancel }: { value: string; onSend: (text: string) => Promise<boolean>; onCancel: () => void }) {
  const editor = usePadEditor({ value, variables: [], onCommit: (t) => void onSend(t), onCancel })
  // the pad takes much of the column: the field it types into must stay in sight
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    box.current?.scrollIntoView?.({ block: 'nearest' })
  }, [])
  return (
    <div ref={box} className="dlg-number" data-testid="ruler-pad">
      <div className="dlg-number-field">
        <PadField editor={editor} label="Distance (mm)" />
      </div>
      <NumberPad editor={editor} />
    </div>
  )
}

const LOUPE = 120
const LOUPE_ZOOM = 2.5
/** How far above the pen tip the magnifier sits (centre to tip), view pixels; below the tip when the tip is near the top. */
const LOUPE_LIFT = 100

/**
 * The ruler's marks over the view: the line, its two ends and its length, and, while a pen is down, a magnifier above the tip so
 * the hand does not hide the end (it shows the view's pixels around the tip at LOUPE_ZOOM, copied from the 3D view's canvas right
 * after each frame, and the line and ends at that scale). Drawn into an element laid over the view; nothing here takes a pointer.
 */
function RulerMarks({ view, state }: { view: RulerView; state: RulerState }) {
  const [, redraw] = useReducer((n: number) => n + 1, 0)
  const loupe = useRef<HTMLCanvasElement>(null)
  const loupeAt = state.loupe
  // the camera moved or a new preview arrived: the marks are drawn again
  useEffect(() => view.onFrame(redraw), [view])
  // the magnifier's picture: the 3D view's pixels, copied in the frame's own task (a WebGL canvas is blank after it)
  useEffect(() => {
    if (loupeAt === null) return
    const copy = () => {
      const c = loupe.current
      const ctx = c?.getContext('2d') ?? null
      if (c === null || ctx === null) return
      const s = view.surface
      const k = s.clientWidth > 0 ? s.width / s.clientWidth : 1
      const half = LOUPE / 2 / LOUPE_ZOOM
      c.width = LOUPE * k
      c.height = LOUPE * k
      try {
        ctx.drawImage(s, (loupeAt.x - half) * k, (loupeAt.y - half) * k, 2 * half * k, 2 * half * k, 0, 0, c.width, c.height)
      } catch {
        // a canvas that cannot be read: the magnifier shows the marks alone
      }
    }
    copy()
    const off = view.onFrame(copy)
    view.requestRender()
    return off
  }, [view, loupeAt])

  const spec = view.canvas()
  if (spec === null) return null
  const at = (e: End | null) => (e === null ? null : endOnScreen(spec, view.view, e))
  const a = at(state.a)
  const b = at(state.b)
  const hover = at(state.hover)
  const mid = a && b ? { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } : null
  const mark = (p: { x: number; y: number }, r: number, key: string) => <circle key={key} className="ruler-end" cx={p.x} cy={p.y} r={r} />
  const lines = (k: number, ox: number, oy: number) =>
    a && b ? (
      <g transform={`translate(${ox} ${oy}) scale(${k})`}>
        <line className="ruler-halo" x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
        <line className="ruler-line" x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
      </g>
    ) : null
  const lift = loupeAt === null ? 0 : loupeAt.y < LOUPE + 40 ? -LOUPE_LIFT : LOUPE_LIFT
  const centre = loupeAt === null ? null : { x: loupeAt.x, y: loupeAt.y - lift }
  return createPortal(
    <div className="ruler-layer" data-testid="ruler-layer" aria-hidden="true">
      <svg>
        {lines(1, 0, 0)}
        {a && mark(a, 6, 'a')}
        {b && mark(b, 6, 'b')}
        {hover && a === null && <circle className="ruler-end" cx={hover.x} cy={hover.y} r={4} opacity={0.6} />}
        {mid && state.length !== null && (
          <text className="ruler-tag" x={mid.x + 10} y={mid.y - 10}>
            {formatNumber(state.length, 2)} mm
          </text>
        )}
      </svg>
      {centre !== null && loupeAt !== null && (
        <div className="ruler-loupe" data-testid="ruler-loupe" style={{ left: centre.x - LOUPE / 2, top: centre.y - LOUPE / 2 }}>
          <canvas ref={loupe} />
          <svg viewBox={`0 0 ${LOUPE} ${LOUPE}`}>
            {lines(LOUPE_ZOOM, LOUPE / 2 - loupeAt.x * LOUPE_ZOOM, LOUPE / 2 - loupeAt.y * LOUPE_ZOOM)}
            {[a, b].map((p, i) => p && <circle key={i} className="ruler-end" cx={LOUPE / 2 + (p.x - loupeAt.x) * LOUPE_ZOOM} cy={LOUPE / 2 + (p.y - loupeAt.y) * LOUPE_ZOOM} r={6} />)}
            <path d={`M ${LOUPE / 2 - 10} ${LOUPE / 2} H ${LOUPE / 2 + 10} M ${LOUPE / 2} ${LOUPE / 2 - 10} V ${LOUPE / 2 + 10}`} stroke="#c5221f" strokeWidth="1.5" />
          </svg>
        </div>
      )}
    </div>,
    view.element,
  )
}
