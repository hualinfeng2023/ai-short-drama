import {
  ApiError,
  fetchDirectorReviewProposals,
  type DirectorReviewIssueType,
  type DirectorReviewProposal,
} from './client'

export interface DirectorGenerationRecord {
  generationRecordId: string
  scriptSceneId: string
  scriptVersionId?: string
  status: 'SUCCEEDED' | 'FAILED'
  provider: string
  model: string
  providerRequestId?: string
  latencyMs?: number
  estimatedCostUsd?: number
  attemptCount: number
  repairAttempts: number
  failureStage?: string
  errorCode?: string
  errorMessage?: string
  retryable: boolean
  retryOfGenerationRecordId?: string
  proposalId?: string
  proposalStatus?: string
  issueType?: string
  createdAt: string
  completedAt?: string
}

export type DirectorGenerationFailure = DirectorGenerationRecord & { status: 'FAILED' }

interface RetryDirectorGenerationInput {
  expectedVersion: number
  targetType?: 'SCRIPT_SCENE' | 'SCENE'
  targetId: string
  issueTypes: DirectorReviewIssueType[]
  instruction?: string
}

async function responsePayload(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function dataFromPayload<T>(response: Response, payload: unknown): T {
  if (!response.ok) throw new ApiError(response.status, payload, response.statusText)
  if (
    typeof payload !== 'object'
    || payload === null
    || !('data' in payload)
  ) {
    throw new ApiError(
      502,
      { detail: '后端返回了无法识别的 Director 响应' },
      'Invalid API response',
    )
  }
  return (payload as { data: T }).data
}

export async function fetchDirectorGenerationFailures(
  projectId: string,
  scriptSceneId: string,
  signal?: AbortSignal,
): Promise<DirectorGenerationFailure[]> {
  const query = new URLSearchParams({ script_scene_id: scriptSceneId })
  const response = await fetch(
    `/api/v1/projects/${projectId}/director-generation-failures?${query}`,
    { headers: { Accept: 'application/json' }, signal },
  )
  const payload = await responsePayload(response)
  const records = dataFromPayload<Array<Record<string, unknown>>>(response, payload)
  return records.map((record) => mapDirectorGenerationRecord(record) as DirectorGenerationFailure)
}

function mapDirectorGenerationRecord(
  record: Record<string, unknown>,
): DirectorGenerationRecord {
  return {
    generationRecordId: String(record.generation_record_id),
    scriptSceneId: String(record.script_scene_id),
    ...(typeof record.script_version_id === 'string'
      ? { scriptVersionId: record.script_version_id }
      : {}),
    status: record.status === 'SUCCEEDED' ? 'SUCCEEDED' : 'FAILED',
    provider: String(record.provider),
    model: String(record.model),
    ...(typeof record.provider_request_id === 'string'
      ? { providerRequestId: record.provider_request_id }
      : {}),
    ...(typeof record.latency_ms === 'number' ? { latencyMs: record.latency_ms } : {}),
    ...(typeof record.estimated_cost_usd === 'number'
      ? { estimatedCostUsd: record.estimated_cost_usd }
      : {}),
    attemptCount: Number(record.attempt_count ?? 0),
    repairAttempts: Number(record.repair_attempts ?? 0),
    ...(typeof record.failure_stage === 'string'
      ? { failureStage: record.failure_stage }
      : {}),
    ...(typeof record.error_code === 'string' ? { errorCode: record.error_code } : {}),
    ...(typeof record.error_message === 'string'
      ? { errorMessage: record.error_message }
      : {}),
    retryable: Boolean(record.retryable),
    ...(typeof record.retry_of_generation_record_id === 'string'
      ? { retryOfGenerationRecordId: record.retry_of_generation_record_id }
      : {}),
    ...(typeof record.proposal_id === 'string' ? { proposalId: record.proposal_id } : {}),
    ...(typeof record.proposal_status === 'string'
      ? { proposalStatus: record.proposal_status }
      : {}),
    ...(typeof record.issue_type === 'string' ? { issueType: record.issue_type } : {}),
    createdAt: String(record.created_at),
    ...(typeof record.completed_at === 'string'
      ? { completedAt: record.completed_at }
      : {}),
  }
}

export async function fetchDirectorGenerationHistory(
  projectId: string,
  scriptSceneId: string,
  signal?: AbortSignal,
): Promise<DirectorGenerationRecord[]> {
  const query = new URLSearchParams({ script_scene_id: scriptSceneId })
  const response = await fetch(
    `/api/v1/projects/${projectId}/director-generation-history?${query}`,
    { headers: { Accept: 'application/json' }, signal },
  )
  const payload = await responsePayload(response)
  const records = dataFromPayload<Array<Record<string, unknown>>>(response, payload)
  return records.map(mapDirectorGenerationRecord)
}

export async function retryDirectorGeneration(
  projectId: string,
  failure: DirectorGenerationFailure,
  input: RetryDirectorGenerationInput,
): Promise<DirectorReviewProposal> {
  const response = await fetch(`/api/v1/projects/${projectId}/director-review-proposals`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({
      expected_version: input.expectedVersion,
      target_type: input.targetType ?? 'SCRIPT_SCENE',
      target_id: input.targetId,
      issue_types: input.issueTypes,
      instruction: input.instruction,
      retry_of_generation_record_id: failure.generationRecordId,
    }),
  })
  const payload = await responsePayload(response)
  const created = dataFromPayload<{ proposal_id: string }>(response, payload)
  const proposals = await fetchDirectorReviewProposals(projectId)
  const proposal = proposals.find((item) => item.proposalId === created.proposal_id)
  if (!proposal) {
    throw new ApiError(
      502,
      { detail: 'Director 重试已完成，但返回列表中缺少对应 Proposal' },
      'Director proposal missing',
    )
  }
  return proposal
}
