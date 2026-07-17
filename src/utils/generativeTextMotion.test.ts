import { describe, expect, it } from 'vitest'
import {
  getGenerativeRevealCharacterCount,
  getGenerativeRevealDuration,
  getGenerativeRevealProgress,
} from './generativeTextMotion'

describe('generative text motion', () => {
  it('keeps the reveal duration within the interaction budget', () => {
    expect(getGenerativeRevealDuration(20)).toBe(1100)
    expect(getGenerativeRevealDuration(2000)).toBe(2400)
  })

  it('reveals content progressively and finishes exactly at the full length', () => {
    expect(getGenerativeRevealProgress(0, 1200)).toBe(0)
    expect(getGenerativeRevealCharacterCount(100, 400, 1200)).toBeGreaterThan(0)
    expect(getGenerativeRevealCharacterCount(100, 1200, 1200)).toBe(100)
    expect(getGenerativeRevealCharacterCount(100, 2000, 1200)).toBe(100)
  })
})
