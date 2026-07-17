import { describe, expect, it, vi } from 'vitest'
import { initialAppState } from '../data/demo'
import { prepareCurrentProjectRecovery } from './studioRecovery'

describe('prepareCurrentProjectRecovery', () => {
  it('reloads the requested project before clearing the local cache', async () => {
    const workspace = {
      project: structuredClone(initialAppState.project),
      jobs: structuredClone(initialAppState.jobs),
    }
    const projects = [{
      ...workspace.project,
      episodeCount: 1,
      sceneCount: workspace.project.scenes.length,
      shotCount: workspace.project.shots.length,
    }]
    const order: string[] = []
    const fetchCurrentWorkspace = vi.fn(async (projectId: string) => {
      order.push(`workspace:${projectId}`)
      return workspace
    })
    const fetchProjectSummaries = vi.fn(async () => {
      order.push('projects')
      return projects
    })
    const clearLocalCache = vi.fn(() => order.push('clear'))

    const result = await prepareCurrentProjectRecovery({
      projectId: 'current-project',
      fetchCurrentWorkspace,
      fetchProjectSummaries,
      clearLocalCache,
    })

    expect(fetchCurrentWorkspace).toHaveBeenCalledWith('current-project')
    expect(result).toEqual({ workspace, projects })
    expect(order.at(-1)).toBe('clear')
  })

  it('keeps the local cache when SQLite cannot be read', async () => {
    const clearLocalCache = vi.fn()

    await expect(prepareCurrentProjectRecovery({
      projectId: 'current-project',
      fetchCurrentWorkspace: async () => { throw new Error('workspace unavailable') },
      fetchProjectSummaries: async () => [],
      clearLocalCache,
    })).rejects.toThrow('workspace unavailable')

    expect(clearLocalCache).not.toHaveBeenCalled()
  })
})
