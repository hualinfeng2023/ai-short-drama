import { describe, expect, it } from 'vitest'

import { normalizeCachedStudioState } from './StudioContext'

describe('normalizeCachedStudioState', () => {
  it('repairs invalid legacy project fields before the first route render', () => {
    const state = normalizeCachedStudioState({
      project: {
        id: 'legacy-project',
        name: null,
        scenes: null,
        shots: null,
        updatedAt: null,
      },
      jobs: [{
        id: 'legacy-job',
        entityType: 'shot',
        entityId: 'legacy-shot',
      }],
    })

    expect(state.project.id).toBe('legacy-project')
    expect(state.project.name).toBeTruthy()
    expect(Array.isArray(state.project.scenes)).toBe(true)
    expect(Array.isArray(state.project.shots)).toBe(true)
    expect(state.project.updatedAt).toBeTruthy()
    expect(state.jobs[0].entity).toBe('shot:legacy-shot')
  })
})
