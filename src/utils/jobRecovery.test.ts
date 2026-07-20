import { describe, expect, it } from 'vitest'

import type { Job } from '../types'
import { getJobRecoveryPlan } from './jobRecovery'

const failedJob = {
  status: 'FAILED',
  progress: 72,
  retryable: true,
  stage: '生成主镜头',
  entityType: 'shot',
  entityId: 'shot-05',
} as Job

describe('getJobRecoveryPlan', () => {
  it('builds a safe fallback plan for legacy failed jobs', () => {
    expect(getJobRecoveryPlan(failedJob)).toMatchObject({
      completionState: 'PARTIAL',
      completedPercent: 72,
      failedStep: '生成主镜头',
      failedParts: ['shot-05'],
      availableActions: [
        'RESUME_FROM_FAILURE',
        'RETRY_FAILED_PARTS',
        'SWITCH_MODEL',
        'FALLBACK_EXECUTION',
        'SAVE_INTERMEDIATE',
        'PROVIDE_INPUT',
      ],
    })
  })

  it('uses the persisted recovery contract and reliability disclosure', () => {
    const plan = getJobRecoveryPlan({
      ...failedJob,
      errorDetails: {
        recovery: {
          completion_state: 'PARTIAL',
          completed_percent: 64,
          failed_step: '视频生成',
          completed_steps: ['脚本解析', '关键帧生成'],
          failed_parts: ['shot-03'],
          intermediate_result_saved: true,
          intermediate_result_keys: ['completed_shot_ids'],
          available_actions: ['RETRY_FAILED_PARTS'],
          unreliable_outputs: ['shot-03 之后的时间线'],
          reliability_note: '已完成镜头可信，失败镜头及其下游结果不可信。',
        },
      },
    })

    expect(plan).toMatchObject({
      completedPercent: 64,
      completedSteps: ['脚本解析', '关键帧生成'],
      failedParts: ['shot-03'],
      intermediateResultSaved: true,
      availableActions: ['RETRY_FAILED_PARTS'],
      unreliableOutputs: ['镜头-03 之后的时间线'],
    })
  })

  it('keeps degraded success visible without exposing retry actions', () => {
    expect(getJobRecoveryPlan({
      ...failedJob,
      status: 'SUCCEEDED',
      progress: 100,
      errorDetails: {
        recovery: {
          completion_state: 'DEGRADED_SUCCEEDED',
          reliability_note: '请人工复核。',
          unreliable_outputs: ['口型同步'],
        },
      },
    })).toMatchObject({
      completionState: 'DEGRADED_SUCCEEDED',
      availableActions: [],
      unreliableOutputs: ['口型同步'],
    })
  })
})
