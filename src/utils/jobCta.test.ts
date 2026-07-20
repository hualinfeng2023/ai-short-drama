import { describe, expect, it } from 'vitest'

import type { Job } from '../types'
import { getCompletedJobCta, getFailedJobGuidance } from './jobCta'

const succeededJob = { status: 'SUCCEEDED' } as Job

describe('getCompletedJobCta', () => {
  it('routes completed story directions to the comparison page', () => {
    expect(getCompletedJobCta({
      ...succeededJob,
      jobType: 'GENERATE_STORY_DIRECTIONS',
    }, 'project-id')).toEqual({
      href: '/projects/project-id/story',
      label: '查看',
    })
  })

  it('routes both stages of the relationship-driven story workflow back to review', () => {
    expect(getCompletedJobCta({
      ...succeededJob,
      jobType: 'GENERATE_STORY_STRUCTURE',
    }, 'project-id')).toEqual({
      href: '/projects/project-id/story',
      label: '查看',
    })
    expect(getCompletedJobCta({
      ...succeededJob,
      jobType: 'GENERATE_SCRIPT_PACKAGE',
    }, 'project-id')).toEqual({
      href: '/projects/project-id/story',
      label: '查看分集大纲与剧本',
    })
  })

  it('does not show a completion CTA while a task is still running', () => {
    expect(getCompletedJobCta({
      ...succeededJob,
      jobType: 'GENERATE_STORY_DIRECTIONS',
      status: 'RUNNING',
    }, 'project-id')).toBeNull()
  })

  it('routes later workflow outputs to their matching workspace', () => {
    expect(getCompletedJobCta({
      ...succeededJob,
      jobType: 'GENERATE_ANIMATIC',
    }, 'project-id')?.href).toBe('/projects/project-id/storyboard')
    expect(getCompletedJobCta({
      ...succeededJob,
      jobType: 'ASSEMBLE_MULTITRACK_TIMELINE',
    }, 'project-id')?.href).toBe('/projects/project-id/production')
  })

  it('routes completed shot tasks directly to the matching shot workspace', () => {
    expect(getCompletedJobCta({
      ...succeededJob,
      entityType: 'shot',
      entity: 'shot:shot-id',
      jobType: 'GENERATE_SHOT_TAKE',
    }, 'project-id', {
      episodeId: 'episode-id',
      sceneId: 'scene-id',
      shotId: 'shot-id',
    })).toEqual({
      href: '/projects/project-id/episodes/episode-id/scenes/scene-id?shot=shot-id',
      label: '查看镜头',
    })
  })

  it('always provides a useful destination for an unknown completed task', () => {
    expect(getCompletedJobCta({
      ...succeededJob,
      entityType: 'project',
      entity: 'project:project-id',
      jobType: 'UNKNOWN_COMPLETED_JOB',
    }, 'project-id')).toEqual({
      href: '/projects/project-id',
      label: '查看',
    })
  })
})

describe('getFailedJobGuidance', () => {
  it('shows the true semantic error and every blocked hidden relationship', () => {
    const issues = ['lead-rival', 'lead-mentor', 'lead-family', 'lead-partner'].map(
      (relationshipKey) => ({
        severity: 'BLOCKER',
        code: 'HIDDEN_RELATIONSHIP_WITHOUT_REVEAL',
        message: '缺少揭示计划',
        relationship_key: relationshipKey,
      }),
    )
    expect(getFailedJobGuidance({
      ...succeededJob,
      status: 'FAILED',
      errorCode: 'RELATIONSHIP_GRAPH_SEMANTIC_INVALID',
      errorMessage: '火山方舟文本服务暂时不可达',
      errorDetails: { issues },
    }, 'project-id')).toEqual({
      description: '4 条隐藏关系缺少揭示计划：lead-rival、lead-mentor、lead-family、lead-partner。模型已按校验反馈修复两次，结果仍未通过。',
      retryLabel: '重新生成关系网',
      secondaryCta: {
        href: '/projects/project-id/story',
        label: '检查故事方向',
      },
    })
  })

  it('offers an actionable recovery path for a blocked relationship graph', () => {
    expect(getFailedJobGuidance({
      status: 'FAILED',
      errorCode: 'RELATIONSHIP_GRAPH_SCHEMA_INVALID',
    } as Job, 'project-id')).toEqual({
      description: '生成的关系网未通过批准校验，结果未保存。可重新生成；若再次失败，请检查故事方向中的角色与关系设定。',
      retryLabel: '重新生成关系网',
      secondaryCta: {
        href: '/projects/project-id/story',
        label: '检查故事方向',
      },
    })
  })

  it('does not replace generic handling for unrelated failures', () => {
    expect(getFailedJobGuidance({
      status: 'FAILED',
      errorCode: 'ARK_TEXT_TIMEOUT',
    } as Job, 'project-id')).toBeNull()
  })
})
