import { describe, expect, it } from 'vitest'
import { calculateProgress, initialAppState } from './demo'

describe('episode progress', () => {
  it('uses current shot states instead of a hard-coded percent', () => {
    expect(calculateProgress(initialAppState.project.shots)).toBe(56)
  })

  it('returns zero for an empty episode', () => {
    expect(calculateProgress([])).toBe(0)
  })
})
