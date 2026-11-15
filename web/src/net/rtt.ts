export const RTT_KEEP = 200

export interface RttSummary {
  count: number
  median: number | null
  worst: number | null
}

/** The last `keep` round trips of ping and pong, in milliseconds. */
export class RttLog {
  private values: number[] = []
  constructor(private readonly keep = RTT_KEEP) {}

  record(ms: number): void {
    this.values.push(ms)
    if (this.values.length > this.keep) this.values.splice(0, this.values.length - this.keep)
  }

  get samples(): readonly number[] {
    return this.values
  }

  summary(): RttSummary {
    return summarise(this.values)
  }
}

export function summarise(values: readonly number[]): RttSummary {
  if (values.length === 0) return { count: 0, median: null, worst: null }
  const sorted = [...values].sort((a, b) => a - b)
  const mid = sorted.length >> 1
  const median = sorted.length % 2 === 1 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2
  return { count: sorted.length, median, worst: sorted[sorted.length - 1]! }
}

/** The text the debug panel's button copies. */
export function describeRtt(s: RttSummary): string {
  if (s.median === null || s.worst === null) return 'round trips: none yet'
  return `round trips: median ${s.median.toFixed(1)} ms, worst ${s.worst.toFixed(1)} ms, over the last ${s.count}`
}
