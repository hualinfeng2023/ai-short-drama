import type { Job, JobRecoveryAction } from '../types'
import { localizeDisplayText } from './localizeDisplayText'

const RECOVERY_ACTIONS = new Set<JobRecoveryAction>([
  'RESUME_FROM_FAILURE',
  'RETRY_FAILED_PARTS',
  'SWITCH_MODEL',
  'FALLBACK_EXECUTION',
  'SAVE_INTERMEDIATE',
  'PROVIDE_INPUT',
  'ESCALATE_HUMAN',
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    : []
}

function recoveryActionList(value: unknown): JobRecoveryAction[] {
  return stringList(value).filter((item): item is JobRecoveryAction => (
    RECOVERY_ACTIONS.has(item as JobRecoveryAction)
  ))
}

export interface JobRecoveryPlan {
  completionState: 'NOT_COMPLETED' | 'PARTIAL' | 'RECOVERED' | 'DEGRADED_SUCCEEDED'
  completedPercent: number
  failedStep: string
  completedSteps: string[]
  failedParts: string[]
  intermediateResultSaved: boolean
  intermediateResultKeys: string[]
  availableActions: JobRecoveryAction[]
  unreliableOutputs: string[]
  reliabilityNote: string
  handoffStatus: 'NOT_REQUESTED' | 'REQUESTED'
}

export function getJobRecoveryPlan(job: Job): JobRecoveryPlan | null {
  const rawRecovery = isRecord(job.errorDetails?.recovery)
    ? job.errorDetails.recovery
    : {}
  const degradedSuccess = rawRecovery.completion_state === 'DEGRADED_SUCCEEDED'
  if (job.status !== 'FAILED' && job.status !== 'CANCELLED' && !degradedSuccess) {
    return null
  }

  const completedPercent = typeof rawRecovery.completed_percent === 'number'
    ? Math.round(rawRecovery.completed_percent)
    : Math.round(job.progress)
  const failedParts = stringList(rawRecovery.failed_parts)
  if (failedParts.length === 0 && job.entityType === 'shot' && job.entityId) {
    failedParts.push(job.entityId)
  }
  const availableActions = recoveryActionList(rawRecovery.available_actions)
  if (availableActions.length === 0 && !degradedSuccess) {
    if (job.retryable) availableActions.push('RESUME_FROM_FAILURE')
    if (failedParts.length > 0) availableActions.push('RETRY_FAILED_PARTS')
    availableActions.push(
      'SWITCH_MODEL',
      'FALLBACK_EXECUTION',
      'SAVE_INTERMEDIATE',
      'PROVIDE_INPUT',
      'ESCALATE_HUMAN',
    )
  }
  const failedStep = typeof rawRecovery.failed_step === 'string' && rawRecovery.failed_step
    ? localizeDisplayText(rawRecovery.failed_step)
    : localizeDisplayText(job.stage || '当前处理步骤')
  const unreliableOutputs = stringList(rawRecovery.unreliable_outputs)
  if (unreliableOutputs.length === 0 && !degradedSuccess) {
    unreliableOutputs.push(`${failedStep}及其后续结果尚未完成验证`)
  }

  return {
    completionState: degradedSuccess
      ? 'DEGRADED_SUCCEEDED'
      : rawRecovery.completion_state === 'RECOVERED'
        ? 'RECOVERED'
        : completedPercent > 0
          ? 'PARTIAL'
          : 'NOT_COMPLETED',
    completedPercent,
    failedStep,
    completedSteps: stringList(rawRecovery.completed_steps).map(localizeDisplayText),
    failedParts,
    intermediateResultSaved: rawRecovery.intermediate_result_saved === true,
    intermediateResultKeys: stringList(rawRecovery.intermediate_result_keys),
    availableActions,
    unreliableOutputs: unreliableOutputs.map(localizeDisplayText),
    reliabilityNote: typeof rawRecovery.reliability_note === 'string'
      ? localizeDisplayText(rawRecovery.reliability_note)
      : degradedSuccess
        ? '任务通过降级方案完成；继续使用前需要人工复核。'
        : '已完成步骤可以保留，但失败步骤及其下游结果不能视为最终可信结果。',
    handoffStatus: rawRecovery.handoff_status === 'REQUESTED'
      ? 'REQUESTED'
      : 'NOT_REQUESTED',
  }
}
