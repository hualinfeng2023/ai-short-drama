import { describe, expect, it } from 'vitest'

import type { DirectorReviewProposal } from '../../api/client'
import {
  directorApprovalOverrideSuggestion,
  directorConfidenceLabel,
  type DirectorReviewAction,
} from './DirectorReviewCard'

type DirectorDecisionAction = Extract<DirectorReviewAction, { type: 'DECIDE' }>

function approvalAction(
  risk: 'DURATION_BUDGET_EXCEEDED' | 'DOWNSTREAM_TIMING_SHIFT',
): DirectorDecisionAction {
  const proposal = {
    comparison: {
      timelinePreview: {
        validationStatus: 'REVIEW_REQUIRED',
        risk,
        after: { overflowMs: 1500 },
        downstreamShiftMs: -800,
      },
    },
  } as DirectorReviewProposal
  return { type: 'DECIDE', proposal, decision: 'APPROVE' }
}

describe('directorApprovalOverrideSuggestion', () => {
  it('prefills the measured duration overflow and the follow-up review plan', () => {
    expect(
      directorApprovalOverrideSuggestion(approvalAction('DURATION_BUDGET_EXCEEDED')),
    ).toBe(
      '接受修改后超出场景预算 1.5 秒 的风险；批准后将在分镜阶段调整停顿并复核总时长。',
    )
  })

  it('prefills the downstream shift direction and review plan', () => {
    expect(
      directorApprovalOverrideSuggestion(approvalAction('DOWNSTREAM_TIMING_SHIFT')),
    ).toBe(
      '接受后续时间线提前 0.8 秒 的风险；批准后将在分镜阶段复核相邻场景衔接。',
    )
  })

  it('does not prefill actions that do not require an approval override', () => {
    const action = approvalAction('DURATION_BUDGET_EXCEEDED')
    expect(directorApprovalOverrideSuggestion({ ...action, decision: 'REJECT' })).toBe('')
  })
})

describe('directorConfidenceLabel', () => {
  it('turns internal confidence scores into decision-friendly labels', () => {
    expect(directorConfidenceLabel(0.92)).toBe('高置信度')
    expect(directorConfidenceLabel(0.72)).toBe('中等置信度')
    expect(directorConfidenceLabel(0.48)).toBe('低置信度')
  })
})
