import { RTT_KEEP, summarise, type RttSummary } from '../net/rtt'

export interface DragSummary {
  /** Client send time to the answer's arrival, over the last moves. */
  roundTrip: RttSummary
  /** The server's solve time reported in the same answers. */
  solve: RttSummary
}

/**
 * The drag round trip and the server's solve time of the last 200 `sketch.drag.moved` answers, in milliseconds.
 * A move carries the client's send time `t` (the connection's clock, `ConnectionStore.stamp()`); the answer echoes
 * it and adds `solve_ms`. Moves that were replaced by a later one get no answer and are not counted.
 */
export class DragLatency {
  private trips: number[] = []
  private solves: number[] = []
  constructor(private readonly keep = RTT_KEEP) {}

  record(roundTrip: number, solve: number): void {
    this.trips.push(roundTrip)
    this.solves.push(solve)
    if (this.trips.length > this.keep) {
      this.trips.splice(0, this.trips.length - this.keep)
      this.solves.splice(0, this.solves.length - this.keep)
    }
  }

  summary(): DragSummary {
    return { roundTrip: summarise(this.trips), solve: summarise(this.solves) }
  }
}

/** The text the debug panel's copy button adds for drags. */
export function describeDrag(s: DragSummary): string {
  const r = s.roundTrip
  if (r.median === null || r.worst === null || s.solve.median === null) return 'drag round trips: none yet'
  return `drag round trips: median ${r.median.toFixed(1)} ms, worst ${r.worst.toFixed(1)} ms, solve median ${s.solve.median.toFixed(1)} ms, over the last ${r.count}`
}
