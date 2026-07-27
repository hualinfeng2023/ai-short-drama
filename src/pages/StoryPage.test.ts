import { describe, expect, it } from 'vitest'

import {
  resolveScriptSceneId,
  resolveStoryWorkbenchView,
} from './StoryPage'

describe('story workbench selection', () => {
  it('keeps an available requested view', () => {
    expect(resolveStoryWorkbenchView('relationships', {
      overview: true,
      relationships: true,
    })).toBe('relationships')
  })

  it('falls back to the first available view', () => {
    expect(resolveStoryWorkbenchView('script', {
      overview: true,
      script: false,
    })).toBe('overview')
  })

  it('keeps the selected scene when it still exists', () => {
    expect(resolveScriptSceneId([
      { id: 'scene-1' },
      { id: 'scene-2' },
    ], 'scene-2')).toBe('scene-2')
  })

  it('falls back to the first scene when the selection is stale', () => {
    expect(resolveScriptSceneId([
      { id: 'scene-1' },
      { id: 'scene-2' },
    ], 'missing')).toBe('scene-1')
  })
})
