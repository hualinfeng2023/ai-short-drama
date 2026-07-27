import type { ProjectReadiness, ProjectStatus } from '../types'

export interface FilmCanvasGuidance {
  actionHref: string
  actionLabel: string
  activeStageLabel: string
  benefit: string | null
  completedStageLabel: string | null
  description: string
  nextStageLabel: string | null
  recommendedNodeType: string | null
  title: string
  tone: 'action' | 'blocked' | 'progress'
}

const ACTION_TITLES: Partial<Record<ProjectStatus, string>> = {
  DRAFT: '完善项目设定',
  PROPOSAL_READY: '选择并确认故事方向',
  RELATIONSHIP_READY: '确认角色关系与故事结构',
  CHARACTER_VISUAL_READY: '生成并锁定角色形象',
  SCRIPT_READY: '审查并批准本集剧本',
  PREPRODUCTION_READY: '检查并批准前期资产',
  STORYBOARD_READY: '审查并批准动态分镜',
  CHARACTER_LOCKED: '继续镜头制作',
  PRODUCING: '查看正在制作的镜头',
  PREVIEW_READY: '审查真实小样',
  APPROVED: '进入正式制作',
  EXPORTING: '查看导出进度',
  EXPORTED: '查看已完成的交付文件',
}

const RECOMMENDED_NODE_TYPES: Partial<Record<ProjectStatus, string>> = {
  DRAFT: 'Project',
  PROPOSAL_READY: 'Story',
  RELATIONSHIP_READY: 'Story',
  CHARACTER_VISUAL_READY: 'Character',
  SCRIPT_READY: 'Script',
  PREPRODUCTION_READY: 'Character',
  STORYBOARD_READY: 'Storyboard',
  CHARACTER_LOCKED: 'Shot',
  PRODUCING: 'Shot',
  PREVIEW_READY: 'Timeline',
  APPROVED: 'Timeline',
}

function lastCompletedStage(readiness: ProjectReadiness, activeIndex: number) {
  return readiness.stages
    .slice(0, Math.max(0, activeIndex))
    .reverse()
    .find((stage) => stage.status === 'COMPLETE') ?? null
}

export function getFilmCanvasGuidance(
  readiness: ProjectReadiness,
): FilmCanvasGuidance {
  const activeIndex = readiness.stages.findIndex(
    (stage) => stage.key === readiness.activeStageKey,
  )
  const activeStage = readiness.stages[activeIndex] ?? readiness.stages[0]
  const completedStage = lastCompletedStage(readiness, activeIndex)
  const nextStage = activeIndex >= 0 ? readiness.stages[activeIndex + 1] ?? null : null
  const blocker = readiness.blockers[0]

  if (blocker) {
    return {
      actionHref: blocker.actionHref,
      actionLabel: blocker.actionLabel,
      activeStageLabel: activeStage?.label ?? '当前阶段',
      benefit: nextStage
        ? `解决后可继续推进「${activeStage?.label ?? '当前阶段'}」，再解锁「${nextStage.label}」。`
        : '解决这个问题后即可继续推进项目。',
      completedStageLabel: completedStage?.label ?? null,
      description: blocker.message,
      nextStageLabel: nextStage?.label ?? null,
      recommendedNodeType: RECOMMENDED_NODE_TYPES[readiness.projectStatus] ?? null,
      title: '先解决当前阻塞',
      tone: 'blocked',
    }
  }

  const progress = readiness.summaryStatus === 'IN_PROGRESS'
  const title = progress
    ? `等待「${activeStage?.label ?? '当前阶段'}」处理完成`
    : ACTION_TITLES[readiness.projectStatus] ?? readiness.nextActionLabel
  return {
    actionHref: readiness.nextActionHref,
    actionLabel: progress ? readiness.nextActionLabel : title,
    activeStageLabel: activeStage?.label ?? '当前阶段',
    benefit: nextStage
      ? `完成后将解锁「${nextStage.label}」：${nextStage.detail}`
      : '完成这一步后，项目将进入下一项可执行工作。',
    completedStageLabel: completedStage?.label ?? null,
    description: progress
      ? `系统正在处理「${activeStage?.label ?? '当前阶段'}」，你可以查看任务进度，不需要重复发起。`
      : `当前项目停在「${activeStage?.label ?? '当前阶段'}」。完成这一步，系统才会继续推进。`,
    nextStageLabel: nextStage?.label ?? null,
    recommendedNodeType: RECOMMENDED_NODE_TYPES[readiness.projectStatus] ?? null,
    title,
    tone: progress ? 'progress' : 'action',
  }
}
