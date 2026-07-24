import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  analyzeRelationshipRevisionImpact,
  createRelationshipGraphRevision,
  createProjectDraft,
  deleteCharacterVisualCandidate,
  deleteProjectRecord,
  enhanceShotPrompt,
  fetchJobs,
  fetchProviderSettings,
  fetchProjectReadiness,
  fetchRelationshipGraphDiff,
  fetchStoryWorkspace,
  fetchBriefVersions,
  fetchDirectorProposals,
  fetchDirectorReviewProposals,
  generateShotTake,
  mapWorkspace,
  recoverPersistedJob,
  reviewPersistedCandidateIdentity,
  rewriteBriefStory,
  saveProviderSettings,
  saveRelationshipGraph,
  suggestBriefRequirements,
  suggestBriefAvoidances,
  suggestBriefBlockingQuestions,
  suggestProjectName,
  updateProjectDraft,
  type ApiWorkspace,
  type RelationshipGraphPayloadRecord,
} from './client'

const apiProviderSettings = {
  storage: { scope: 'server_data', updated_at: '2026-07-15T08:00:00Z', secrets_returned: false },
  ark: {
    api_key_configured: true,
    api_key_hint: '••••5678',
    api_key_source: 'saved',
    responses_url: 'https://ark.example.com/api/v3/responses',
    prompt_model: 'text-v2',
    images_url: 'https://ark.example.com/api/v3/images/generations',
    image_model: 'image-v5',
    video_tasks_url: 'https://ark.example.com/api/v3/contents/generations/tasks',
    video_model: 'video-v1',
    request_timeout_seconds: 120,
    video_poll_interval_seconds: 4,
    video_timeout_seconds: 600,
    source_url_fast_path_seconds: 480,
    identity_qc_enabled: true,
    identity_auto_pass_threshold: 0.9,
  },
  tos: {
    enabled: false,
    access_key_configured: false,
    access_key_hint: null,
    access_key_source: 'default',
    secret_key_configured: false,
    secret_key_hint: null,
    security_token_configured: false,
    endpoint: 'tos-cn-beijing.volces.com',
    region: 'cn-beijing',
    bucket: '',
    presign_ttl_seconds: 7200,
    object_prefix: 'ai-short-drama/media-staging',
    object_expires_days: 1,
    cleanup_on_completion: true,
  },
} as const

const apiProject = {
  id: '11111111-1111-4111-8111-111111111112',
  name: '雨停以后',
  idea: '暴雨停电夜，陌生人被困在便利店，各自藏着同一个秘密。',
  genre: 'urban_suspense',
  style: 'realistic_cinematic',
  target_duration_sec: 60,
  aspect_ratio: '9:16',
  target_platform: 'douyin',
  status: 'DRAFT',
  lock_version: 1,
  available_points: 50000,
  timeline_version: 0,
  preview_approved: false,
  export_ready: false,
  created_at: '2026-07-13T12:00:00Z',
  updated_at: '2026-07-13T12:00:00Z',
} as const

