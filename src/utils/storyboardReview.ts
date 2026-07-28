import type {
  ShotValidationIssue,
  StructuredShotSpec,
  StoryboardWorkspace,
} from '../api/client'

type StoryboardShot = StoryboardWorkspace['shots'][number]

export type StoryboardReviewPhase = 'BLOCKED' | 'WARNINGS' | 'READY' | 'APPROVED'
export type ShotFieldReviewStatus = 'DRAFT' | 'BLOCKED' | 'QC_REVIEW_REQUIRED' | 'QC_PASSED'

export function getStoryboardReviewSummary(
  shots: StoryboardShot[],
  approved: boolean,
) {
  const blockerCount = shots.reduce(
    (total, shot) => total + shot.validationReport.issues.filter(
      (issue) => issue.severity === 'BLOCKER',
    ).length,
    0,
  )
  const warningCount = shots.reduce(
    (total, shot) => total + shot.validationReport.issues.filter(
      (issue) => issue.severity === 'WARNING',
    ).length,
    0,
  )
  const affectedShotCount = shots.filter(
    (shot) => shot.validationReport.issues.length > 0,
  ).length

  const phase: StoryboardReviewPhase = approved
    ? 'APPROVED'
    : blockerCount > 0
      ? 'BLOCKED'
      : warningCount > 0
        ? 'WARNINGS'
        : 'READY'

  return { affectedShotCount, blockerCount, phase, warningCount }
}

function readText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

export function getShotIssuePresentation(
  issue: ShotValidationIssue,
  spec: StructuredShotSpec,
) {
  if (issue.field_path.includes('visual_content.action')) {
    return {
      fieldLabel: '画面动作',
      guidance: `当前画面动作在 ${spec.duration_sec} 秒内包含过多连续变化，难以稳定呈现。建议保留一个主要动作，或适当延长镜头。`,
    }
  }
  if (issue.field_path.includes('end_state.action_state')) {
    return {
      fieldLabel: '结尾状态',
      guidance: '镜头发生了动作，但结尾状态没有说明变化结果。请补充动作结束后人物或物体所处的状态。',
    }
  }
  if (issue.field_path.includes('duration_sec')) {
    return {
      fieldLabel: '镜头时长',
      guidance: `当前镜头时长为 ${spec.duration_sec} 秒。请调整时长，使画面动作能完整、稳定地呈现。`,
    }
  }
  if (issue.field_path.includes('narrative_goal')) {
    return {
      fieldLabel: '叙事目标',
      guidance: '请明确这个镜头需要让观众理解、感受或期待什么。',
    }
  }
  return {
    fieldLabel: '镜头规范',
    guidance: issue.message,
  }
}

export function getShotFieldReviewStatus(
  issues: ShotValidationIssue[],
  fieldPath: string,
  changedSinceValidation = false,
  fieldLabel = '当前字段',
): {
  description: string
  label: string
  status: ShotFieldReviewStatus
} {
  if (changedSinceValidation) {
    return {
      description: `${fieldLabel}已修改，请保存并重新验证后确认结果。`,
      label: '待重新检查',
      status: 'DRAFT',
    }
  }

  const fieldIssues = issues.filter((issue) => issue.field_path.includes(fieldPath))
  if (fieldIssues.some((issue) => issue.severity === 'BLOCKER')) {
    return {
      description: `${fieldLabel}存在阻断问题，需要修复后才能通过检查。`,
      label: '需修复',
      status: 'BLOCKED',
    }
  }
  if (fieldIssues.some((issue) => issue.severity === 'WARNING')) {
    return {
      description: `${fieldLabel}可以继续审核，但建议根据提示调整。`,
      label: '建议修改',
      status: 'QC_REVIEW_REQUIRED',
    }
  }
  return {
    description: `${fieldLabel}已通过当前镜头检查。`,
    label: '已通过',
    status: 'QC_PASSED',
  }
}

export function getEndActionState(spec: StructuredShotSpec): string {
  return readText(spec.end_state.action_state)
}
