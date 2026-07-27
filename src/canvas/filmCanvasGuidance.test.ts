import { describe, expect, it } from 'vitest'
import type { ProjectReadiness } from '../types'
import { getFilmCanvasGuidance } from './filmCanvasGuidance'

const readiness: ProjectReadiness = {
  projectId: 'project-1',
  workflowMode: 'PIPELINE',
  projectStatus: 'SCRIPT_READY',
  summaryStatus: 'ACTION_REQUIRED',
  activeStageKey: 'STORY',
  activeJobCount: 0,
  stages: [
    { key: 'BRIEF', label: '故事设定', status: 'COMPLETE', href: '/projects/project-1', detail: '项目目标与约束' },
    { key: 'STORY', label: '故事剧本', status: 'CURRENT', href: '/projects/project-1/story', detail: '故事方向与剧本' },
    { key: 'PREPRODUCTION', label: '前期资产', status: 'LOCKED', href: '/projects/project-1/preproduction', detail: '角色、造型、场景、道具与声音' },
  ],
  blockers: [],
  nextActionLabel: '继续故事剧本',
  nextActionHref: '/projects/project-1/story',
  updatedAt: '2026-07-27T00:00:00Z',
}

describe('Film Canvas next-action guidance', () => {
  it('turns generic readiness into a concrete action and payoff', () => {
    const guidance = getFilmCanvasGuidance(readiness)
    expect(guidance).toMatchObject({
      title: '审查并批准本集剧本',
      actionLabel: '审查并批准本集剧本',
      actionHref: '/projects/project-1/story',
      completedStageLabel: '故事设定',
      activeStageLabel: '故事剧本',
      nextStageLabel: '前期资产',
      recommendedNodeType: 'Script',
      tone: 'action',
    })
    expect(guidance.benefit).toContain('角色、造型、场景、道具与声音')
  })

  it('lets the canonical blocker replace the normal next action', () => {
    const guidance = getFilmCanvasGuidance({
      ...readiness,
      summaryStatus: 'BLOCKED',
      blockers: [{
        code: 'MISSING_TARGET',
        message: '还缺少目标受众。',
        actionLabel: '补充项目设定',
        actionHref: '/projects/project-1',
      }],
    })

    expect(guidance).toMatchObject({
      title: '先解决当前阻塞',
      description: '还缺少目标受众。',
      actionLabel: '补充项目设定',
      actionHref: '/projects/project-1',
      tone: 'blocked',
    })
  })
})