const apiRelationshipGraph = {
  id: '22222222-2222-4222-8222-222222222222',
  project_id: apiProject.id,
  story_bible_version_id: '33333333-3333-4333-8333-333333333333',
  version: 1,
  parent_version_id: null,
  status: 'DRAFT',
  schema_version: 'relationship-graph-v1',
  config_version: 'relationship-graph-v1',
  provider: 'ark',
  model: 'text-v2',
  content_hash: 'relationship-hash',
  lock_version: 1,
  project_lock_version: 2,
  approved_at: null,
  approved_by: null,
  created_at: '2026-07-16T00:00:00Z',
  graph: {
    schema_version: 'relationship-graph-v1',
    edges: [{
      relationship_key: 'lead-rival',
      source_character_key: 'lead',
      target_character_key: 'rival',
      directionality: 'BIDIRECTIONAL',
      relationship_types: ['RIVAL'],
      surface_relationship: '互相怀疑',
      true_relationship: '共享旧案秘密',
      source_view: { perceived_relationship: '对手', belief: '对方隐瞒证据' },
      target_view: { perceived_relationship: '嫌疑人', belief: '对方操控现场' },
      trust_level: -2,
      emotional_temperature: -1,
      power_balance: 0,
      conflict_intensity: 3,
      story_function: '制造误判并推动认证',
      secret: null,
      is_core: true,
      locked: false,
      ordinal: 1,
    }],
    beats: [],
    core_relationship_keys: ['lead-rival'],
    generation_notes: [],
  },
  validation_issues: [],
  editability: {
    semantic_editable: true,
    layout_editable: true,
    can_submit: true,
    can_approve: true,
    can_create_revision: false,
    active_job: false,
    reason_code: null,
    reason_message: null,
    requires_impact_confirmation: false,
  },
} as const

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('mapWorkspace', () => {
  it('maps the snake_case read API into the existing frontend state contract', () => {
    const workspace: ApiWorkspace = {
      project: {
        id: 'project-id', name: '项目', idea: '故事', genre: '都市', style: '电影感',
        target_duration_sec: 60, aspect_ratio: '9:16', target_platform: 'douyin',
        status: 'PRODUCING', lock_version: 1,
        available_points: 100, timeline_version: 2, preview_approved: false,
        export_ready: false, created_at: '2026-07-13T11:00:00Z',
        updated_at: '2026-07-13T12:00:00Z',
      },
      episode: { id: 'episode-id' },
      scenes: [{
        id: 'scene-id', code: '01', title: '开场', purpose: '建立冲突',
        duration_sec: 8, status: 'READY',
      }],
      shots: [{
        id: 'shot-id', scene_id: 'scene-id', code: 'S01', ordinal: 1, title: '镜头',
        description: '动作', dialogue: '', duration_sec: 8, status: 'READY',
        shot_size: 'MS', camera_movement: 'STATIC', current_take: 1,
        candidate_take: null, continuity: 'CLEAR', location: '室内', time_of_day: '日',
      }],
      jobs: [{
        id: 'job-id', project_id: 'project-id', project_name: '项目', job_type: 'DEMO_RENDER',
        entity_type: 'shot', entity_id: 'shot-id', label: '任务', entity: 'shot-id',
        status: 'SUCCEEDED', progress: 100, stage: '完成', attempt: 1, max_attempts: 3,
        available_at: '2026-07-13T12:00:00Z', heartbeat_at: null,
        created_at: '2026-07-13T12:00:00Z', updated_at: '2026-07-13T12:01:00Z',
        completed_at: '2026-07-13T12:01:00Z', estimated_seconds: null,
        retryable: false, error_code: null, error_message: null,
      }],
    }

    const result = mapWorkspace(workspace)
    expect(result.project.episodeId).toBe('episode-id')
    expect(result.project.scenes[0].shotIds).toEqual(['shot-id'])
    expect(result.project.shots[0]).toMatchObject({ sceneId: 'scene-id', durationSec: 8 })
    expect(result.project.shots[0]).not.toHaveProperty('candidateTake')
    expect(result.jobs[0]).not.toHaveProperty('estimatedSeconds')
  })
})

describe('global jobs client', () => {
  it('loads all jobs without silently scoping to the demo project', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: [{
        id: 'job-global', project_id: 'project-other', project_name: '另一个项目', job_type: 'DEMO_RENDER',
        entity_type: 'project', entity_id: 'project-other', label: '跨项目任务',
        entity: 'project:project-other', status: 'RUNNING', progress: 45,
        stage: '正在生成', attempt: 1, max_attempts: 3,
        available_at: '2026-07-17T00:00:00Z', heartbeat_at: '2026-07-17T00:00:01Z',
        created_at: '2026-07-17T00:00:00Z', updated_at: '2026-07-17T00:00:01Z',
        completed_at: null, estimated_seconds: 120, retryable: true,
        error_code: null, error_message: null,
      }],
      trace_id: 'trace-global-jobs',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const jobs = await fetchJobs()

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/jobs')
    expect(jobs[0]).toMatchObject({
      id: 'job-global',
      projectId: 'project-other',
      projectName: '另一个项目',
      status: 'RUNNING',
    })
  })

  it('repairs a missing legacy job entity instead of crashing task rendering', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: [{
        id: 'job-legacy', project_id: 'project-other', project_name: '旧项目',
        job_type: 'DEMO_RENDER', entity_type: 'shot', entity_id: 'shot-legacy',
        label: '旧任务', status: 'SUCCEEDED', progress: 100, stage: '完成',
        attempt: 1, max_attempts: 3, available_at: '2026-07-17T00:00:00Z',
        heartbeat_at: null, created_at: '2026-07-17T00:00:00Z',
        updated_at: '2026-07-17T00:01:00Z', completed_at: '2026-07-17T00:01:00Z',
        estimated_seconds: null, retryable: false, error_code: null, error_message: null,
      }],
      trace_id: 'trace-legacy-job',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const jobs = await fetchJobs()

    expect(jobs[0].entity).toBe('shot:shot-legacy')
  })

  it('sends a structured recovery action without losing partial progress', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: {
        id: 'job-recovery', project_id: 'project-id', job_type: 'DEMO_RENDER',
        entity_type: 'shot', entity_id: 'shot-03', label: '恢复镜头',
        entity: 'shot:shot-03', status: 'RETRY_WAIT', progress: 72,
        stage: '等待重试失败部分', attempt: 3, max_attempts: 4,
        available_at: '2026-07-17T00:00:00Z', heartbeat_at: null,
        created_at: '2026-07-17T00:00:00Z', updated_at: '2026-07-17T00:01:00Z',
        completed_at: null, estimated_seconds: 120, retryable: true,
        error_code: null, error_message: null,
      },
      trace_id: 'trace-recover-job',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const job = await recoverPersistedJob('job-recovery', {
      action: 'RETRY_FAILED_PARTS',
      failedPartIds: ['shot-03'],
    })

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/jobs/job-recovery/recovery')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      action: 'RETRY_FAILED_PARTS',
      failed_part_ids: ['shot-03'],
    })
    expect(job).toMatchObject({ status: 'RETRY_WAIT', progress: 72 })
  })
})

