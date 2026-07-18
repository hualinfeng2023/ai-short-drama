import type { ProjectReadiness } from '../types'

/**
 * 本地演示模式下的项目阶段快照。
 * 经典镜头工作流（验证样片）保持可用；五阶段制作流的各阶段以 LOCKED
 * 形式展示并说明解锁条件，让阶段导航在任何模式下都在场。
 */
export function buildLocalReadiness({
  projectId,
  episodeId,
  activeJobCount,
}: {
  projectId: string
  episodeId: string | null
  activeJobCount: number
}): ProjectReadiness {
  const episodeHref = episodeId
    ? `/projects/${projectId}/episodes/${episodeId}`
    : `/projects/${projectId}`
  const lockedDetail = '属于五阶段制作流，连接本地服务端后可用'
  return {
    projectId,
    workflowMode: 'CLASSIC',
    projectStatus: 'PRODUCING',
    summaryStatus: 'IN_PROGRESS',
    activeStageKey: 'episode',
    activeJobCount,
    stages: [
      {
        key: 'episode',
        label: '第 1 集 · 验证样片',
        status: 'IN_PROGRESS',
        href: episodeHref,
        detail: '经典镜头工作流，浏览器演示模式可直接体验',
      },
      { key: 'brief', label: '项目简报', status: 'LOCKED', href: `/projects/${projectId}`, detail: lockedDetail },
      { key: 'story', label: '故事与剧本', status: 'LOCKED', href: `/projects/${projectId}/story`, detail: lockedDetail },
      { key: 'preproduction', label: '前期资产', status: 'LOCKED', href: `/projects/${projectId}/preproduction`, detail: lockedDetail },
      { key: 'storyboard', label: '动态分镜', status: 'LOCKED', href: `/projects/${projectId}/storyboard`, detail: lockedDetail },
      { key: 'production', label: '正式制作', status: 'LOCKED', href: `/projects/${projectId}/production`, detail: lockedDetail },
    ],
    blockers: [],
    nextActionLabel: '继续镜头制作',
    nextActionHref: episodeHref,
    updatedAt: new Date().toISOString(),
  }
}
