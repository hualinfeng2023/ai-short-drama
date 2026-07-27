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

    expect(preferences).toEqual({ visualMode: 'cinema', themeMode: 'dark' })
    expect('project' in preferences).toBe(false)
    expect('jobs' in preferences).toBe(false)
  })

  it('falls back when the preference payload is invalid', () => {
    expect(normalizeStudioPreferences({ visualMode: 'invalid' })).toEqual({
      visualMode: 'standard',
      themeMode: 'system',
    })
  })

  it('restores an explicit color theme independently from the workspace mode', () => {
    expect(normalizeStudioPreferences({ visualMode: 'focus', themeMode: 'dark' })).toEqual({
      visualMode: 'focus',
      themeMode: 'dark',
    })
  })
})