describe('project readiness client', () => {
  it('maps the canonical workflow and blockers', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: {
        project_id: 'project-id', workflow_mode: 'CLASSIC', project_status: 'PRODUCING',
        summary_status: 'ACTION_REQUIRED', active_stage_key: 'SHOTS', active_job_count: 0,
        stages: [{ key: 'SHOTS', label: '镜头制作', status: 'CURRENT', href: '/shots', detail: '逐镜头制作' }],
        blockers: [{ code: 'TEST', message: '需要处理', action_label: '查看', action_href: '/tasks' }],
        next_action_label: '继续镜头制作', next_action_href: '/shots', updated_at: '2026-07-17T00:00:00Z',
      },
      trace_id: 'trace-readiness',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const readiness = await fetchProjectReadiness('project-id')

    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/projects/project-id/readiness')
    expect(readiness).toMatchObject({ workflowMode: 'CLASSIC', activeStageKey: 'SHOTS' })
    expect(readiness.blockers[0]).toEqual({
      code: 'TEST', message: '需要处理', actionLabel: '查看', actionHref: '/tasks',
    })
  })
})

describe('job error details', () => {
  it('maps persisted relationship blockers for the task page', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: [{
        id: 'job-semantic', project_id: 'project-id', job_type: 'GENERATE_STORY_STRUCTURE',
        entity_type: 'proposal_version', entity_id: 'proposal-id', label: '生成关系网',
        entity: 'proposal_version:proposal-id', status: 'FAILED', progress: 70,
        stage: '任务失败', attempt: 1, max_attempts: 3,
        available_at: '2026-07-17T12:00:00Z', heartbeat_at: null,
        created_at: '2026-07-17T12:00:00Z', updated_at: '2026-07-17T12:01:00Z',
        completed_at: '2026-07-17T12:01:00Z', estimated_seconds: 60, retryable: true,
        error_code: 'RELATIONSHIP_GRAPH_SEMANTIC_INVALID',
        error_message: '1 条隐藏关系缺少揭示计划',
        error_details: {
          issues: [{
            severity: 'BLOCKER', code: 'HIDDEN_RELATIONSHIP_WITHOUT_REVEAL',
            message: '缺少揭示计划', relationship_key: 'lead-rival',
          }],
        },
      }],
      trace_id: 'trace-jobs',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    const [job] = await fetchJobs()

    expect(job.errorCode).toBe('RELATIONSHIP_GRAPH_SEMANTIC_INVALID')
    expect(job.errorDetails).toEqual({
      issues: [{
        severity: 'BLOCKER', code: 'HIDDEN_RELATIONSHIP_WITHOUT_REVEAL',
        message: '缺少揭示计划', relationship_key: 'lead-rival',
      }],
    })
  })
})

describe('legacy director proposal compatibility', () => {
  it('maps seeded scenes that predate purpose, shots, and assumptions fields', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: [{
        id: 'proposal-id',
        project_id: apiProject.id,
        version: 1,
        brief_version: 1,
        payload: {
          title: '她的第二人生',
          logline: '重新找回人生方向。',
          total_duration_sec: 60,
          scenes: [{ code: '01', title: '坠落', duration_sec: 18 }],
        },
        provider: 'mock',
        status: 'APPROVED',
      }],
      trace_id: 'trace-legacy-proposal',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    const [proposal] = await fetchDirectorProposals(apiProject.id)

    expect(proposal.directorStatement).toBe('')
    expect(proposal.assumptions).toEqual([])
    expect(proposal.scenes[0]).toMatchObject({ purpose: '', shots: [] })
  })
})

