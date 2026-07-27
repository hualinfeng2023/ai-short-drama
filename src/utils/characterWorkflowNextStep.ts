export interface CharacterWorkflowNextStep {
  title: string
  description: string
  actionLabel: string
  destination: 'CHARACTER' | 'TASKS' | 'STORY'
}

const STORY_REVIEW_OR_LATER_STATUSES = new Set([
  'SCRIPT_READY',
  'STORY_APPROVED',
  'PREPRODUCTION_READY',
  'PREPRODUCTION_APPROVED',
  'STORYBOARD_READY',
  'STORYBOARD_APPROVED',
  'CHARACTER_LOCKED',
  'PRODUCING',
  'PREVIEW_READY',
  'APPROVED',
  'EXPORTING',
  'EXPORTED',
])

export function resolveCharacterWorkflowNextStep(input: {
  projectStatus: string
  lockedCount: number
  totalCount: number
  nextCharacterName?: string
}): CharacterWorkflowNextStep {
  const remainingCount = Math.max(0, input.totalCount - input.lockedCount)
  if (remainingCount > 0) {
    return {
      title: `还需确认 ${remainingCount} 位角色形象`,
      description: '逐一完成基准检查并锁定；全部锁定后，系统会自动生成分集大纲和剧本。',
      actionLabel: input.nextCharacterName
        ? `前往确认${input.nextCharacterName}`
        : '继续确认角色',
      destination: 'CHARACTER',
    }
  }
  if (STORY_REVIEW_OR_LATER_STATUSES.has(input.projectStatus)) {
    return {
      title: '下一步：审核故事与剧本',
      description: '确认分集大纲和剧本后，再进入前期资产锁定与动态分镜。',
      actionLabel: '前往剧本审核',
      destination: 'STORY',
    }
  }
  return {
    title: '角色形象已完成，正在生成剧本',
    description: '系统正基于已批准的关系和锁定身份生成分集大纲与剧本。',
    actionLabel: '查看生成进度',
    destination: 'TASKS',
  }
}
