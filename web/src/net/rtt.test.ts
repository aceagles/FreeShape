import { expect, it } from 'vitest'
import { RttLog, describeRtt, summarise } from './rtt'

it('computes the median and the worst of recorded round trips', () => {
  const log = new RttLog()
  for (const ms of [5, 1, 9, 3, 7]) log.record(ms)
  expect(log.summary()).toEqual({ count: 5, median: 5, worst: 9 })
  log.record(11)
  expect(log.summary()).toEqual({ count: 6, median: 6, worst: 11 })
})

it('reports nothing before the first round trip', () => {
  expect(summarise([])).toEqual({ count: 0, median: null, worst: null })
  expect(describeRtt(summarise([]))).toBe('round trips: none yet')
})

it('keeps the last 200 round trips', () => {
  const log = new RttLog()
  for (let i = 0; i < 250; i++) log.record(i)
  expect(log.samples.length).toBe(200)
  expect(log.samples[0]).toBe(50)
  expect(log.summary().worst).toBe(249)
})

it('describes median and worst for the copy button', () => {
  expect(describeRtt(summarise([2, 4, 30]))).toBe('round trips: median 4.0 ms, worst 30.0 ms, over the last 3')
})