describe('director review proposal client', () => {
  it('maps structured options, impact protection, and low-cost comparison', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: [{
        proposal_id: '44444444-4444-4444-8444-444444444444',
        project_id: apiProject.id,
        issue_type: 'AI_DIALOGUE',
        observation: '对白在解释剧情。',
        rationale: '人物需要采取语言行动。',
        target_objects: [{
          type: 'ScriptScene',
          id: '55555555-5555-4555-8555-555555555555',
          version_id: '66666666-6666-4666-8666-666666666666',
        }],
        alternatives: [{
          option_id: 'dialogue-concise',
          title: '压缩解释性对白',
          rationale: '保留行动，删除填充。',
          proposed_change: {
            scope: 'LINE',
            entity_id: '77777777-7777-4777-8777-777777777777',
            changes: { text: '现在离开。' },
            before: { text: '其实我觉得，你现在应该离开。' },
          },
          estimated_time_seconds: 1,
          estimated_cost_usd: 0,
        }, {
          option_id: 'pacing',
          title: '收紧节奏',
          rationale: '缩短停顿。',
          proposed_change: {
            scope: 'LINE',
            entity_id: '77777777-7777-4777-8777-777777777777',
            changes: { pause_after_ms: 100 },
            before: { pause_after_ms: 300 },
          },
          estimated_time_seconds: 1,
          estimated_cost_usd: 0,
        }],
        recommended_option: 'dialogue-concise',
        confidence: 0.82,
        affected_objects: [{ type: 'Shot', id: 'shot-id', next_status: 'SUSPECT' }],
        preserved_objects: [{ type: 'Take', id: 'take-id', approval: 'APPROVED' }],
        estimated_time_seconds: 2,
        estimated_cost_usd: 0,
        requires_confirmation: true,
        validation_plan: ['比较人物目标'],
        base_script_version_id: '66666666-6666-4666-8666-666666666666',
        script_scene_id: '55555555-5555-4555-8555-555555555555',
        scene_ordinal: 1,
        provider: { provider: 'mock', model: 'director-v1', request_id: null },
        status: 'APPLIED_PENDING_APPROVAL',
        result_script_version_id: '88888888-8888-4888-8888-888888888888',
        rollback_script_version_id: null,
        comparison: {
          before: { text: '其实我觉得，你现在应该离开。' },
          after: { text: '现在离开。' },
          estimated_duration_before_ms: 3200,
          estimated_duration_after_ms: 1800,
          media_generation: false,
        },
        invalidated: [{ type: 'Shot', id: 'shot-id', next_status: 'SUSPECT' }],
        approval_result: null,
        created_at: '2026-07-25T00:00:00Z',
      }],
      trace_id: 'trace-director-review',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    const [proposal] = await fetchDirectorReviewProposals(apiProject.id)

    expect(proposal).toMatchObject({
      issueType: 'AI_DIALOGUE',
      recommendedOption: 'dialogue-concise',
      sceneOrdinal: 1,
      status: 'APPLIED_PENDING_APPROVAL',
      estimatedCostUsd: 0,
    })
    expect(proposal.alternatives[0].proposedChange.changes).toEqual({
      text: '现在离开。',
    })
    expect(proposal.comparison).toMatchObject({
      estimatedDurationBeforeMs: 3200,
      estimatedDurationAfterMs: 1800,
      mediaGeneration: false,
    })
    expect(proposal.preservedObjects[0].approval).toBe('APPROVED')
  })
})

describe('provider settings client', () => {
  it('maps masked provider settings without requiring secret values', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: apiProviderSettings,
      trace_id: 'trace-provider-read',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    const settings = await fetchProviderSettings()

    expect(settings.ark).toMatchObject({
      apiKeyConfigured: true,
      apiKeyHint: '••••5678',
      apiKeySource: 'saved',
      imageModel: 'image-v5',
    })
    expect(settings.storage.secretsReturned).toBe(false)
    expect(settings.ark).not.toHaveProperty('apiKey')
  })

  it('sends secret replacements and provider fields in the backend contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: apiProviderSettings,
      trace_id: 'trace-provider-save',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await saveProviderSettings({
      ark: {
        apiKey: 'replacement-secret',
        clearApiKey: false,
        responsesUrl: apiProviderSettings.ark.responses_url,
        promptModel: apiProviderSettings.ark.prompt_model,
        imagesUrl: apiProviderSettings.ark.images_url,
        imageModel: apiProviderSettings.ark.image_model,
        videoTasksUrl: apiProviderSettings.ark.video_tasks_url,
        videoModel: apiProviderSettings.ark.video_model,
        requestTimeoutSeconds: 120,
        videoPollIntervalSeconds: 4,
        videoTimeoutSeconds: 600,
        sourceUrlFastPathSeconds: 480,
        identityQcEnabled: true,
        identityAutoPassThreshold: 0.9,
      },
      tos: {
        enabled: false,
        clearAccessKey: false,
        clearSecretKey: false,
        clearSecurityToken: false,
        endpoint: apiProviderSettings.tos.endpoint,
        region: apiProviderSettings.tos.region,
        bucket: '',
        presignTtlSeconds: 7200,
        objectPrefix: apiProviderSettings.tos.object_prefix,
        objectExpiresDays: 1,
        cleanupOnCompletion: true,
      },
    })

    const request = fetchMock.mock.calls[0][1] as RequestInit
    const body = JSON.parse(String(request.body))
    expect(body.ark).toMatchObject({
      api_key: 'replacement-secret',
      clear_api_key: false,
      image_model: 'image-v5',
    })
    expect(body.tos).not.toHaveProperty('access_key')
  })
})

