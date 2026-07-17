import type { Job } from '../types'

export interface JobCta {
  href: string
  label: string
}

export interface JobCtaContext {
  episodeId?: string
  sceneId?: string
  shotId?: string
}

export interface FailedJobGuidance {
  description: string
  retryLabel: string
  secondaryCta?: JobCta
}

function relationshipBlockingDetails(job: Job) {
  const rawIssues = job.errorDetails?.issues
  const issues = Array.isArray(rawIssues)
    ? rawIssues.filter((issue): issue is Record<string, unknown> => (
      typeof issue === 'object' && issue !== null
    ))
    : []
  const hiddenIssues = issues.filter(
    (issue) => issue.code === 'HIDDEN_RELATIONSHIP_WITHOUT_REVEAL',
  )
  const hiddenKeys = [...new Set(hiddenIssues
    .map((issue) => issue.relationship_key)
    .filter((key): key is string => typeof key === 'string' && key.length > 0))]
  const blockingKeys = [...new Set(issues
    .map((issue) => issue.relationship_key)
    .filter((key): key is string => typeof key === 'string' && key.length > 0))]
  return { hiddenCount: hiddenIssues.length, hiddenKeys, blockingKeys }
}

export function getFailedJobGuidance(
  job: Job,
  projectId: string,
): FailedJobGuidance | null {
  if (job.status !== 'FAILED') return null

  if (job.errorCode === 'RELATIONSHIP_GRAPH_SEMANTIC_INVALID') {
    const { hiddenCount, hiddenKeys, blockingKeys } = relationshipBlockingDetails(job)
    const relationKeys = hiddenKeys.length > 0 ? hiddenKeys : blockingKeys
    const description = hiddenCount > 0
      ? `${hiddenCount} 条隐藏关系缺少揭示计划：${relationKeys.join('、')}。模型已按校验反馈修复两次，结果仍未通过。`
      : `角色关系网仍有语义阻断项${relationKeys.length > 0 ? `：${relationKeys.join('、')}` : ''}。模型已按校验反馈修复两次，结果仍未通过。`
    return {
      description,
      retryLabel: '重新生成关系网',
      secondaryCta: {
        href: `/projects/${projectId}/story`,
        label: '检查故事方向',
      },
    }
  }

  if (job.errorCode === 'RELATIONSHIP_GRAPH_SCHEMA_INVALID') {
    return {
      description: '生成的关系网未通过批准校验，结果未保存。可重新生成；若再次失败，请检查故事方向中的角色与关系设定。',
      retryLabel: '重新生成关系网',
      secondaryCta: {
        href: `/projects/${projectId}/story`,
        label: '检查故事方向',
      },
    }
  }

  return null
}

export function getCompletedJobCta(
  job: Job,
  projectId: string,
  context: JobCtaContext = {},
): JobCta | null {
  if (job.status !== 'SUCCEEDED') return null

  if (job.entityType === 'shot' || job.entity?.startsWith('shot:')) {
    if (context.episodeId && context.sceneId && context.shotId) {
      return {
        href: `/projects/${projectId}/episodes/${context.episodeId}/scenes/${context.sceneId}?shot=${context.shotId}`,
        label: '查看镜头',
      }
    }
    return { href: `/projects/${projectId}/storyboard`, label: '查看镜头' }
  }

  switch (job.jobType) {
    case 'GENERATE_STORY_DIRECTIONS':
      return { href: `/projects/${projectId}/story`, label: '查看 3 个方向' }
    case 'GENERATE_STORY_PACKAGE':
      return { href: `/projects/${projectId}/story`, label: '查看创作包' }
    case 'GENERATE_STORY_STRUCTURE':
      return { href: `/projects/${projectId}/story`, label: '审核角色关系' }
    case 'GENERATE_SCRIPT_PACKAGE':
      return { href: `/projects/${projectId}/story`, label: '查看分集大纲与剧本' }
    case 'GENERATE_CHARACTER_CANDIDATES':
    case 'GENERATE_CHARACTER_CANDIDATE':
    case 'GENERATE_CHARACTER_LOOKS':
      return { href: `/projects/${projectId}/preproduction`, label: '查看角色资产' }
    case 'GENERATE_STORYBOARD_V2':
    case 'GENERATE_STORYBOARD_TAKE':
    case 'GENERATE_ANIMATIC':
      return { href: `/projects/${projectId}/storyboard`, label: '查看分镜' }
    case 'START_MEDIA_PRODUCTION':
    case 'GENERATE_AUDIO_PIPELINE':
    case 'ASSEMBLE_MULTITRACK_TIMELINE':
    case 'EXPORT_PACKAGE_V2':
      return { href: `/projects/${projectId}/production`, label: '查看制作结果' }
    default:
      return { href: `/projects/${projectId}`, label: '查看任务结果' }
  }
}
