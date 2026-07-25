import { describe, expect, it } from 'vitest'

import { normalizeStudioPreferences } from './StudioContext'

describe('normalizeStudioPreferences', () => {
  it('restores only view preferences and ignores cached domain objects', () => {
    const preferences = normalizeStudioPreferences({
      visualMode: 'cinema',
      project: {
        id: 'legacy-project',
        shots: [{ id: 'must-not-be-restored' }],
      },
      jobs: [{ id: 'must-not-be-restored' }],
    })

    expect(preferences).toEqual({ visualMode: 'cinema' })
    expect('project' in preferences).toBe(false)
    expect('jobs' in preferences).toBe(false)
  })

  it('falls back when the preference payload is invalid', () => {
    expect(normalizeStudioPreferences({ visualMode: 'invalid' })).toEqual({
      visualMode: 'standard',
    })
  })
})