describe('project write client', () => {
  it('deletes a project through the project resource endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: { project_id: apiProject.id, deleted: true },
      trace_id: 'trace-delete',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await deleteProjectRecord(apiProject.id)

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/projects/${apiProject.id}`,
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('sends the idempotency key and maps a created Draft', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: { project: apiProject, brief_version: 1, idempotency_replayed: false },
      trace_id: 'trace-create',
    }), { status: 201, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await createProjectDraft({
      name: apiProject.name,
      idea: apiProject.idea,
      genre: apiProject.genre,
      style: apiProject.style,
      target_duration_sec: 60,
      aspect_ratio: '9:16',
      target_platform: 'douyin',
      reference_asset_ids: [],
      assumptions: [],
    }, 'create-rain-v1')

    expect(result.project).toMatchObject({ status: 'DRAFT', lockVersion: 1 })
    expect(result.briefVersion).toBe(1)
    const request = fetchMock.mock.calls[0][1] as RequestInit
    expect(request.headers).toMatchObject({ 'Idempotency-Key': 'create-rain-v1' })
  })

  it('preserves version conflict details as an ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: {
        code: 'VERSION_CONFLICT',
        message: '项目已被其他修改更新',
        user_action: '刷新最新版本后重新提交',
        retryable: false,
        details: { latest_version: 2 },
      },
      trace_id: 'trace-conflict',
    }), { status: 409, headers: { 'Content-Type': 'application/json' } })))

    const request = updateProjectDraft(apiProject.id, {
      expected_version: 1,
      name: '过期名字',
    })
    await expect(request).rejects.toBeInstanceOf(ApiError)
    await expect(request).rejects.toMatchObject({
      code: 'VERSION_CONFLICT',
      status: 409,
      traceId: 'trace-conflict',
    })
  })

  it('maps versioned multi-target Brief records', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: [{
        id: 'brief-id', project_id: apiProject.id, version: 2,
        project_name: apiProject.name, raw_input: apiProject.idea,
        genre: apiProject.genre, style: apiProject.style,
        target_duration_sec: 60, aspect_ratio: '9:16', target_platform: 'douyin',
        reference_asset_ids: [], assumptions: [],
        narrative_protagonist: 'female', target_audience: 'female_frequency',
        emotional_rewards: ['identity', 'career'], audience_profile: '25—40岁女性',
        production_format: 'live_action',
        primary_audience: 'urban_women_25_34',
        secondary_audiences: ['suspense_fans'], primary_market: 'CN',
        secondary_markets: ['SG'], canonical_language: 'zh-CN',
        localization_targets: ['en-SG'],
        platform_targets: [{
          platform: 'douyin', priority: 'PRIMARY', aspect_ratio: '9:16',
          target_duration_sec: 60, caption_mode: 'BOTH',
        }],
        content_requirements: ['前三秒出现危机'], content_avoidances: [],
        creative_defaults: { pace: 'fast' }, blocking_questions: [],
        payload_schema_version: 'brief-v3', content_hash: 'a'.repeat(64),
        status: 'DRAFT', created_at: '2026-07-14T12:00:00Z',
      }],
      trace_id: 'trace-brief',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    const briefs = await fetchBriefVersions(apiProject.id)
    expect(briefs[0]).toMatchObject({
      version: 2,
      narrativeProtagonist: 'female',
      targetAudience: 'female_frequency',
      emotionalRewards: ['identity', 'career'],
      audienceProfile: '25—40岁女性',
      productionFormat: 'live_action',
      primaryAudience: 'urban_women_25_34',
      secondaryMarkets: ['SG'],
      platformTargets: [{ platform: 'douyin', priority: 'PRIMARY' }],
      payloadSchemaVersion: 'brief-v3',
    })
  })
})

describe('character candidate write client', () => {
  it('deletes one historical candidate with optimistic version data', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: {
        character_id: 'character-id',
        candidate_id: 'candidate-id',
        deleted: true,
        lock_version: 8,
      },
      trace_id: 'trace-delete-candidate',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await deleteCharacterVisualCandidate(
      apiProject.id,
      'character-id',
      'candidate-id',
      7,
    )

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/projects/${apiProject.id}/characters/character-id/visual-candidates/candidate-id`,
      expect.objectContaining({ method: 'DELETE' }),
    )
    const request = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(request.body))).toEqual({
      expected_version: 7,
      actor: '创作者',
    })
  })
})

describe('identity review client', () => {
  it('sends a structured regenerate decision with the current lock version', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: {
        action: 'REGENERATE',
        shot: {},
        job: {
          id: 'job-review', project_id: 'project-id', job_type: 'GENERATE_SHOT_IMAGE',
          entity_type: 'shot', entity_id: 'shot-id', label: 'S01 · Take V4', entity: 'shot:shot-id',
          status: 'PENDING', progress: 0, stage: '等待生成', attempt: 0, max_attempts: 3,
          available_at: '2026-07-14T12:00:00Z', heartbeat_at: null,
          created_at: '2026-07-14T12:00:00Z', updated_at: '2026-07-14T12:00:00Z',
          completed_at: null, estimated_seconds: 2, retryable: true,
          error_code: null, error_message: null,
        },
      },
      trace_id: 'trace-review',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await reviewPersistedCandidateIdentity('shot-id', {
      decision: 'REGENERATE',
      issues: ['HAIR', 'WARDROBE'],
      note: '发型和外套需要调整',
      expectedVersion: 12,
    })

    expect(result).toMatchObject({ action: 'REGENERATE', job: { id: 'job-review' } })
    const request = fetchMock.mock.calls[0][1] as RequestInit
    expect(request.headers).toMatchObject({ 'Content-Type': 'application/json' })
    expect(JSON.parse(request.body as string)).toEqual({
      decision: 'REGENERATE',
      issues: ['HAIR', 'WARDROBE'],
      note: '发型和外套需要调整',
      expected_version: 12,
      actor: '创作者',
    })
  })
})

