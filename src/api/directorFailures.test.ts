import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import {
  fetchDirectorGenerationFailures,
  retryDirectorGeneration,
  type DirectorGenerationFailure,
} from './directorFailures'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Director generation failure API', () => {
  it('maps a persisted failure record for the Inspector', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: [{
        generation_record_id: '11111111-1111-4111-8111-111111111111',
        script_scene_id: '22222222-2222-4222-8222-222222222222',
        status: 'FAILED',
        provider: 'volcengine-ark',
        model: 'director-model',
        provider_request_id: 'request-3',
        latency_ms: 812,
        attempt_count: 3,
        repair_attempts: 2,
        failure_stage: 'PROVIDER_VALIDATION',
        error_code: 'ARK_TEXT_SCHEMA_INVALID',
        error_message: '连续三次未返回有效结构',
        retryable: true,
        retry_of_generation_record_id: null,
        created_at: '2026-07-27T00:00:00Z',
      }],
      trace_id: 'trace-failure',
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })))

    const result = await fetchDirectorGenerationFailures(
      '33333333-3333-4333-8333-333333333333',
      '22222222-2222-4222-8222-222222222222',
    )

    expect(result).toEqual([{
      generationRecordId: '11111111-1111-4111-8111-111111111111',
      scriptSceneId: '22222222-2222-4222-8222-222222222222',
      status: 'FAILED',
      provider: 'volcengine-ark',
      model: 'director-model',
      providerRequestId: 'request-3',
      latencyMs: 812,
      attemptCount: 3,
      repairAttempts: 2,
      failureStage: 'PROVIDER_VALIDATION',
      errorCode: 'ARK_TEXT_SCHEMA_INVALID',
      errorMessage: '连续三次未返回有效结构',
      retryable: true,
      createdAt: '2026-07-27T00:00:00Z',
    }])
  })

  it('sends retry lineage with a fresh idempotency key', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: {
        code: 'VERSION_CONFLICT',
        message: '项目版本已经变化',
      },
      trace_id: 'trace-retry',
    }), {
      status: 409,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('crypto', { randomUUID: () => 'retry-idempotency-key' })
    const failure: DirectorGenerationFailure = {
      generationRecordId: '11111111-1111-4111-8111-111111111111',
      scriptSceneId: '22222222-2222-4222-8222-222222222222',
      status: 'FAILED',
      provider: 'volcengine-ark',
      model: 'director-model',
      attemptCount: 3,
      repairAttempts: 2,
      retryable: true,
      createdAt: '2026-07-27T00:00:00Z',
    }

    await expect(retryDirectorGeneration(
      '33333333-3333-4333-8333-333333333333',
      failure,
      {
        expectedVersion: 8,
        targetId: failure.scriptSceneId,
        issueTypes: ['PACING'],
      },
    )).rejects.toBeInstanceOf(ApiError)

    const request = fetchMock.mock.calls[0]![1] as RequestInit
    expect(request.headers).toMatchObject({
      'Idempotency-Key': 'retry-idempotency-key',
    })
    expect(JSON.parse(String(request.body))).toMatchObject({
      expected_version: 8,
      target_id: failure.scriptSceneId,
      retry_of_generation_record_id: failure.generationRecordId,
    })
  })
})
