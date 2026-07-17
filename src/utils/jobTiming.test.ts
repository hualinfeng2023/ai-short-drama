import { describe, expect, it } from 'vitest'

import type { Job } from '../types'
import { elapsedJobSeconds, formatElapsedTime } from './jobTiming'

const job = {
  createdAt: '2026-07-14T15:55:32.000Z',
  status: 'RUNNING',
} as Job

describe('job timing', () => {
  it('keeps active elapsed time moving from the creation timestamp', () => {
    expect(elapsedJobSeconds(job, Date.parse('2026-07-14T15:56:47.000Z'))).toBe(75)
  })

  it('freezes completed elapsed time at the completion timestamp', () => {
    expect(elapsedJobSeconds({
      ...job,
      status: 'SUCCEEDED',
      completedAt: '2026-07-14T15:57:23.000Z',
    }, Date.parse('2026-07-15T00:00:00.000Z'))).toBe(111)
  })

  it('formats seconds and minutes as explicit elapsed time', () => {
    expect(formatElapsedTime(15)).toBe('15 秒')
    expect(formatElapsedTime(111)).toBe('1 分 51 秒')
  })
})