describe('image generation client', () => {
  it('sends the selected model, resolution, and aspect ratio', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: {
        id: 'job-image', project_id: 'project-id', job_type: 'GENERATE_SHOT_IMAGE',
        entity_type: 'shot', entity_id: 'shot-id', label: 'S01 · Take V3', entity: 'shot-id',
        status: 'PENDING', progress: 0, stage: '等待 Seedream 生成关键帧', attempt: 0,
        max_attempts: 3, available_at: '2026-07-14T00:00:00Z', heartbeat_at: null,
        created_at: '2026-07-14T00:00:00Z', updated_at: '2026-07-14T00:00:00Z',
        completed_at: null, estimated_seconds: 45, retryable: true,
        error_code: null, error_message: null,
      },
    }), { status: 202, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await generateShotTake('shot-id', {
      model: 'doubao-seedream-4-5-251128',
      resolution: '4K',
      aspectRatio: '21:9',
    }, 'shot-image-options-v1')

    const request = fetchMock.mock.calls[0][1] as RequestInit
    expect(request.headers).toMatchObject({ 'Idempotency-Key': 'shot-image-options-v1' })
    expect(JSON.parse(String(request.body))).toEqual({
      model: 'doubao-seedream-4-5-251128',
      resolution: '4K',
      aspect_ratio: '21:9',
    })
  })
})

describe('project naming client', () => {
  it('sends the current brief and maps the suggested name', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: {
        original: '粗略故事梗概',
        suggested: '双生神药',
        provider: 'volcengine-ark',
        model: 'doubao-seed-2-0-lite-260215',
        warning: null,
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await suggestProjectName('project-id', {
      current_name: '粗略故事梗概',
      idea: '一对姐妹同时得到两颗神药，她们必须做出选择。',
      genre: 'urban_drama',
      style: 'realistic_cinematic',
      narrative_protagonist: 'dual',
      target_audience: 'general',
      emotional_rewards: ['family'],
      audience_profile: '',
      production_format: 'live_action',
      primary_market: 'CN',
      canonical_language: 'zh-CN',
      target_duration_sec: 60,
      aspect_ratio: '9:16',
      target_platform: 'douyin',
      content_requirements: ['名称体现两姐妹与神药'],
      content_avoidances: ['不使用泛化逆袭标题'],
    })

    expect(result).toEqual({
      original: '粗略故事梗概',
      suggested: '双生神药',
      provider: 'volcengine-ark',
      model: 'doubao-seed-2-0-lite-260215',
      warning: undefined,
    })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/projects/project-id/name-suggestions')
    const request = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(request.body))).toMatchObject({
      current_name: '粗略故事梗概',
      primary_market: 'CN',
      target_duration_sec: 60,
      content_requirements: ['名称体现两姐妹与神药'],
    })
  })

  it('sends the brief context and maps requirement suggestions', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: {
        items: ['前三秒建立危机', '结尾保留反转钩子'],
        provider: 'local-fallback',
        model: 'brief-requirements-generator-v1',
        warning: 'ARK_API_KEY 未配置',
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await suggestBriefRequirements('project-id', {
      idea: '一对姐妹得到两颗神药，在末日中走向不同选择。',
      genre: 'urban_drama',
      style: 'realistic_cinematic',
      target_duration_sec: 60,
      aspect_ratio: '9:16',
      target_platform: 'douyin',
      narrative_protagonist: 'dual',
      target_audience: 'general',
      emotional_rewards: ['family'],
      audience_profile: '',
      production_format: 'live_action',
      primary_market: 'CN',
      canonical_language: 'zh-CN',
      existing_requirements: [],
      content_avoidances: [],
    })

    expect(result.items).toEqual(['前三秒建立危机', '结尾保留反转钩子'])
    expect(result.warning).toBe('ARK_API_KEY 未配置')
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/v1/projects/project-id/brief-requirement-suggestions',
    )
  })

  it('sends the brief context and maps avoidance suggestions', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: {
        items: ['避免人物设定前后矛盾', '避免未授权素材露出'],
        provider: 'local-fallback',
        model: 'brief-avoidances-generator-v1',
        warning: 'ARK_API_KEY 未配置',
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await suggestBriefAvoidances('project-id', {
      idea: '一对姐妹得到两颗神药，在末日中走向不同选择。',
      genre: 'urban_drama',
      style: 'realistic_cinematic',
      target_duration_sec: 60,
      aspect_ratio: '9:16',
      target_platform: 'douyin',
      narrative_protagonist: 'dual',
      target_audience: 'general',
      emotional_rewards: ['family'],
      audience_profile: '',
      production_format: 'live_action',
      primary_market: 'CN',
      canonical_language: 'zh-CN',
      content_requirements: ['前三秒建立危机'],
      existing_avoidances: [],
    })

    expect(result.items).toEqual(['避免人物设定前后矛盾', '避免未授权素材露出'])
    expect(result.warning).toBe('ARK_API_KEY 未配置')
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/v1/projects/project-id/brief-avoidance-suggestions',
    )
  })

  it('sends the brief context and maps blocking question suggestions', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: {
        items: ['主角最终是否公开能力来源？'],
        provider: 'volcengine-ark',
        model: 'doubao-seed-2-0-lite-260215',
        warning: null,
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await suggestBriefBlockingQuestions('project-id', {
      idea: '一对姐妹得到两颗神药，在末日中走向不同选择。',
      genre: 'urban_drama',
      style: 'realistic_cinematic',
      target_duration_sec: 60,
      aspect_ratio: '9:16',
      target_platform: 'douyin',
      narrative_protagonist: 'dual',
      target_audience: 'general',
      emotional_rewards: ['family'],
      audience_profile: '',
      production_format: 'live_action',
      primary_market: 'CN',
      canonical_language: 'zh-CN',
      content_requirements: ['前三秒建立危机'],
      content_avoidances: ['避免无铺垫反转'],
      existing_questions: [],
    })

    expect(result.items).toEqual(['主角最终是否公开能力来源？'])
    expect(result.warning).toBeUndefined()
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/v1/projects/project-id/brief-blocking-question-suggestions',
    )
  })

  it('sends the complete brief to Seed and maps a story rewrite', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: {
        original: '姐妹得到两颗神药，七天后末日到来。',
        rewritten: '七天后末日将至，一对姐妹得到能力相反的两颗神药。',
        logic_checks: ['人物关系未变', '时间线未变', '能力设定未变'],
        provider: 'volcengine-ark',
        model: 'doubao-seed-2-0-lite-260215',
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await rewriteBriefStory('project-id', {
      idea: '姐妹得到两颗神药，七天后末日到来。',
      genre: 'urban_drama',
      style: 'realistic_cinematic',
      target_duration_sec: 60,
      aspect_ratio: '9:16',
      target_platform: 'douyin',
      secondary_platforms: ['reels'],
      narrative_protagonist: 'dual',
      target_audience: 'general',
      emotional_rewards: ['family'],
      audience_profile: '',
      production_format: 'live_action',
      primary_market: 'CN',
      secondary_markets: ['SG'],
      canonical_language: 'zh-CN',
      localization_targets: ['en-SG'],
      content_requirements: ['前三秒建立危机'],
      content_avoidances: ['未授权品牌露出'],
    })

    expect(result).toMatchObject({
      rewritten: '七天后末日将至，一对姐妹得到能力相反的两颗神药。',
      logicChecks: ['人物关系未变', '时间线未变', '能力设定未变'],
      provider: 'volcengine-ark',
    })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/projects/project-id/story-rewrites')
    const request = fetchMock.mock.calls[0][1] as RequestInit
    expect(JSON.parse(String(request.body))).toMatchObject({
      secondary_platforms: ['reels'],
      narrative_protagonist: 'dual',
      target_audience: 'general',
      emotional_rewards: ['family'],
      content_requirements: ['前三秒建立危机'],
    })
  })
})

