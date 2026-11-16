import { expect, it } from 'vitest'
import { DragLatency, describeDrag } from './latency'

it('summarises the round trip and the solve time of recorded drag answers', () => {
  const d = new DragLatency()
  expect(describeDrag(d.summary())).toBe('drag round trips: none yet')
  for (const [trip, solve] of [[30, 4], [10, 2], [20, 3]] as const) d.record(trip, solve)
  const s = d.summary()
  expect(s.roundTrip).toEqual({ count: 3, median: 20, worst: 30 })
  expect(s.solve.median).toBe(3)
  expect(describeDrag(s)).toBe('drag round trips: median 20.0 ms, worst 30.0 ms, solve median 3.0 ms, over the last 3')
})

it('keeps the last 200 moves', () => {
  const d = new DragLatency()
  for (let i = 0; i < 260; i++) d.record(i, 1)
  expect(d.summary().roundTrip).toEqual({ count: 200, median: 159.5, worst: 259 })
})