describe('API error compatibility', () => {
  it('preserves a default FastAPI detail error instead of throwing while parsing it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: 'Not Found',
    }), { status: 404, statusText: 'Not Found' })))

    const request = enhanceShotPrompt('shot-id', '需要增加画面细节')
    await expect(request).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      code: 'HTTP_404',
      message: 'Not Found',
    })
  })

  it('handles a non-JSON proxy error safely', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      'Bad Gateway',
      { status: 502, statusText: 'Bad Gateway' },
    )))

    await expect(enhanceShotPrompt('shot-id', '需要增加画面细节')).rejects.toMatchObject({
      name: 'ApiError',
      status: 502,
      code: 'HTTP_502',
      message: 'Bad Gateway',
    })
  })
})

describe('relationship graph client', () => {
  it('maps the versioned graph, editability and workspace gate fields', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: {
        directions: [],
        story_dna_versions: [],
        story_bible_versions: [],
        relationship_graph_versions: [apiRelationshipGraph],
        current_approved_relationship_graph_id: null,
        has_unapproved_relationship_revision: true,
        current_script_relationship_graph_id: null,
        relationship_graph_stale: false,
        episode_outline_versions: [],
        script_versions: [{
          id: 'script-v1',
          version: 1,
          status: 'READY_FOR_REVIEW',
          payload: {},
          critic: {},
          content_hash: 'script-hash',
          provider: 'mock',
          model: 'mock-script',
          config_version: 'script-v4',
          relationship_graph_version_id: apiRelationshipGraph.id,
          episode_ordinal: 1,
          estimated_duration_ms: 60_000,
          scenes: [],
        }],
      },
      trace_id: 'trace-relationship-workspace',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    const workspace = await fetchStoryWorkspace(apiProject.id)

    expect(workspace.currentRelationshipGraphId).toBe(apiRelationshipGraph.id)
    expect(workspace.hasUnapprovedRelationshipRevision).toBe(true)
    expect(workspace.relationshipGraphVersions[0]).toMatchObject({
      projectId: apiProject.id,
      storyBibleVersionId: apiRelationshipGraph.story_bible_version_id,
      projectLockVersion: 2,
      editability: { semanticEditable: true, canApprove: true },
      graph: {
        edges: [{
          relationshipKey: 'lead-rival',
          surfaceRelationship: '互相怀疑',
          conflictIntensity: 3,
        }],
      },
    })
    expect(workspace.scriptVersions[0]).toMatchObject({
      relationshipGraphVersionId: apiRelationshipGraph.id,
      episodeOrdinal: 1,
    })
  })

  it('serializes a full local draft with both optimistic lock versions', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: apiRelationshipGraph,
      trace_id: 'trace-relationship-save',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    const graph: RelationshipGraphPayloadRecord = {
      schemaVersion: 'relationship-graph-v1',
      edges: [{
        relationshipKey: 'lead-rival',
        sourceCharacterKey: 'lead',
        targetCharacterKey: 'rival',
        directionality: 'BIDIRECTIONAL',
        relationshipTypes: ['RIVAL'],
        surfaceRelationship: '暂时合作',
        trueRelationship: '共享旧案秘密',
        sourceView: { perceivedRelationship: '对手', belief: '对方隐瞒证据' },
        targetView: { perceivedRelationship: '嫌疑人', belief: '对方操控现场' },
        trustLevel: -1,
        emotionalTemperature: 0,
        powerBalance: 0,
        conflictIntensity: 2,
        storyFunction: '推动认证',
        secret: null,
        isCore: true,
        locked: false,
        ordinal: 1,
      }],
      beats: [],
      coreRelationshipKeys: ['lead-rival'],
      generationNotes: [],
    }

    await saveRelationshipGraph(apiRelationshipGraph.id, 2, 1, graph)

    const request = fetchMock.mock.calls[0][1] as RequestInit
    const body = JSON.parse(String(request.body))
    expect(request.method).toBe('PATCH')
    expect(body).toMatchObject({
      expected_project_version: 2,
      expected_graph_version: 1,
      edges: [{
        relationship_key: 'lead-rival',
        surface_relationship: '暂时合作',
        trust_level: -1,
      }],
      core_relationship_keys: ['lead-rival'],
    })
  })

  it('maps relationship diff and creates a confirmed revision from the analyzed impact', async () => {
    const impactResponse = {
      project_id: apiProject.id,
      project_version: 8,
      base_relationship_graph_id: apiRelationshipGraph.id,
      base_content_hash: 'a'.repeat(64),
      relationship_keys: ['lead-rival'],
      intent: '调整真实关系并同步受影响场景',
      affected: {
        episode_ordinals: [1],
        outline_version_ids: ['outline-1'],
        script_version_ids: ['script-1'],
        scenes: [{ id: 'scene-1', ordinal: 2, heading: '暗房对峙' }],
        regenerate_asset_types: ['分集大纲', '剧本'],
        preserved_asset_types: ['故事设定'],
      },
      estimate: { points: 20, seconds: 45 },
      touches_approved: false,
      impact_hash: 'b'.repeat(64),
      requires_confirmation: true,
    }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: {
        from_graph_id: 'graph-v1',
        to_graph_id: 'graph-v2',
        from_version: 1,
        to_version: 2,
        highest_priority: 'P1',
        counts: { P0: 0, P1: 1, P2: 0, P3: 0, P4: 0 },
        changes: [{
          category: 'RELATIONSHIP_CHANGED',
          priority: 'P1',
          relationship_key: 'lead-rival',
          fields: ['true_relationship'],
          before: { true_relationship: '对立' },
          after: { true_relationship: '利用' },
          summary: '真实关系变化',
        }],
      } }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: impactResponse }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: {
        revision_graph: { ...apiRelationshipGraph, version: 2, parent_version_id: apiRelationshipGraph.id },
        change_set: { id: 'change-set-1' },
      } }), { status: 201, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    const diff = await fetchRelationshipGraphDiff('graph-v1', 'graph-v2')
    expect(diff.changes[0]).toMatchObject({ relationshipKey: 'lead-rival', priority: 'P1' })
    const graph = {
      id: apiRelationshipGraph.id,
      projectId: apiProject.id,
      projectLockVersion: 8,
    } as never
    const impact = await analyzeRelationshipRevisionImpact(
      graph,
      ['lead-rival'],
      impactResponse.intent,
    )
    expect(impact.affected.scenes[0].heading).toBe('暗房对峙')
    const revision = await createRelationshipGraphRevision(impact)
    expect(revision).toMatchObject({ version: 2, parentVersionId: apiRelationshipGraph.id })
    const createBody = JSON.parse(String((fetchMock.mock.calls[2][1] as RequestInit).body))
    expect(createBody).toMatchObject({
      confirmed: true,
      impact_hash: impactResponse.impact_hash,
      expected_version: 8,
    })
  })
})
