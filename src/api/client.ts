import type {
  AppState,
  BriefVersionRecord,
  CharacterRecord,
  DirectorProposal,
  ExportEstimate,
  ExportPackage,
  Job,
  JobRecoveryRequest,
  ProjectRecord,
  ProjectReadiness,
  ProjectState,
  ProjectSummary,
  PreviewComparison,
  RevisionImpact,
  Scene,
  Shot,
  TimelineRecord,
  PlatformTarget,
  EmotionalReward,
  NarrativeProtagonist,
  ProductionFormat,
  TargetAudience,
} from '../types'

export class ApiError extends Error {
  status: number
  code: string
  userAction: string | null
  details: Record<string, unknown> | null
  traceId: string

  constructor(status: number, payload: unknown, statusText = '') {
    const root = isRecord(payload) ? payload : {}
    const error = isRecord(root.error) ? root.error : {}
    const detail = root.detail
    const detailMessage = typeof detail === 'string'
      ? detail
      : isRecord(detail) && typeof detail.message === 'string'
        ? detail.message
        : Array.isArray(detail)
          ? detail
            .map((item) => isRecord(item) && typeof item.msg === 'string' ? item.msg : null)
            .filter((item): item is string => item !== null)
            .join('；')
          : ''
    const message = typeof error.message === 'string' && error.message.trim()
      ? error.message
      : detailMessage || statusText || `请求失败（HTTP ${status}）`
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = typeof error.code === 'string' ? error.code : `HTTP_${status}`
    this.userAction = typeof error.user_action === 'string' ? error.user_action : null
    this.details = isRecord(error.details)
      ? error.details
      : Array.isArray(detail)
        ? { validation: detail }
        : isRecord(detail)
          ? detail
          : null
    this.traceId = typeof root.trace_id === 'string' ? root.trace_id : 'unknown'
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

interface ApiProject {
  id: string
  name: string
  idea: string
  genre: string
  style: string
  target_duration_sec: number
  aspect_ratio: ProjectState['aspectRatio']
  target_platform: string
  status: ProjectState['status']
  lock_version: number
  available_points: number
  timeline_version: number
  preview_approved: boolean
  export_ready: boolean
  created_at: string
  updated_at: string
}

interface ApiProjectSummary extends ApiProject {
  episode_count: number
  scene_count: number
  shot_count: number
}

interface ApiProjectReadiness {
  project_id: string
  workflow_mode: ProjectReadiness['workflowMode']
  project_status: ProjectReadiness['projectStatus']
  summary_status: ProjectReadiness['summaryStatus']
  active_stage_key: string
  active_job_count: number
  stages: Array<{
    key: string
    label: string
    status: ProjectReadiness['stages'][number]['status']
    href: string
    detail: string
  }>
  blockers: Array<{
    code: string
    message: string
    action_label: string
    action_href: string
  }>
  next_action_label: string
  next_action_href: string
  updated_at: string
}

interface ApiBriefVersion {
  id: string
  project_id: string
  version: number
  project_name: string
  raw_input: string
  genre: string
  style: string
  target_duration_sec: number
  aspect_ratio: '9:16' | '16:9'
  target_platform: string
  reference_asset_ids: string[]
  assumptions: string[]
  narrative_protagonist: NarrativeProtagonist
  target_audience: TargetAudience
  emotional_rewards: EmotionalReward[]
  audience_profile: string
  production_format: ProductionFormat
  primary_audience: string
  secondary_audiences: string[]
  primary_market: string
  secondary_markets: string[]
  canonical_language: string
  localization_targets: string[]
  platform_targets: Array<{
    platform: string
    priority: PlatformTarget['priority']
    aspect_ratio: PlatformTarget['aspectRatio']
    target_duration_sec: number
    caption_mode: PlatformTarget['captionMode']
  }>
  content_requirements: string[]
  content_avoidances: string[]
  creative_defaults: Record<string, string | number | boolean>
  blocking_questions: string[]
  payload_schema_version: string
  content_hash: string
  status: string
  created_at: string
}

interface ApiEpisode {
  id: string
}

interface ApiScene {
  id: string
  code: string
  title: string
  purpose: string
  duration_sec: number
  status: Scene['status']
}

interface ApiShot {
  id: string
  scene_id: string
  code: string
  ordinal: number
  title: string
  description: string
  dialogue: string
  duration_sec: number
  status: Shot['status']
  shot_size: Shot['shotSize']
  camera_movement: Shot['cameraMovement']
  current_take: number
  candidate_take: number | null
  continuity: Shot['continuity']
  location: string
  time_of_day: string
  current_image_url?: string | null
  candidate_image_url?: string | null
  current_image_model?: string | null
  candidate_image_model?: string | null
  current_video_url?: string | null
  candidate_video_url?: string | null
  lock_version?: number
  character_ids?: string[]
  character_look_version?: string
  character_identity_version_ids?: string[]
  character_look_version_ids?: string[]
  character_story_state_version_ids?: string[]
  character_bindings?: Array<{
    id: string
    name: string
    role: string
    visual_brief: string
    look_version: string
    locked_candidate_id: string
    reference_asset_id: string
    reference_asset_url: string
    identity_version_id?: string | null
    look_version_id?: string | null
    story_state_version_id?: string | null
  }>
  current_identity_status?: Shot['currentIdentityStatus'] | null
  candidate_identity_status?: Shot['candidateIdentityStatus'] | null
  candidate_identity_score?: number | null
  candidate_identity_message?: string | null
  current_identity_review?: ApiIdentityReviewRecord | null
  candidate_identity_review?: ApiIdentityReviewRecord | null
  latest_identity_review?: ApiIdentityReviewRecord | null
}

interface ApiIdentityReviewRecord {
  decision: import('../types').IdentityReviewDecision
  issues: import('../types').IdentityReviewIssue[]
  note?: string | null
  actor: string
  reviewed_at: string
  score?: number | null
  reference_asset_ids: string[]
  look_version?: string | null
}

interface ApiJob {
  id: string
  project_id: string
  project_name: string
  job_type: string
  entity_type: string
  entity_id: string
  label: string
  entity: string
  status: Job['status']
  progress: number
  stage: string
  attempt: number
  max_attempts: number
  available_at: string
  heartbeat_at: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
  estimated_seconds: number | null
  retryable: boolean
  error_code: string | null
  error_message: string | null
  error_details?: Record<string, unknown> | null
}

interface ApiProposal {
  id: string
  project_id: string
  version: number
  brief_version: number
  direction_key?: string
  source_proposal_ids?: string[]
  schema_version?: string
  payload: {
    narrative_targeting?: {
      narrative_protagonist: NarrativeProtagonist
      target_audience: TargetAudience
      emotional_rewards: EmotionalReward[]
      audience_profile: string
      production_format: ProductionFormat
    }
    title: string
    logline: string
    director_statement?: string
    total_duration_sec: number
    scenes: Array<{
      code: string
      title: string
      purpose?: string
      duration_sec: number
      shots?: Array<{
        code: string
        duration_sec: number
        shot_size: string
        camera: string
      }>
    }>
    assumptions?: string[]
    differentiator?: string
    audience_fit?: string
    visual_signature?: string
    selection_tradeoff?: string
    key_turns?: string[]
    risk_notes?: string[]
    sequel_setup?: {
      current_arc_closure: string
      final_reveal_or_action: string
      next_installment_conflict: string
      next_installment_objective: string
    }
    story_dna?: DirectorProposal['storyDna']
    brief_compliance?: {
      status: 'ALL_MET' | 'PARTIAL' | 'CONFLICT'
      items: Array<{
        category: 'REQUIREMENT' | 'AVOIDANCE'
        item: string
        status: 'MET' | 'PARTIAL' | 'CONFLICT'
        evidence: string
      }>
    }
    production_complexity?: {
      character_count: number
      scene_count: number
      exterior_scene_count: number
      exterior_requirements: string[]
      vfx_requirements: string[]
      estimated_generation: {
        keyframe_images: number
        video_clips: number
        voice_segments: number
      }
    }
    first_episode_rhythm?: {
      opening_3s_hook: string
      first_payoff: string
      ending_action: string
    }
    ai_recommendation?: {
      recommended: boolean
      brief_matches: string[]
      reason: string
    }
  }
  provider: string
  status: string
}

interface ApiCharacter {
  id: string
  project_id: string
  character_key: string
  name: string
  role: string
  visual_brief: string
  status: string
  locked_candidate_id: string | null
  lock_version: number
  candidates: Array<{
    id: string
    ordinal: number
    asset_id: string
    asset_url: string
    seed: string
    status: string
    selected: boolean
  }>
}

interface ApiTimeline {
  id: string
  project_id: string
  episode_id: string
  version: number
  status: string
  duration_ms: number
  baseline_hash: string
  approved_at: string | null
  assets: TimelineRecord['assets']
}

interface ApiRevisionImpact {
  base_timeline_id: string
  scope: { type: 'SHOT' | 'SCENE' | 'PROJECT'; ids: string[] }
  intent: { type: string; instruction: string }
  affected: {
    shots: string[]
    asset_types: string[]
    preserved_hashes: string[]
  }
  estimated_points: number
  estimated_seconds: number
  requires_confirmation: boolean
  story_dna_changed: boolean
  touches_approved: boolean
}

interface ApiPreviewComparison {
  left: ApiTimeline
  right: ApiTimeline
  changed_assets: string[]
  unchanged_assets: string[]
  changed_shot_ids: string[]
  summary: string
}

interface ApiExportEstimate {
  timeline_id: string
  profile: string
  estimated_points: number
  estimated_seconds: number
  rights_status: string
  blocked: boolean
  blockers: string[]
  outputs: string[]
}

interface ApiExport {
  id: string
  project_id: string
  timeline_id: string
  status: string
  profile: string
  export_profile_id?: string | null
  language?: string
  rights_status: string
  assets: ExportPackage['assets']
  created_at: string
  completed_at: string | null
}

export interface PreproductionWorkspace {
  characters: CharacterRecord[]
  looks: Array<{
    id: string
    characterId: string
    version: number
    label: string
    usageScope: string
    payload: Record<string, unknown>
    referenceAssetIds: string[]
    status: string
    contentHash: string
  }>
  locations: Array<{
    id: string
    key: string
    version: number
    name: string
    payload: Record<string, unknown>
    status: string
    contentHash: string
  }>
  props: Array<{
    id: string
    key: string
    version: number
    name: string
    payload: Record<string, unknown>
    status: string
    contentHash: string
  }>
  voices: Array<{
    id: string
    characterId: string
    version: number
    provider: string
    voiceKey: string
    consentStatus: string
    cloningEnabled: boolean
    status: string
  }>
  visualBibles: Array<{
    id: string
    version: number
    status: string
    contentHash: string
  }>
}

export interface CharacterVisualProfile {
  id: string
  version: number
  status: string
  summary: string
  identityFields: Record<string, string>
  appearanceFields: Record<string, string>
  personalityVisualization: Record<string, string>
  stylingFields: Record<string, string | string[]>
  projectStyle: Record<string, string>
  negativeConstraints: string[]
  conflictReport: Array<{
    severity: 'BLOCKER' | 'WARNING' | 'INFO'
    code: string
    message: string
    suggestion: string
  }>
  contentHash: string
}

export interface FamilyResemblanceConstraint {
  id: string
  version: number
  status: 'WAITING_FOR_LOCKED_RELATIVE' | 'ACTIVE' | 'SUPERSEDED'
  similarityLevel: 'LOW' | 'MEDIUM' | 'HIGH' | 'VERY_HIGH'
  sourceCharacterIds: string[]
  sourceIdentityVersionIds: string[]
  inheritedFeatures: Array<{
    field: string
    label: string
    value: string
    sourceCharacterId: string
    sourceCharacterName: string
    sourceIdentityVersionId: string
  }>
  relationshipEvidence: Array<{
    relationshipKey: string
    relativeCharacterKey: string
    relationType: FamilyRelationType
    sharedUpbringing: SharedUpbringing
    upbringingContext?: string
  }>
  temperamentAffinity: {
    level: string
    instruction: string
    basis: Array<Record<string, unknown>>
  }
  independenceConstraints: string[]
}

export interface CharacterVisualRecord {
  id: string
  characterKey: string
  name: string
  role: string
  visualBrief: string
  status: string
  lockVersion: number
  sourceStale: boolean
  pendingSourceChanges?: {
    storyBibleVersion: number
    storyBibleStatus: string
    changedFields: string[]
  }
  lockedCandidateId?: string
  currentProfileVersionId?: string
  lockedIdentityVersionId?: string
  activeLookVersionId?: string
  activeStoryStateVersionId?: string
  profile?: CharacterVisualProfile
  familyResemblanceConstraint?: FamilyResemblanceConstraint
  batches: Array<{
    id: string
    version: number
    profileVersionId: string
    familyConstraintVersionId?: string
    requestedCount: number
    composition: string
    status: string
  }>
  candidates: Array<{
    id: string
    ordinal: number
    batchId?: string
    profileVersionId?: string
    assetId: string
    assetUrl: string
    seed: string
    status: string
    reviewStatus: string
    selected: boolean
    deletable: boolean
    deleteBlockReason?: string
    variantKey?: string
    variantLabel?: string
    variantDescription?: string
    refinementNote?: string
    sourceCandidateId?: string
    generationPrompt: string
  }>
  identities: Array<{
    id: string
    version: number
    sourceCandidateId: string
    profileVersionId: string
    status: string
    sourceCandidateAssetUrl?: string
    lockedAt?: string
    lockedBy?: string
    assets: Array<{
      id: string
      viewType: string
      assetId: string
      assetUrl: string
      status: string
    }>
    viewJobs: Array<{
      id: string
      viewType: string
      status: string
      stage: string
      createdAt: string
      updatedAt: string
      completedAt?: string
      retryable: boolean
      errorCode?: string
      maxWaitSeconds: number
    }>
  }>
  looks: Array<{ id: string; version: number; label: string; status: string }>
  storyStates: Array<{ id: string; version: number; label: string; status: string }>
}

export interface CharacterVisualWorkspace {
  projectId: string
  projectStatus: string
  projectLockVersion: number
  defaultCandidateCount: number
  generationPolicy: string
  characters: CharacterVisualRecord[]
}

export interface StoryboardWorkspace {
  storyboard: null | {
    id: string
    version: number
    status: string
    episodeId: string
    scriptVersionId: string
    visualBibleVersionId: string
    contentHash: string
    animaticUrl?: string
  }
  shots: Array<{
    shotSpecId: string
    shotId: string
    code: string
    title: string
    description: string
    dialogue: string
    durationMs: number
    shotSize: string
    cameraMovement: string
    characterLookIds: string[]
    locationVersionId?: string
    propVersionIds: string[]
    status: string
    imageUrl?: string
    contentHash: string
  }>
  workflow: null | {
    id: string
    status: string
    currentGate?: string
    nodes: Array<{
      id: string
      nodeKey: string
      nodeType: string
      status: string
      dependencies: string[]
      degraded: boolean
    }>
  }
  gate: null | { id: string; gateKey: string; status: string; decision?: string }
}

export interface AudioWorkspace {
  soundBrief: null | {
    id: string
    version: number
    status: string
    rightsStatus: string
    payload: Record<string, unknown>
  }
  cues: Array<{
    id: string
    type: string
    ordinal: number
    startMs: number
    durationMs: number
    status: string
    payload: Record<string, unknown>
    take?: { id: string; assetId: string; approval: string; qualityStatus: string }
  }>
  lipSync: Array<{
    id: string
    shotId: string
    approval: string
    qualityStatus: string
    fallbackStrategy?: string
    sourceVideoPreserved: boolean
  }>
}

export interface TimelineWorkspace {
  timeline: null | {
    id: string
    version: number
    status: string
    durationMs: number
    baselineHash: string
    assets: Record<string, string | null>
  }
  tracks: Array<{
    id: string
    type: string
    name: string
    gainDb: number
    stemAssetId?: string
    clips: Array<{
      id: string
      sourceEntityType: string
      sourceEntityId: string
      assetId?: string
      startMs: number
      endMs: number
      contentHash: string
      degraded: boolean
    }>
  }>
  qualityChecks: Array<{
    type: string
    status: string
    score?: number
    findings: string[]
    evidence: Record<string, unknown>
  }>
  gate: null | { id: string; key: string; status: string }
}

export interface ExportProfileRecord {
  id: string
  projectId: string
  name: string
  version: number
  platform: string
  aspectRatio: '9:16' | '16:9'
  width: number
  height: number
  captionMode: 'BURNED_IN' | 'SIDECAR' | 'BOTH'
  languages: string[]
  audioTracks: string[]
  status: string
}

export interface ApiWorkspace {
  project: ApiProject
  episode: ApiEpisode
  scenes: ApiScene[]
  shots: ApiShot[]
  jobs: ApiJob[]
}

export interface ProjectCreateInput {
  name?: string
  idea: string
  genre: string
  style: string
  target_duration_sec: number
  aspect_ratio: '9:16' | '16:9'
  target_platform: string
  reference_asset_ids: string[]
  assumptions: string[]
  narrative_protagonist?: NarrativeProtagonist
  target_audience?: TargetAudience
  emotional_rewards?: EmotionalReward[]
  audience_profile?: string
  production_format?: ProductionFormat
  primary_audience?: string
  secondary_audiences?: string[]
  primary_market?: string
  secondary_markets?: string[]
  canonical_language?: string
  localization_targets?: string[]
  platform_targets?: Array<{
    platform: string
    priority: PlatformTarget['priority']
    aspect_ratio: PlatformTarget['aspectRatio']
    target_duration_sec: number
    caption_mode: PlatformTarget['captionMode']
  }>
  content_requirements?: string[]
  content_avoidances?: string[]
  creative_defaults?: Record<string, string | number | boolean>
  blocking_questions?: string[]
}

export interface ProjectUpdateInput {
  expected_version: number
  name?: string
  idea?: string
  genre?: string
  style?: string
  target_duration_sec?: number
  aspect_ratio?: '9:16' | '16:9'
  target_platform?: string
  reference_asset_ids?: string[]
  assumptions?: string[]
  narrative_protagonist?: NarrativeProtagonist
  target_audience?: TargetAudience
  emotional_rewards?: EmotionalReward[]
  audience_profile?: string
  production_format?: ProductionFormat
  primary_audience?: string
  secondary_audiences?: string[]
  primary_market?: string
  secondary_markets?: string[]
  canonical_language?: string
  localization_targets?: string[]
  platform_targets?: ProjectCreateInput['platform_targets']
  content_requirements?: string[]
  content_avoidances?: string[]
  creative_defaults?: Record<string, string | number | boolean>
  blocking_questions?: string[]
}

export interface ProjectNameSuggestionInput {
  current_name?: string
  idea: string
  genre: string
  style: string
  narrative_protagonist: NarrativeProtagonist
  target_audience: TargetAudience
  emotional_rewards: EmotionalReward[]
  audience_profile: string
  production_format: ProductionFormat
  primary_market: string
  canonical_language: string
  target_duration_sec: number
  aspect_ratio: '9:16' | '16:9'
  target_platform: string
  content_requirements: string[]
  content_avoidances: string[]
}

export interface ProjectNameSuggestion {
  original?: string
  suggested: string
  provider: string
  model: string
  warning?: string
}

export interface BriefRequirementsSuggestionInput {
  idea: string
  genre: string
  style: string
  target_duration_sec: number
  aspect_ratio: '9:16' | '16:9'
  target_platform: string
  narrative_protagonist: NarrativeProtagonist
  target_audience: TargetAudience
  emotional_rewards: EmotionalReward[]
  audience_profile: string
  production_format: ProductionFormat
  primary_market: string
  canonical_language: string
  existing_requirements: string[]
  content_avoidances: string[]
}

export interface BriefRequirementsSuggestion {
  items: string[]
  provider: string
  model: string
  warning?: string
}

export interface BriefAvoidancesSuggestionInput {
  idea: string
  genre: string
  style: string
  target_duration_sec: number
  aspect_ratio: '9:16' | '16:9'
  target_platform: string
  narrative_protagonist: NarrativeProtagonist
  target_audience: TargetAudience
  emotional_rewards: EmotionalReward[]
  audience_profile: string
  production_format: ProductionFormat
  primary_market: string
  canonical_language: string
  content_requirements: string[]
  existing_avoidances: string[]
}

export interface BriefAvoidancesSuggestion {
  items: string[]
  provider: string
  model: string
  warning?: string
}

export interface BriefBlockingQuestionsSuggestionInput {
  idea: string
  genre: string
  style: string
  target_duration_sec: number
  aspect_ratio: '9:16' | '16:9'
  target_platform: string
  narrative_protagonist: NarrativeProtagonist
  target_audience: TargetAudience
  emotional_rewards: EmotionalReward[]
  audience_profile: string
  production_format: ProductionFormat
  primary_market: string
  canonical_language: string
  content_requirements: string[]
  content_avoidances: string[]
  existing_questions: string[]
}

export interface BriefBlockingQuestionsSuggestion {
  items: string[]
  provider: string
  model: string
  warning?: string
}

export interface BriefStoryRewriteInput {
  idea: string
  genre: string
  style: string
  target_duration_sec: number
  aspect_ratio: '9:16' | '16:9'
  target_platform: string
  secondary_platforms: string[]
  narrative_protagonist: NarrativeProtagonist
  target_audience: TargetAudience
  emotional_rewards: EmotionalReward[]
  audience_profile: string
  production_format: ProductionFormat
  primary_market: string
  secondary_markets: string[]
  canonical_language: string
  localization_targets: string[]
  content_requirements: string[]
  content_avoidances: string[]
}

export interface BriefStoryRewrite {
  original: string
  rewritten: string
  logicChecks: string[]
  provider: string
  model: string
}

export interface ReferenceAsset {
  id: string
  projectId: string
  filename: string
  kind: string
  mime: string
  sizeBytes: number
  status: string
  parseStatus: string
  rightsStatus: string
  contentUrl: string
}

export interface RuntimeConfig {
  appName: string
  environment: string
  apiVersion: string
  capabilities: {
    jobWorker: boolean
    jobRecovery: boolean
    jobEventsSse: boolean
    mediaPipeline: boolean
    providerCalls: boolean
    imageProvider: string
    imageModel: string
    imageModels: Array<{ id: string; label: string }>
    optionalImageProvider: string
    videoProvider: string
    videoModel: string
    featureFlags: Record<string, boolean>
  }
}

export interface ProviderSettings {
  storage: {
    scope: string
    updatedAt: string | null
    secretsReturned: boolean
  }
  ark: {
    apiKeyConfigured: boolean
    apiKeyHint: string | null
    apiKeySource: 'saved' | 'environment' | 'default'
    responsesUrl: string
    promptModel: string
    imagesUrl: string
    imageModel: string
    videoTasksUrl: string
    videoModel: string
    requestTimeoutSeconds: number
    videoPollIntervalSeconds: number
    videoTimeoutSeconds: number
    sourceUrlFastPathSeconds: number
    identityQcEnabled: boolean
    identityAutoPassThreshold: number
  }
  tos: {
    enabled: boolean
    accessKeyConfigured: boolean
    accessKeyHint: string | null
    accessKeySource: 'saved' | 'environment' | 'default'
    secretKeyConfigured: boolean
    secretKeyHint: string | null
    securityTokenConfigured: boolean
    endpoint: string
    region: string
    bucket: string
    presignTtlSeconds: number
    objectPrefix: string
    objectExpiresDays: number
    cleanupOnCompletion: boolean
  }
}

export interface ProviderSettingsUpdate {
  ark: {
    apiKey?: string
    clearApiKey: boolean
    responsesUrl: string
    promptModel: string
    imagesUrl: string
    imageModel: string
    videoTasksUrl: string
    videoModel: string
    requestTimeoutSeconds: number
    videoPollIntervalSeconds: number
    videoTimeoutSeconds: number
    sourceUrlFastPathSeconds: number
    identityQcEnabled: boolean
    identityAutoPassThreshold: number
  }
  tos: {
    enabled: boolean
    accessKey?: string
    clearAccessKey: boolean
    secretKey?: string
    clearSecretKey: boolean
    securityToken?: string
    clearSecurityToken: boolean
    endpoint: string
    region: string
    bucket: string
    presignTtlSeconds: number
    objectPrefix: string
    objectExpiresDays: number
    cleanupOnCompletion: boolean
  }
}

export interface ProviderConnectionResult {
  provider: string
  status: 'connected' | 'error' | 'not_configured'
  message: string
}

function mapProject(project: ApiProject): ProjectRecord {
  return {
    id: project.id,
    name: project.name,
    idea: project.idea,
    genre: project.genre,
    style: project.style,
    targetDurationSec: project.target_duration_sec,
    aspectRatio: project.aspect_ratio,
    targetPlatform: project.target_platform,
    status: project.status,
    lockVersion: project.lock_version,
    availablePoints: project.available_points,
    timelineVersion: project.timeline_version,
    previewApproved: project.preview_approved,
    exportReady: project.export_ready,
    createdAt: project.created_at,
    updatedAt: project.updated_at,
  }
}

function mapProjectSummary(project: ApiProjectSummary): ProjectSummary {
  return {
    ...mapProject(project),
    episodeCount: project.episode_count,
    sceneCount: project.scene_count,
    shotCount: project.shot_count,
  }
}

function mapProjectReadiness(readiness: ApiProjectReadiness): ProjectReadiness {
  return {
    projectId: readiness.project_id,
    workflowMode: readiness.workflow_mode,
    projectStatus: readiness.project_status,
    summaryStatus: readiness.summary_status,
    activeStageKey: readiness.active_stage_key,
    activeJobCount: readiness.active_job_count,
    stages: readiness.stages,
    blockers: readiness.blockers.map((blocker) => ({
      code: blocker.code,
      message: blocker.message,
      actionLabel: blocker.action_label,
      actionHref: blocker.action_href,
    })),
    nextActionLabel: readiness.next_action_label,
    nextActionHref: readiness.next_action_href,
    updatedAt: readiness.updated_at,
  }
}

function mapJob(job: ApiJob): Job {
  return {
    id: job.id,
    projectId: job.project_id,
    projectName: job.project_name,
    jobType: job.job_type,
    entityType: job.entity_type,
    entityId: job.entity_id,
    label: job.label,
    entity: job.entity,
    status: job.status,
    progress: job.progress,
    stage: job.stage,
    attempt: job.attempt,
    maxAttempts: job.max_attempts,
    availableAt: job.available_at,
    ...(job.heartbeat_at === null ? {} : { heartbeatAt: job.heartbeat_at }),
    createdAt: job.created_at,
    updatedAt: job.updated_at,
    ...(job.completed_at === null ? {} : { completedAt: job.completed_at }),
    ...(job.estimated_seconds === null ? {} : { estimatedSeconds: job.estimated_seconds }),
    retryable: job.retryable,
    ...(job.error_code === null ? {} : { errorCode: job.error_code }),
    ...(job.error_message === null ? {} : { errorMessage: job.error_message }),
    ...(job.error_details == null ? {} : { errorDetails: job.error_details }),
  }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { Accept: 'application/json', ...init?.headers },
  })
  const rawBody = await response.text()
  let payload: unknown = null
  if (rawBody) {
    try {
      payload = JSON.parse(rawBody)
    } catch {
      payload = rawBody
    }
  }
  if (!response.ok) throw new ApiError(response.status, payload, response.statusText)
  if (!isRecord(payload) || !('data' in payload)) {
    throw new ApiError(
      502,
      { detail: '后端返回了无法识别的响应，请确认前后端版本一致' },
      'Invalid API response',
    )
  }
  return payload.data as T
}

export function mapWorkspace(workspace: ApiWorkspace): Pick<AppState, 'project' | 'jobs'> {
  const mapIdentityReview = (review: ApiIdentityReviewRecord) => ({
    decision: review.decision,
    issues: review.issues,
    ...(review.note == null ? {} : { note: review.note }),
    actor: review.actor,
    reviewedAt: review.reviewed_at,
    ...(review.score == null ? {} : { score: review.score }),
    referenceAssetIds: review.reference_asset_ids,
    ...(review.look_version == null ? {} : { lookVersion: review.look_version }),
  })
  const shots: Shot[] = workspace.shots.map((shot) => ({
    id: shot.id,
    sceneId: shot.scene_id,
    code: shot.code,
    ordinal: shot.ordinal,
    title: shot.title,
    description: shot.description,
    dialogue: shot.dialogue,
    durationSec: shot.duration_sec,
    status: shot.status,
    shotSize: shot.shot_size,
    cameraMovement: shot.camera_movement,
    currentTake: shot.current_take,
    ...(shot.candidate_take === null ? {} : { candidateTake: shot.candidate_take }),
    continuity: shot.continuity,
    location: shot.location,
    timeOfDay: shot.time_of_day,
    ...(shot.current_image_url == null ? {} : { currentImageUrl: shot.current_image_url }),
    ...(shot.candidate_image_url == null ? {} : { candidateImageUrl: shot.candidate_image_url }),
    ...(shot.current_image_model == null ? {} : { currentImageModel: shot.current_image_model }),
    ...(shot.candidate_image_model == null ? {} : { candidateImageModel: shot.candidate_image_model }),
    ...(shot.current_video_url == null ? {} : { currentVideoUrl: shot.current_video_url }),
    ...(shot.candidate_video_url == null ? {} : { candidateVideoUrl: shot.candidate_video_url }),
    lockVersion: shot.lock_version ?? 1,
    characterIds: shot.character_ids ?? [],
    characterLookVersion: shot.character_look_version ?? 'Look V1',
    characterIdentityVersionIds: shot.character_identity_version_ids ?? [],
    characterLookVersionIds: shot.character_look_version_ids ?? [],
    characterStoryStateVersionIds: shot.character_story_state_version_ids ?? [],
    characterBindings: (shot.character_bindings ?? []).map((binding) => ({
      id: binding.id,
      name: binding.name,
      role: binding.role,
      visualBrief: binding.visual_brief,
      lookVersion: binding.look_version,
      lockedCandidateId: binding.locked_candidate_id,
      referenceAssetId: binding.reference_asset_id,
      referenceAssetUrl: binding.reference_asset_url,
      ...(binding.identity_version_id == null
        ? {}
        : { identityVersionId: binding.identity_version_id }),
      ...(binding.look_version_id == null ? {} : { lookVersionId: binding.look_version_id }),
      ...(binding.story_state_version_id == null
        ? {}
        : { storyStateVersionId: binding.story_state_version_id }),
    })),
    ...(shot.current_identity_status == null
      ? {}
      : { currentIdentityStatus: shot.current_identity_status }),
    ...(shot.candidate_identity_status == null
      ? {}
      : { candidateIdentityStatus: shot.candidate_identity_status }),
    ...(shot.candidate_identity_score == null
      ? {}
      : { candidateIdentityScore: shot.candidate_identity_score }),
    ...(shot.candidate_identity_message == null
      ? {}
      : { candidateIdentityMessage: shot.candidate_identity_message }),
    ...(shot.current_identity_review == null
      ? {}
      : { currentIdentityReview: mapIdentityReview(shot.current_identity_review) }),
    ...(shot.candidate_identity_review == null
      ? {}
      : { candidateIdentityReview: mapIdentityReview(shot.candidate_identity_review) }),
    ...(shot.latest_identity_review == null
      ? {}
      : { latestIdentityReview: mapIdentityReview(shot.latest_identity_review) }),
  }))
  const scenes: Scene[] = workspace.scenes.map((scene) => ({
    id: scene.id,
    code: scene.code,
    title: scene.title,
    purpose: scene.purpose,
    durationSec: scene.duration_sec,
    status: scene.status,
    shotIds: shots.filter((shot) => shot.sceneId === scene.id).map((shot) => shot.id),
  }))
  const project: ProjectState = {
    ...mapProject(workspace.project),
    episodeId: workspace.episode.id,
    scenes,
    shots,
  }
  const jobs = workspace.jobs.map(mapJob)
  return { project, jobs }
}

export async function fetchWorkspace(projectId: string, signal?: AbortSignal) {
  const workspace = await requestJson<ApiWorkspace>(
    `/api/v1/projects/${projectId}/workspace`,
    { signal },
  )
  return mapWorkspace(workspace)
}

export async function fetchRuntimeConfig(signal?: AbortSignal): Promise<RuntimeConfig> {
  const result = await requestJson<{
    app_name: string
    environment: string
    api_version: string
    capabilities: {
      job_worker: boolean
      job_recovery: boolean
      job_events_sse: boolean
      media_pipeline: boolean
      provider_calls: boolean
      image_provider: string
      image_model: string
      image_models: Array<{ id: string; label: string }>
      optional_image_provider: string
      video_provider: string
      video_model: string
      feature_flags: Record<string, boolean>
    }
  }>('/meta/config', { signal })
  return {
    appName: result.app_name,
    environment: result.environment,
    apiVersion: result.api_version,
    capabilities: {
      jobWorker: result.capabilities.job_worker,
      jobRecovery: result.capabilities.job_recovery,
      jobEventsSse: result.capabilities.job_events_sse,
      mediaPipeline: result.capabilities.media_pipeline,
      providerCalls: result.capabilities.provider_calls,
      imageProvider: result.capabilities.image_provider,
      imageModel: result.capabilities.image_model,
      imageModels: result.capabilities.image_models,
      optionalImageProvider: result.capabilities.optional_image_provider,
      videoProvider: result.capabilities.video_provider,
      videoModel: result.capabilities.video_model,
      featureFlags: result.capabilities.feature_flags,
    },
  }
}

interface ApiProviderSettings {
  storage: {
    scope: string
    updated_at: string | null
    secrets_returned: boolean
  }
  ark: {
    api_key_configured: boolean
    api_key_hint: string | null
    api_key_source: 'saved' | 'environment' | 'default'
    responses_url: string
    prompt_model: string
    images_url: string
    image_model: string
    video_tasks_url: string
    video_model: string
    request_timeout_seconds: number
    video_poll_interval_seconds: number
    video_timeout_seconds: number
    source_url_fast_path_seconds: number
    identity_qc_enabled: boolean
    identity_auto_pass_threshold: number
  }
  tos: {
    enabled: boolean
    access_key_configured: boolean
    access_key_hint: string | null
    access_key_source: 'saved' | 'environment' | 'default'
    secret_key_configured: boolean
    secret_key_hint: string | null
    security_token_configured: boolean
    endpoint: string
    region: string
    bucket: string
    presign_ttl_seconds: number
    object_prefix: string
    object_expires_days: number
    cleanup_on_completion: boolean
  }
}

function mapProviderSettings(result: ApiProviderSettings): ProviderSettings {
  return {
    storage: {
      scope: result.storage.scope,
      updatedAt: result.storage.updated_at,
      secretsReturned: result.storage.secrets_returned,
    },
    ark: {
      apiKeyConfigured: result.ark.api_key_configured,
      apiKeyHint: result.ark.api_key_hint,
      apiKeySource: result.ark.api_key_source,
      responsesUrl: result.ark.responses_url,
      promptModel: result.ark.prompt_model,
      imagesUrl: result.ark.images_url,
      imageModel: result.ark.image_model,
      videoTasksUrl: result.ark.video_tasks_url,
      videoModel: result.ark.video_model,
      requestTimeoutSeconds: result.ark.request_timeout_seconds,
      videoPollIntervalSeconds: result.ark.video_poll_interval_seconds,
      videoTimeoutSeconds: result.ark.video_timeout_seconds,
      sourceUrlFastPathSeconds: result.ark.source_url_fast_path_seconds,
      identityQcEnabled: result.ark.identity_qc_enabled,
      identityAutoPassThreshold: result.ark.identity_auto_pass_threshold,
    },
    tos: {
      enabled: result.tos.enabled,
      accessKeyConfigured: result.tos.access_key_configured,
      accessKeyHint: result.tos.access_key_hint,
      accessKeySource: result.tos.access_key_source,
      secretKeyConfigured: result.tos.secret_key_configured,
      secretKeyHint: result.tos.secret_key_hint,
      securityTokenConfigured: result.tos.security_token_configured,
      endpoint: result.tos.endpoint,
      region: result.tos.region,
      bucket: result.tos.bucket,
      presignTtlSeconds: result.tos.presign_ttl_seconds,
      objectPrefix: result.tos.object_prefix,
      objectExpiresDays: result.tos.object_expires_days,
      cleanupOnCompletion: result.tos.cleanup_on_completion,
    },
  }
}

export async function fetchProviderSettings(signal?: AbortSignal): Promise<ProviderSettings> {
  return mapProviderSettings(
    await requestJson<ApiProviderSettings>('/api/v1/settings/providers', { signal }),
  )
}

export async function saveProviderSettings(
  update: ProviderSettingsUpdate,
): Promise<ProviderSettings> {
  const result = await requestJson<ApiProviderSettings>('/api/v1/settings/providers', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ark: {
        ...(update.ark.apiKey ? { api_key: update.ark.apiKey } : {}),
        clear_api_key: update.ark.clearApiKey,
        responses_url: update.ark.responsesUrl,
        prompt_model: update.ark.promptModel,
        images_url: update.ark.imagesUrl,
        image_model: update.ark.imageModel,
        video_tasks_url: update.ark.videoTasksUrl,
        video_model: update.ark.videoModel,
        request_timeout_seconds: update.ark.requestTimeoutSeconds,
        video_poll_interval_seconds: update.ark.videoPollIntervalSeconds,
        video_timeout_seconds: update.ark.videoTimeoutSeconds,
        source_url_fast_path_seconds: update.ark.sourceUrlFastPathSeconds,
        identity_qc_enabled: update.ark.identityQcEnabled,
        identity_auto_pass_threshold: update.ark.identityAutoPassThreshold,
      },
      tos: {
        enabled: update.tos.enabled,
        ...(update.tos.accessKey ? { access_key: update.tos.accessKey } : {}),
        clear_access_key: update.tos.clearAccessKey,
        ...(update.tos.secretKey ? { secret_key: update.tos.secretKey } : {}),
        clear_secret_key: update.tos.clearSecretKey,
        ...(update.tos.securityToken ? { security_token: update.tos.securityToken } : {}),
        clear_security_token: update.tos.clearSecurityToken,
        endpoint: update.tos.endpoint,
        region: update.tos.region,
        bucket: update.tos.bucket,
        presign_ttl_seconds: update.tos.presignTtlSeconds,
        object_prefix: update.tos.objectPrefix,
        object_expires_days: update.tos.objectExpiresDays,
        cleanup_on_completion: update.tos.cleanupOnCompletion,
      },
    }),
  })
  return mapProviderSettings(result)
}

export async function testProviderConnection(
  provider: 'ark' | 'tos',
): Promise<ProviderConnectionResult> {
  return requestJson<ProviderConnectionResult>('/api/v1/settings/providers/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider }),
  })
}

export async function uploadProjectAsset(
  projectId: string,
  file: File,
): Promise<ReferenceAsset> {
  const asset = await requestJson<{
    id: string
    project_id: string
    original_filename: string | null
    kind: string
    mime: string
    size_bytes: number
    status: string
    metadata: { parse_status?: string }
    rights_status: string
    content_url: string
  }>(`/api/v1/projects/${projectId}/assets`, {
    method: 'POST',
    headers: {
      'Content-Type': file.type || 'application/octet-stream',
      'X-Filename': encodeURIComponent(file.name),
      'X-Rights-Confirmed': 'true',
    },
    body: file,
  })
  return {
    id: asset.id,
    projectId: asset.project_id,
    filename: asset.original_filename ?? file.name,
    kind: asset.kind,
    mime: asset.mime,
    sizeBytes: asset.size_bytes,
    status: asset.status,
    parseStatus: asset.metadata.parse_status ?? 'READY',
    rightsStatus: asset.rights_status,
    contentUrl: asset.content_url,
  }
}

export async function fetchProjects(signal?: AbortSignal): Promise<ProjectSummary[]> {
  const projects = await requestJson<ApiProjectSummary[]>('/api/v1/projects', { signal })
  return projects.map(mapProjectSummary)
}

export async function fetchProject(projectId: string, signal?: AbortSignal) {
  const project = await requestJson<ApiProject>(`/api/v1/projects/${projectId}`, { signal })
  return mapProject(project)
}

export async function fetchProjectReadiness(
  projectId: string,
  signal?: AbortSignal,
): Promise<ProjectReadiness> {
  const readiness = await requestJson<ApiProjectReadiness>(
    `/api/v1/projects/${projectId}/readiness`,
    { signal },
  )
  return mapProjectReadiness(readiness)
}

export async function deleteProjectRecord(projectId: string): Promise<void> {
  await requestJson<{ project_id: string; deleted: boolean }>(`/api/v1/projects/${projectId}`, {
    method: 'DELETE',
  })
}

function mapBriefVersion(brief: ApiBriefVersion): BriefVersionRecord {
  return {
    id: brief.id,
    projectId: brief.project_id,
    version: brief.version,
    projectName: brief.project_name,
    rawInput: brief.raw_input,
    genre: brief.genre,
    style: brief.style,
    targetDurationSec: brief.target_duration_sec,
    aspectRatio: brief.aspect_ratio,
    targetPlatform: brief.target_platform,
    referenceAssetIds: brief.reference_asset_ids,
    assumptions: brief.assumptions,
    narrativeProtagonist: brief.narrative_protagonist,
    targetAudience: brief.target_audience,
    emotionalRewards: brief.emotional_rewards,
    audienceProfile: brief.audience_profile,
    productionFormat: brief.production_format,
    primaryAudience: brief.primary_audience,
    secondaryAudiences: brief.secondary_audiences,
    primaryMarket: brief.primary_market,
    secondaryMarkets: brief.secondary_markets,
    canonicalLanguage: brief.canonical_language,
    localizationTargets: brief.localization_targets,
    platformTargets: brief.platform_targets.map((target) => ({
      platform: target.platform,
      priority: target.priority,
      aspectRatio: target.aspect_ratio,
      targetDurationSec: target.target_duration_sec,
      captionMode: target.caption_mode,
    })),
    contentRequirements: brief.content_requirements,
    contentAvoidances: brief.content_avoidances,
    creativeDefaults: brief.creative_defaults,
    blockingQuestions: brief.blocking_questions,
    payloadSchemaVersion: brief.payload_schema_version,
    contentHash: brief.content_hash,
    status: brief.status,
    createdAt: brief.created_at,
  }
}

export async function fetchBriefVersions(
  projectId: string,
  signal?: AbortSignal,
): Promise<BriefVersionRecord[]> {
  const briefs = await requestJson<ApiBriefVersion[]>(
    `/api/v1/projects/${projectId}/brief-versions`,
    { signal },
  )
  return briefs.map(mapBriefVersion)
}

export async function createProjectDraft(input: ProjectCreateInput, idempotencyKey: string) {
  const result = await requestJson<{
    project: ApiProject
    brief_version: number
    idempotency_replayed: boolean
  }>('/api/v1/projects', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify(input),
  })
  return {
    project: mapProject(result.project),
    briefVersion: result.brief_version,
    idempotencyReplayed: result.idempotency_replayed,
  }
}

export async function updateProjectDraft(projectId: string, input: ProjectUpdateInput) {
  const result = await requestJson<{ project: ApiProject; brief_version: number }>(
    `/api/v1/projects/${projectId}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
  )
  return { project: mapProject(result.project), briefVersion: result.brief_version }
}

export async function suggestProjectName(
  projectId: string,
  input: ProjectNameSuggestionInput,
): Promise<ProjectNameSuggestion> {
  const result = await requestJson<{
    original: string | null
    suggested: string
    provider: string
    model: string
    warning: string | null
  }>(`/api/v1/projects/${projectId}/name-suggestions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return {
    original: result.original ?? undefined,
    suggested: result.suggested,
    provider: result.provider,
    model: result.model,
    warning: result.warning ?? undefined,
  }
}

export async function suggestBriefRequirements(
  projectId: string,
  input: BriefRequirementsSuggestionInput,
): Promise<BriefRequirementsSuggestion> {
  const result = await requestJson<{
    items: string[]
    provider: string
    model: string
    warning: string | null
  }>(`/api/v1/projects/${projectId}/brief-requirement-suggestions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return {
    items: result.items,
    provider: result.provider,
    model: result.model,
    warning: result.warning ?? undefined,
  }
}

export async function suggestBriefAvoidances(
  projectId: string,
  input: BriefAvoidancesSuggestionInput,
): Promise<BriefAvoidancesSuggestion> {
  const result = await requestJson<{
    items: string[]
    provider: string
    model: string
    warning: string | null
  }>(`/api/v1/projects/${projectId}/brief-avoidance-suggestions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return {
    items: result.items,
    provider: result.provider,
    model: result.model,
    warning: result.warning ?? undefined,
  }
}

export async function suggestBriefBlockingQuestions(
  projectId: string,
  input: BriefBlockingQuestionsSuggestionInput,
): Promise<BriefBlockingQuestionsSuggestion> {
  const result = await requestJson<{
    items: string[]
    provider: string
    model: string
    warning: string | null
  }>(`/api/v1/projects/${projectId}/brief-blocking-question-suggestions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return {
    items: result.items,
    provider: result.provider,
    model: result.model,
    warning: result.warning ?? undefined,
  }
}

export async function rewriteBriefStory(
  projectId: string,
  input: BriefStoryRewriteInput,
): Promise<BriefStoryRewrite> {
  const result = await requestJson<{
    original: string
    rewritten: string
    logic_checks: string[]
    provider: string
    model: string
  }>(`/api/v1/projects/${projectId}/story-rewrites`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return {
    original: result.original,
    rewritten: result.rewritten,
    logicChecks: result.logic_checks,
    provider: result.provider,
    model: result.model,
  }
}

export async function fetchJobs(signal?: AbortSignal): Promise<Job[]> {
  const jobs = await requestJson<ApiJob[]>('/api/v1/jobs', { signal })
  return jobs.map(mapJob)
}

export async function fetchProjectJobs(
  projectId: string,
  signal?: AbortSignal,
): Promise<Job[]> {
  const jobs = await requestJson<ApiJob[]>(`/api/v1/projects/${projectId}/jobs`, { signal })
  return jobs.map(mapJob)
}

export async function generateDirectorProposal(
  projectId: string,
  expectedVersion: number,
  idempotencyKey: string,
): Promise<Job> {
  const job = await requestJson<ApiJob>(`/api/v1/projects/${projectId}/director-proposals`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({ expected_version: expectedVersion }),
  })
  return mapJob(job)
}

export async function generateStoryDirections(
  projectId: string,
  expectedVersion: number,
  idempotencyKey: string,
): Promise<Job> {
  const job = await requestJson<ApiJob>(`/api/v1/projects/${projectId}/story-directions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({ expected_version: expectedVersion }),
  })
  return mapJob(job)
}

function mapProposal(proposal: ApiProposal): DirectorProposal {
  return {
    id: proposal.id,
    projectId: proposal.project_id,
    version: proposal.version,
    briefVersion: proposal.brief_version,
    directionKey: proposal.direction_key ?? 'legacy',
    sourceProposalIds: proposal.source_proposal_ids ?? [],
    schemaVersion: proposal.schema_version ?? 'legacy-proposal-v1',
    ...(proposal.payload.narrative_targeting == null
      ? {}
      : {
          narrativeTargeting: {
            narrativeProtagonist: proposal.payload.narrative_targeting.narrative_protagonist,
            targetAudience: proposal.payload.narrative_targeting.target_audience,
            emotionalRewards: proposal.payload.narrative_targeting.emotional_rewards,
            audienceProfile: proposal.payload.narrative_targeting.audience_profile,
            productionFormat: proposal.payload.narrative_targeting.production_format,
          },
        }),
    title: proposal.payload.title,
    logline: proposal.payload.logline,
    directorStatement: proposal.payload.director_statement ?? '',
    totalDurationSec: proposal.payload.total_duration_sec,
    scenes: proposal.payload.scenes.map((scene) => ({
      code: scene.code,
      title: scene.title,
      purpose: scene.purpose ?? '',
      durationSec: scene.duration_sec,
      shots: (scene.shots ?? []).map((shot) => ({
        code: shot.code,
        durationSec: shot.duration_sec,
        shotSize: shot.shot_size,
        camera: shot.camera,
      })),
    })),
    assumptions: proposal.payload.assumptions ?? [],
    ...(proposal.payload.differentiator == null
      ? {}
      : { differentiator: proposal.payload.differentiator }),
    ...(proposal.payload.audience_fit == null
      ? {}
      : { audienceFit: proposal.payload.audience_fit }),
    ...(proposal.payload.visual_signature == null
      ? {}
      : { visualSignature: proposal.payload.visual_signature }),
    ...(proposal.payload.selection_tradeoff == null
      ? {}
      : { selectionTradeoff: proposal.payload.selection_tradeoff }),
    ...(proposal.payload.key_turns == null ? {} : { keyTurns: proposal.payload.key_turns }),
    ...(proposal.payload.risk_notes == null ? {} : { riskNotes: proposal.payload.risk_notes }),
    ...(proposal.payload.sequel_setup == null
      ? {}
      : {
          sequelSetup: {
            currentArcClosure: proposal.payload.sequel_setup.current_arc_closure,
            finalRevealOrAction: proposal.payload.sequel_setup.final_reveal_or_action,
            nextInstallmentConflict: proposal.payload.sequel_setup.next_installment_conflict,
            nextInstallmentObjective: proposal.payload.sequel_setup.next_installment_objective,
          },
        }),
    ...(proposal.payload.story_dna == null ? {} : { storyDna: proposal.payload.story_dna }),
    ...(proposal.payload.brief_compliance == null
      ? {}
      : {
          briefCompliance: {
            status: proposal.payload.brief_compliance.status,
            items: proposal.payload.brief_compliance.items,
          },
        }),
    ...(proposal.payload.production_complexity == null
      ? {}
      : {
          productionComplexity: {
            characterCount: proposal.payload.production_complexity.character_count,
            sceneCount: proposal.payload.production_complexity.scene_count,
            exteriorSceneCount: proposal.payload.production_complexity.exterior_scene_count,
            exteriorRequirements: proposal.payload.production_complexity.exterior_requirements,
            vfxRequirements: proposal.payload.production_complexity.vfx_requirements,
            estimatedGeneration: {
              keyframeImages: proposal.payload.production_complexity.estimated_generation.keyframe_images,
              videoClips: proposal.payload.production_complexity.estimated_generation.video_clips,
              voiceSegments: proposal.payload.production_complexity.estimated_generation.voice_segments,
            },
          },
        }),
    ...(proposal.payload.first_episode_rhythm == null
      ? {}
      : {
          firstEpisodeRhythm: {
            opening3sHook: proposal.payload.first_episode_rhythm.opening_3s_hook,
            firstPayoff: proposal.payload.first_episode_rhythm.first_payoff,
            endingAction: proposal.payload.first_episode_rhythm.ending_action,
          },
        }),
    ...(proposal.payload.ai_recommendation == null
      ? {}
      : {
          aiRecommendation: {
            recommended: proposal.payload.ai_recommendation.recommended,
            briefMatches: proposal.payload.ai_recommendation.brief_matches,
            reason: proposal.payload.ai_recommendation.reason,
          },
        }),
    provider: proposal.provider,
    status: proposal.status,
  }
}

export async function fetchDirectorProposals(
  projectId: string,
  signal?: AbortSignal,
): Promise<DirectorProposal[]> {
  const proposals = await requestJson<ApiProposal[]>(
    `/api/v1/projects/${projectId}/director-proposals`,
    { signal },
  )
  return proposals.map(mapProposal)
}

export async function fetchStoryDirections(
  projectId: string,
  signal?: AbortSignal,
): Promise<DirectorProposal[]> {
  const proposals = await requestJson<ApiProposal[]>(
    `/api/v1/projects/${projectId}/story-directions`,
    { signal },
  )
  return proposals.map(mapProposal)
}

export async function mergeStoryDirections(
  projectId: string,
  expectedVersion: number,
  sourceProposalIds: string[],
): Promise<DirectorProposal> {
  const proposal = await requestJson<ApiProposal>(
    `/api/v1/projects/${projectId}/story-directions/merge`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({
        expected_version: expectedVersion,
        source_proposal_ids: sourceProposalIds,
      }),
    },
  )
  return mapProposal(proposal)
}

export interface StoryWorkspace {
  directions: DirectorProposal[]
  storyDnaVersions: Array<{
    id: string
    version: number
    title: string
    logline: string
    payload: Record<string, unknown>
    status: string
    contentHash: string
    provider: string
    model: string
  }>
  storyBibleVersions: Array<CreativeVersion>
  relationshipGraphVersions: RelationshipGraphVersionRecord[]
  currentRelationshipGraphId: string | null
  currentApprovedRelationshipGraphId: string | null
  hasUnapprovedRelationshipRevision: boolean
  currentScriptRelationshipGraphId: string | null
  relationshipGraphStale: boolean
  episodeOutlineVersions: Array<CreativeVersion & { episodeOrdinal: number }>
  scriptVersions: ScriptVersionRecord[]
}

export type RelationshipType =
  | 'FAMILY'
  | 'ROMANTIC'
  | 'FRIENDSHIP'
  | 'ALLY'
  | 'RIVAL'
  | 'AUTHORITY'
  | 'DEPENDENCY'
  | 'DEBT'
  | 'CONTROL'
  | 'SECRET'
  | 'OTHER'

export type FamilyRelationType =
  | 'UNSPECIFIED'
  | 'BIOLOGICAL_PARENT_CHILD'
  | 'BIOLOGICAL_GRANDPARENT_GRANDCHILD'
  | 'FULL_SIBLINGS'
  | 'PATERNAL_HALF_SIBLINGS'
  | 'MATERNAL_HALF_SIBLINGS'
  | 'IDENTICAL_TWINS'
  | 'FRATERNAL_TWINS'
  | 'ADOPTIVE_PARENT_CHILD'
  | 'STEP_PARENT_CHILD'
  | 'IN_LAW'
  | 'OTHER_NON_BIOLOGICAL'

export type SharedUpbringing = 'SAME_HOUSEHOLD' | 'PARTIAL' | 'SEPARATE' | 'UNKNOWN'

export interface FamilyKinshipRecord {
  relationType: FamilyRelationType
  sharedUpbringing: SharedUpbringing
  upbringingContext?: string
}

export interface RelationshipUpbringingSuggestionInput {
  familyKinship: FamilyKinshipRecord
  surfaceRelationship: string
  trueRelationship: string
}

export interface RelationshipUpbringingSuggestion {
  suggestion: string
  provider: string
  model: string
  warning?: string
}

export interface RelationshipPerspectiveRecord {
  perceivedRelationship: string
  belief: string
}

export interface RelationshipStateRecord {
  surfaceRelationship: string
  trueRelationship: string
  trustLevel: number
  emotionalTemperature: number
  powerBalance: number
  conflictIntensity: number
}

export interface RelationshipEdgeRecord extends RelationshipStateRecord {
  relationshipKey: string
  sourceCharacterKey: string
  targetCharacterKey: string
  directionality: 'BIDIRECTIONAL' | 'DIRECTED'
  relationshipTypes: RelationshipType[]
  familyKinship?: FamilyKinshipRecord
  sourceView: RelationshipPerspectiveRecord
  targetView: RelationshipPerspectiveRecord
  storyFunction: string
  secret: string | null
  isCore: boolean
  locked: boolean
  ordinal: number
}

export interface RelationshipBeatRecord {
  relationshipKey: string
  episodeOrdinal: number
  sequence: number
  sceneOrdinal: number | null
  triggerType: 'STORY_EVENT' | 'MISJUDGMENT' | 'AUTHENTICATION' | 'REVEAL' | 'CHOICE' | 'BETRAYAL' | 'PAYOFF'
  triggerRef: string | null
  beforeState: RelationshipStateRecord
  afterState: RelationshipStateRecord
  evidence: string
  emotionalConsequence: string
  audienceVisibility: 'HIDDEN' | 'PARTIAL' | 'REVEALED'
  ordinal: number
}

export interface RelationshipGraphPayloadRecord {
  schemaVersion: 'relationship-graph-v1'
  edges: RelationshipEdgeRecord[]
  beats: RelationshipBeatRecord[]
  coreRelationshipKeys: string[]
  generationNotes: string[]
}

export interface RelationshipGraphValidationIssue {
  severity: 'BLOCKER' | 'WARNING' | 'INFO'
  code: string
  message: string
  relationshipKey: string | null
  characterKey: string | null
}

export interface RelationshipGraphEditability {
  semanticEditable: boolean
  layoutEditable: boolean
  canSubmit: boolean
  canApprove: boolean
  canCreateRevision: boolean
  activeJob: boolean
  reasonCode: string | null
  reasonMessage: string | null
  requiresImpactConfirmation: boolean
}

export interface RelationshipGraphVersionRecord {
  id: string
  projectId: string
  storyBibleVersionId: string
  version: number
  parentVersionId: string | null
  status: string
  schemaVersion: string
  configVersion: string
  provider: string
  model: string
  contentHash: string
  lockVersion: number
  projectLockVersion: number
  approvedAt: string | null
  approvedBy: string | null
  createdAt: string
  graph: RelationshipGraphPayloadRecord
  validationIssues: RelationshipGraphValidationIssue[]
  editability: RelationshipGraphEditability
}

export interface RelationshipGraphValidation {
  graphId: string
  validForApproval: boolean
  issues: RelationshipGraphValidationIssue[]
}

export interface RelationshipGraphDiffChange {
  category: 'RELATIONSHIP_ADDED' | 'RELATIONSHIP_REMOVED' | 'RELATIONSHIP_CHANGED' | 'BEAT_ADDED' | 'BEAT_REMOVED' | 'BEAT_CHANGED'
  priority: 'P0' | 'P1' | 'P2' | 'P3' | 'P4'
  relationshipKey: string
  episodeOrdinal: number | null
  beatOrdinal: number | null
  fields: string[]
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  summary: string
}

export interface RelationshipGraphDiff {
  fromGraphId: string
  toGraphId: string
  fromVersion: number
  toVersion: number
  highestPriority: RelationshipGraphDiffChange['priority'] | null
  counts: Record<RelationshipGraphDiffChange['priority'], number>
  changes: RelationshipGraphDiffChange[]
}

export interface RelationshipRevisionImpact {
  projectId: string
  projectVersion: number
  baseRelationshipGraphId: string
  baseContentHash: string
  relationshipKeys: string[]
  intent: string
  affected: {
    episodeOrdinals: number[]
    outlineVersionIds: string[]
    scriptVersionIds: string[]
    scenes: Array<{ id: string; ordinal: number; heading: string }>
    regenerateAssetTypes: string[]
    preservedAssetTypes: string[]
  }
  estimate: { points: number; seconds: number }
  touchesApproved: boolean
  impactHash: string
  requiresConfirmation: boolean
}

export interface StoryPackageEstimate {
  assets: string[]
  estimatedSeconds: number
  estimatedPoints: number
  directionLock: 'ON_SUCCESS'
  versionStrategy: 'CREATE_NEW_VERSION'
}

export interface CreativeVersion {
  id: string
  version: number
  status: string
  payload: Record<string, unknown>
  critic: Record<string, unknown>
  contentHash: string
  provider: string
  model: string
  configVersion: string
}

export interface ScriptVersionRecord extends CreativeVersion {
  relationshipGraphVersionId: string | null
  episodeOrdinal: number
  estimatedDurationMs: number
  scenes: Array<{
    id: string
    ordinal: number
    heading: string
    location: string
    timeOfDay: string
    purpose: string
    emotion: string
    durationMs: number
    bgmIntent: string
    sfxIntents: string[]
    lines: Array<{
      id: string
      ordinal: number
      speakerKey: string
      text: string
      lineType: string
      emotion: string
      speechRate: number
      pauseAfterMs: number
      estimatedDurationMs: number
      pronunciation: Record<string, string>
      localizations: Record<string, string>
    }>
  }>
}

export type ScriptExcerptRewriteAction =
  | 'REWRITE'
  | 'SHORTEN'
  | 'INTENSIFY_CONFLICT'
  | 'ADJUST_TONE'
  | 'CUSTOM'

export interface ScriptExcerptRewrite {
  id: string
  projectId: string
  baseScriptVersionId: string
  baseLineId: string
  parentRevisionId: string | null
  appliedScriptVersionId: string | null
  episodeOrdinal: number
  sceneOrdinal: number
  lineOrdinal: number
  version: number
  selectionStart: number
  selectionEnd: number
  originalText: string
  proposedText: string
  action: ScriptExcerptRewriteAction
  customInstruction: string | null
  tone: string | null
  rationale: string
  status: 'GENERATED' | 'APPLIED'
  provider: string
  model: string
  createdAt: string
  appliedAt: string | null
}

interface ApiScriptExcerptRewrite {
  id: string
  project_id: string
  base_script_version_id: string
  base_line_id: string
  parent_revision_id: string | null
  applied_script_version_id: string | null
  episode_ordinal: number
  scene_ordinal: number
  line_ordinal: number
  version: number
  selection_start: number
  selection_end: number
  original_text: string
  proposed_text: string
  action: ScriptExcerptRewriteAction
  custom_instruction: string | null
  tone: string | null
  rationale: string
  status: 'GENERATED' | 'APPLIED'
  provider: string
  model: string
  created_at: string
  applied_at: string | null
}

interface ApiCreativeVersion {
  id: string
  version: number
  status: string
  payload: Record<string, unknown>
  critic: Record<string, unknown>
  content_hash: string
  provider: string
  model: string
  config_version: string
  relationship_graph_version_id?: string | null
  episode_ordinal?: number
  estimated_duration_ms?: number
  scenes?: Array<{
    id: string
    ordinal: number
    heading: string
    location: string
    time_of_day: string
    purpose: string
    emotion: string
    duration_ms: number
    bgm_intent: string
    sfx_intents: string[]
    lines: Array<{
      id: string
      ordinal: number
      speaker_key: string
      text: string
      line_type: string
      emotion: string
      speech_rate: number
      pause_after_ms: number
      estimated_duration_ms: number
      pronunciation: Record<string, string>
      localizations: Record<string, string>
    }>
  }>
}

interface ApiRelationshipState {
  surface_relationship: string
  true_relationship: string
  trust_level: number
  emotional_temperature: number
  power_balance: number
  conflict_intensity: number
}

interface ApiRelationshipEdge extends ApiRelationshipState {
  relationship_key: string
  source_character_key: string
  target_character_key: string
  directionality: RelationshipEdgeRecord['directionality']
  relationship_types: RelationshipType[]
  family_kinship?: {
    relation_type: FamilyRelationType
    shared_upbringing: SharedUpbringing
    upbringing_context?: string | null
  } | null
  source_view: { perceived_relationship: string; belief: string }
  target_view: { perceived_relationship: string; belief: string }
  story_function: string
  secret: string | null
  is_core: boolean
  locked: boolean
  ordinal: number
}

interface ApiRelationshipBeat {
  relationship_key: string
  episode_ordinal: number
  sequence: number
  scene_ordinal: number | null
  trigger_type: RelationshipBeatRecord['triggerType']
  trigger_ref: string | null
  before_state: ApiRelationshipState
  after_state: ApiRelationshipState
  evidence: string
  emotional_consequence: string
  audience_visibility: RelationshipBeatRecord['audienceVisibility']
  ordinal: number
}

interface ApiRelationshipGraphVersion {
  id: string
  project_id: string
  story_bible_version_id: string
  version: number
  parent_version_id: string | null
  status: string
  schema_version: string
  config_version: string
  provider: string
  model: string
  content_hash: string
  lock_version: number
  project_lock_version: number
  approved_at: string | null
  approved_by: string | null
  created_at: string
  graph: {
    schema_version: 'relationship-graph-v1'
    edges: ApiRelationshipEdge[]
    beats: ApiRelationshipBeat[]
    core_relationship_keys: string[]
    generation_notes: string[]
  }
  validation_issues: Array<{
    severity: RelationshipGraphValidationIssue['severity']
    code: string
    message: string
    relationship_key?: string | null
    character_key?: string | null
  }>
  editability: {
    semantic_editable: boolean
    layout_editable: boolean
    can_submit: boolean
    can_approve: boolean
    can_create_revision: boolean
    active_job: boolean
    reason_code: string | null
    reason_message: string | null
    requires_impact_confirmation: boolean
  }
  job?: ApiJob
}

function mapRelationshipState(state: ApiRelationshipState): RelationshipStateRecord {
  return {
    surfaceRelationship: state.surface_relationship,
    trueRelationship: state.true_relationship,
    trustLevel: state.trust_level,
    emotionalTemperature: state.emotional_temperature,
    powerBalance: state.power_balance,
    conflictIntensity: state.conflict_intensity,
  }
}

function serializeRelationshipState(state: RelationshipStateRecord): ApiRelationshipState {
  return {
    surface_relationship: state.surfaceRelationship,
    true_relationship: state.trueRelationship,
    trust_level: state.trustLevel,
    emotional_temperature: state.emotionalTemperature,
    power_balance: state.powerBalance,
    conflict_intensity: state.conflictIntensity,
  }
}

function mapRelationshipGraphVersion(item: ApiRelationshipGraphVersion): RelationshipGraphVersionRecord {
  return {
    id: item.id,
    projectId: item.project_id,
    storyBibleVersionId: item.story_bible_version_id,
    version: item.version,
    parentVersionId: item.parent_version_id,
    status: item.status,
    schemaVersion: item.schema_version,
    configVersion: item.config_version,
    provider: item.provider,
    model: item.model,
    contentHash: item.content_hash,
    lockVersion: item.lock_version,
    projectLockVersion: item.project_lock_version,
    approvedAt: item.approved_at,
    approvedBy: item.approved_by,
    createdAt: item.created_at,
    graph: {
      schemaVersion: item.graph.schema_version,
      edges: item.graph.edges.map((edge) => ({
        relationshipKey: edge.relationship_key,
        sourceCharacterKey: edge.source_character_key,
        targetCharacterKey: edge.target_character_key,
        directionality: edge.directionality,
        relationshipTypes: edge.relationship_types,
        ...(edge.family_kinship == null ? {} : {
          familyKinship: {
            relationType: edge.family_kinship.relation_type,
            sharedUpbringing: edge.family_kinship.shared_upbringing,
            ...(edge.family_kinship.upbringing_context
              ? { upbringingContext: edge.family_kinship.upbringing_context }
              : {}),
          },
        }),
        sourceView: {
          perceivedRelationship: edge.source_view.perceived_relationship,
          belief: edge.source_view.belief,
        },
        targetView: {
          perceivedRelationship: edge.target_view.perceived_relationship,
          belief: edge.target_view.belief,
        },
        ...mapRelationshipState(edge),
        storyFunction: edge.story_function,
        secret: edge.secret,
        isCore: edge.is_core,
        locked: edge.locked,
        ordinal: edge.ordinal,
      })),
      beats: item.graph.beats.map((beat) => ({
        relationshipKey: beat.relationship_key,
        episodeOrdinal: beat.episode_ordinal,
        sequence: beat.sequence,
        sceneOrdinal: beat.scene_ordinal,
        triggerType: beat.trigger_type,
        triggerRef: beat.trigger_ref,
        beforeState: mapRelationshipState(beat.before_state),
        afterState: mapRelationshipState(beat.after_state),
        evidence: beat.evidence,
        emotionalConsequence: beat.emotional_consequence,
        audienceVisibility: beat.audience_visibility,
        ordinal: beat.ordinal,
      })),
      coreRelationshipKeys: item.graph.core_relationship_keys,
      generationNotes: item.graph.generation_notes,
    },
    validationIssues: item.validation_issues.map((issue) => ({
      severity: issue.severity,
      code: issue.code,
      message: issue.message,
      relationshipKey: issue.relationship_key ?? null,
      characterKey: issue.character_key ?? null,
    })),
    editability: {
      semanticEditable: item.editability.semantic_editable,
      layoutEditable: item.editability.layout_editable,
      canSubmit: item.editability.can_submit,
      canApprove: item.editability.can_approve,
      canCreateRevision: item.editability.can_create_revision,
      activeJob: item.editability.active_job,
      reasonCode: item.editability.reason_code,
      reasonMessage: item.editability.reason_message,
      requiresImpactConfirmation: item.editability.requires_impact_confirmation,
    },
  }
}

function mapCreativeVersion(version: ApiCreativeVersion): CreativeVersion {
  return {
    id: version.id,
    version: version.version,
    status: version.status,
    payload: version.payload,
    critic: version.critic,
    contentHash: version.content_hash,
    provider: version.provider,
    model: version.model,
    configVersion: version.config_version,
  }
}

function mapScriptExcerptRewrite(
  revision: ApiScriptExcerptRewrite,
): ScriptExcerptRewrite {
  return {
    id: revision.id,
    projectId: revision.project_id,
    baseScriptVersionId: revision.base_script_version_id,
    baseLineId: revision.base_line_id,
    parentRevisionId: revision.parent_revision_id,
    appliedScriptVersionId: revision.applied_script_version_id,
    episodeOrdinal: revision.episode_ordinal,
    sceneOrdinal: revision.scene_ordinal,
    lineOrdinal: revision.line_ordinal,
    version: revision.version,
    selectionStart: revision.selection_start,
    selectionEnd: revision.selection_end,
    originalText: revision.original_text,
    proposedText: revision.proposed_text,
    action: revision.action,
    customInstruction: revision.custom_instruction,
    tone: revision.tone,
    rationale: revision.rationale,
    status: revision.status,
    provider: revision.provider,
    model: revision.model,
    createdAt: revision.created_at,
    appliedAt: revision.applied_at,
  }
}

export async function fetchStoryWorkspace(
  projectId: string,
  signal?: AbortSignal,
): Promise<StoryWorkspace> {
  const result = await requestJson<{
    directions: ApiProposal[]
    story_dna_versions: Array<{
      id: string
      version: number
      title: string
      logline: string
      payload: Record<string, unknown>
      status: string
      content_hash: string
      provider: string
      model: string
    }>
    story_bible_versions: ApiCreativeVersion[]
    relationship_graph_versions: ApiRelationshipGraphVersion[]
    current_approved_relationship_graph_id: string | null
    has_unapproved_relationship_revision: boolean
    current_script_relationship_graph_id: string | null
    relationship_graph_stale: boolean
    episode_outline_versions: ApiCreativeVersion[]
    script_versions: ApiCreativeVersion[]
  }>(`/api/v1/projects/${projectId}/story-workspace`, { signal })
  return {
    directions: result.directions.map(mapProposal),
    storyDnaVersions: result.story_dna_versions.map((item) => ({
      id: item.id,
      version: item.version,
      title: item.title,
      logline: item.logline,
      payload: item.payload,
      status: item.status,
      contentHash: item.content_hash,
      provider: item.provider,
      model: item.model,
    })),
    storyBibleVersions: result.story_bible_versions.map(mapCreativeVersion),
    relationshipGraphVersions: result.relationship_graph_versions.map(mapRelationshipGraphVersion),
    currentRelationshipGraphId: result.relationship_graph_versions[0]?.id ?? null,
    currentApprovedRelationshipGraphId: result.current_approved_relationship_graph_id,
    hasUnapprovedRelationshipRevision: result.has_unapproved_relationship_revision,
    currentScriptRelationshipGraphId: result.current_script_relationship_graph_id,
    relationshipGraphStale: result.relationship_graph_stale,
    episodeOutlineVersions: result.episode_outline_versions.map((item) => ({
      ...mapCreativeVersion(item),
      episodeOrdinal: item.episode_ordinal ?? 1,
    })),
    scriptVersions: result.script_versions.map((item) => ({
      ...mapCreativeVersion(item),
      relationshipGraphVersionId: item.relationship_graph_version_id ?? null,
      episodeOrdinal: item.episode_ordinal ?? 1,
      estimatedDurationMs: item.estimated_duration_ms ?? 0,
      scenes: (item.scenes ?? []).map((scene) => ({
        id: scene.id,
        ordinal: scene.ordinal,
        heading: scene.heading,
        location: scene.location,
        timeOfDay: scene.time_of_day,
        purpose: scene.purpose,
        emotion: scene.emotion,
        durationMs: scene.duration_ms,
        bgmIntent: scene.bgm_intent,
        sfxIntents: scene.sfx_intents,
        lines: scene.lines.map((line) => ({
          id: line.id,
          ordinal: line.ordinal,
          speakerKey: line.speaker_key,
          text: line.text,
          lineType: line.line_type,
          emotion: line.emotion,
          speechRate: line.speech_rate,
          pauseAfterMs: line.pause_after_ms,
          estimatedDurationMs: line.estimated_duration_ms,
          pronunciation: line.pronunciation,
          localizations: line.localizations,
        })),
      })),
    })),
  }
}

export async function fetchStoryPackageEstimate(
  projectId: string,
  signal?: AbortSignal,
): Promise<StoryPackageEstimate> {
  const result = await requestJson<{
    assets: string[]
    estimated_seconds: number
    estimated_points: number
    direction_lock: 'ON_SUCCESS'
    version_strategy: 'CREATE_NEW_VERSION'
  }>(`/api/v1/projects/${projectId}/story-package-estimate`, { signal })
  return {
    assets: result.assets,
    estimatedSeconds: result.estimated_seconds,
    estimatedPoints: result.estimated_points,
    directionLock: result.direction_lock,
    versionStrategy: result.version_strategy,
  }
}

export async function generateStoryPackage(
  projectId: string,
  proposalVersion: number,
  expectedVersion: number,
): Promise<Job> {
  const job = await requestJson<ApiJob>(
    `/api/v1/projects/${projectId}/story-dna/${proposalVersion}/approve`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({ expected_version: expectedVersion, actor: '创作者' }),
    },
  )
  return mapJob(job)
}

function serializeRelationshipGraph(graph: RelationshipGraphPayloadRecord) {
  return {
    edges: graph.edges.map((edge) => ({
      relationship_key: edge.relationshipKey,
      source_character_key: edge.sourceCharacterKey,
      target_character_key: edge.targetCharacterKey,
      directionality: edge.directionality,
      relationship_types: edge.relationshipTypes,
      ...(edge.familyKinship ? {
        family_kinship: {
          relation_type: edge.familyKinship.relationType,
          shared_upbringing: edge.familyKinship.sharedUpbringing,
          upbringing_context: edge.familyKinship.upbringingContext ?? null,
        },
      } : {}),
      surface_relationship: edge.surfaceRelationship,
      true_relationship: edge.trueRelationship,
      source_view: {
        perceived_relationship: edge.sourceView.perceivedRelationship,
        belief: edge.sourceView.belief,
      },
      target_view: {
        perceived_relationship: edge.targetView.perceivedRelationship,
        belief: edge.targetView.belief,
      },
      trust_level: edge.trustLevel,
      emotional_temperature: edge.emotionalTemperature,
      power_balance: edge.powerBalance,
      conflict_intensity: edge.conflictIntensity,
      story_function: edge.storyFunction,
      secret: edge.secret,
      is_core: edge.isCore,
      locked: edge.locked,
      ordinal: edge.ordinal,
    })),
    beats: graph.beats.map((beat) => ({
      relationship_key: beat.relationshipKey,
      episode_ordinal: beat.episodeOrdinal,
      sequence: beat.sequence,
      scene_ordinal: beat.sceneOrdinal,
      trigger_type: beat.triggerType,
      trigger_ref: beat.triggerRef,
      before_state: serializeRelationshipState(beat.beforeState),
      after_state: serializeRelationshipState(beat.afterState),
      evidence: beat.evidence,
      emotional_consequence: beat.emotionalConsequence,
      audience_visibility: beat.audienceVisibility,
      ordinal: beat.ordinal,
    })),
    core_relationship_keys: graph.coreRelationshipKeys,
    generation_notes: graph.generationNotes,
  }
}

export async function saveRelationshipGraph(
  graphId: string,
  expectedProjectVersion: number,
  expectedGraphVersion: number,
  graph: RelationshipGraphPayloadRecord,
): Promise<RelationshipGraphVersionRecord> {
  const result = await requestJson<ApiRelationshipGraphVersion>(
    `/api/v1/relationship-graphs/${graphId}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_project_version: expectedProjectVersion,
        expected_graph_version: expectedGraphVersion,
        ...serializeRelationshipGraph(graph),
        actor: '创作者',
      }),
    },
  )
  return mapRelationshipGraphVersion(result)
}

export async function generateRelationshipUpbringingSuggestion(
  graphId: string,
  relationshipKey: string,
  input: RelationshipUpbringingSuggestionInput,
): Promise<RelationshipUpbringingSuggestion> {
  const result = await requestJson<{
    suggestion: string
    provider: string
    model: string
    warning: string | null
  }>(
    `/api/v1/relationship-graphs/${graphId}/relationships/${encodeURIComponent(relationshipKey)}/upbringing-suggestion`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        family_kinship: {
          relation_type: input.familyKinship.relationType,
          shared_upbringing: input.familyKinship.sharedUpbringing,
          upbringing_context: input.familyKinship.upbringingContext ?? null,
        },
        surface_relationship: input.surfaceRelationship,
        true_relationship: input.trueRelationship,
      }),
    },
  )
  return {
    suggestion: result.suggestion,
    provider: result.provider,
    model: result.model,
    warning: result.warning ?? undefined,
  }
}

export async function fetchRelationshipGraphValidation(
  graphId: string,
): Promise<RelationshipGraphValidation> {
  const result = await requestJson<{
    graph_id: string
    valid_for_approval: boolean
    issues: ApiRelationshipGraphVersion['validation_issues']
  }>(`/api/v1/relationship-graphs/${graphId}/validation`)
  return {
    graphId: result.graph_id,
    validForApproval: result.valid_for_approval,
    issues: result.issues.map((issue) => ({
      severity: issue.severity,
      code: issue.code,
      message: issue.message,
      relationshipKey: issue.relationship_key ?? null,
      characterKey: issue.character_key ?? null,
    })),
  }
}

async function relationshipGraphAction(
  graph: RelationshipGraphVersionRecord,
  action: 'submit' | 'withdraw',
): Promise<RelationshipGraphVersionRecord> {
  const result = await requestJson<ApiRelationshipGraphVersion>(
    `/api/v1/relationship-graphs/${graph.id}/${action}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_project_version: graph.projectLockVersion,
        expected_graph_version: graph.lockVersion,
        actor: '创作者',
      }),
    },
  )
  return mapRelationshipGraphVersion(result)
}

export function submitRelationshipGraph(
  graph: RelationshipGraphVersionRecord,
): Promise<RelationshipGraphVersionRecord> {
  return relationshipGraphAction(graph, 'submit')
}

export function withdrawRelationshipGraph(
  graph: RelationshipGraphVersionRecord,
): Promise<RelationshipGraphVersionRecord> {
  return relationshipGraphAction(graph, 'withdraw')
}

export async function setRelationshipGraphEdgeLock(
  graph: RelationshipGraphVersionRecord,
  relationshipKey: string,
  locked: boolean,
): Promise<RelationshipGraphVersionRecord> {
  const result = await requestJson<ApiRelationshipGraphVersion>(
    `/api/v1/relationship-graphs/${graph.id}/relationships/${encodeURIComponent(relationshipKey)}/${locked ? 'lock' : 'unlock'}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_project_version: graph.projectLockVersion,
        expected_graph_version: graph.lockVersion,
        actor: '创作者',
      }),
    },
  )
  return mapRelationshipGraphVersion(result)
}

export async function approveRelationshipGraph(
  graphId: string,
  expectedProjectVersion: number,
  expectedGraphVersion: number,
): Promise<{
  graph: RelationshipGraphVersionRecord
  characterVisuals: { characterCount: number; route: string }
}> {
  const result = await requestJson<ApiRelationshipGraphVersion & {
    character_visuals: { character_count: number; route: string }
  }>(
    `/api/v1/relationship-graphs/${graphId}/approve`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_project_version: expectedProjectVersion,
        expected_graph_version: expectedGraphVersion,
        actor: '创作者',
        note: '已在故事工作区确认角色关系',
      }),
    },
  )
  return {
    graph: mapRelationshipGraphVersion(result),
    characterVisuals: {
      characterCount: result.character_visuals.character_count,
      route: result.character_visuals.route,
    },
  }
}

export async function fetchRelationshipGraphDiff(
  fromGraphId: string,
  toGraphId: string,
): Promise<RelationshipGraphDiff> {
  const result = await requestJson<{
    from_graph_id: string
    to_graph_id: string
    from_version: number
    to_version: number
    highest_priority: RelationshipGraphDiff['highestPriority']
    counts: RelationshipGraphDiff['counts']
    changes: Array<{
      category: RelationshipGraphDiffChange['category']
      priority: RelationshipGraphDiffChange['priority']
      relationship_key: string
      episode_ordinal?: number
      beat_ordinal?: number
      fields: string[]
      before: Record<string, unknown> | null
      after: Record<string, unknown> | null
      summary: string
    }>
  }>(`/api/v1/relationship-graphs/${fromGraphId}/diff/${toGraphId}`)
  return {
    fromGraphId: result.from_graph_id,
    toGraphId: result.to_graph_id,
    fromVersion: result.from_version,
    toVersion: result.to_version,
    highestPriority: result.highest_priority,
    counts: result.counts,
    changes: result.changes.map((change) => ({
      category: change.category,
      priority: change.priority,
      relationshipKey: change.relationship_key,
      episodeOrdinal: change.episode_ordinal ?? null,
      beatOrdinal: change.beat_ordinal ?? null,
      fields: change.fields,
      before: change.before,
      after: change.after,
      summary: change.summary,
    })),
  }
}

function mapRelationshipRevisionImpact(result: {
  project_id: string
  project_version: number
  base_relationship_graph_id: string
  base_content_hash: string
  relationship_keys: string[]
  intent: string
  affected: {
    episode_ordinals: number[]
    outline_version_ids: string[]
    script_version_ids: string[]
    scenes: Array<{ id: string; ordinal: number; heading: string }>
    regenerate_asset_types: string[]
    preserved_asset_types: string[]
  }
  estimate: { points: number; seconds: number }
  touches_approved: boolean
  impact_hash: string
  requires_confirmation: boolean
}): RelationshipRevisionImpact {
  return {
    projectId: result.project_id,
    projectVersion: result.project_version,
    baseRelationshipGraphId: result.base_relationship_graph_id,
    baseContentHash: result.base_content_hash,
    relationshipKeys: result.relationship_keys,
    intent: result.intent,
    affected: {
      episodeOrdinals: result.affected.episode_ordinals,
      outlineVersionIds: result.affected.outline_version_ids,
      scriptVersionIds: result.affected.script_version_ids,
      scenes: result.affected.scenes,
      regenerateAssetTypes: result.affected.regenerate_asset_types,
      preservedAssetTypes: result.affected.preserved_asset_types,
    },
    estimate: result.estimate,
    touchesApproved: result.touches_approved,
    impactHash: result.impact_hash,
    requiresConfirmation: result.requires_confirmation,
  }
}

export async function analyzeRelationshipRevisionImpact(
  graph: RelationshipGraphVersionRecord,
  relationshipKeys: string[],
  intent: string,
): Promise<RelationshipRevisionImpact> {
  const result = await requestJson<Parameters<typeof mapRelationshipRevisionImpact>[0]>(
    `/api/v1/projects/${graph.projectId}/relationship-revision-impact`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_relationship_graph_id: graph.id,
        relationship_keys: relationshipKeys,
        intent,
        expected_version: graph.projectLockVersion,
      }),
    },
  )
  return mapRelationshipRevisionImpact(result)
}

export async function createRelationshipGraphRevision(
  impact: RelationshipRevisionImpact,
): Promise<RelationshipGraphVersionRecord> {
  const result = await requestJson<{
    revision_graph: ApiRelationshipGraphVersion
    change_set: unknown
  }>(`/api/v1/projects/${impact.projectId}/relationship-revisions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      base_relationship_graph_id: impact.baseRelationshipGraphId,
      relationship_keys: impact.relationshipKeys,
      intent: impact.intent,
      expected_version: impact.projectVersion,
      confirmed: true,
      impact_hash: impact.impactHash,
      actor: '创作者',
    }),
  })
  return mapRelationshipGraphVersion(result.revision_graph)
}

export interface CharacterRevisionChanges {
  name?: string
  role?: string
  gender?: 'male' | 'female' | 'nonbinary' | 'unspecified'
  ethnicity?: string
  age?: string
  height?: string
  occupation?: string
  personality?: string[]
  dramatic_function?: string
  desire?: string
  fear?: string
  secret?: string
  visual_notes?: string
}

export interface CharacterRevisionReview {
  baseStoryBibleId: string
  baseRelationshipGraphId: string
  characterKey: string
  originalCharacter: Record<string, unknown>
  proposedCharacter: Record<string, unknown>
  changedFields: string[]
  affected: {
    relationshipKeys: string[]
    relationshipCount: number
    outlineCount: number
    scriptCount: number
    regenerateAssetTypes: string[]
    preservedAssetTypes: string[]
  }
  impactHash: string
  requiresConfirmation: boolean
  review: {
    verdict: 'PASS' | 'CONFLICT'
    summary: string
    issues: Array<{ severity: 'BLOCKER' | 'WARNING' | 'INFO'; code: string; field?: string | null; message: string; suggestion: string }>
    storySyncNotes: string[]
    relationshipSyncNotes: string[]
  }
  provider: string
  model: string
}

function mapCharacterRevisionReview(result: any): CharacterRevisionReview {
  return {
    baseStoryBibleId: result.base_story_bible_id,
    baseRelationshipGraphId: result.base_relationship_graph_id,
    characterKey: result.character_key,
    originalCharacter: result.original_character,
    proposedCharacter: result.proposed_character,
    changedFields: result.changed_fields,
    affected: {
      relationshipKeys: result.affected.relationship_keys,
      relationshipCount: result.affected.relationship_count,
      outlineCount: result.affected.outline_count,
      scriptCount: result.affected.script_count,
      regenerateAssetTypes: result.affected.regenerate_asset_types,
      preservedAssetTypes: result.affected.preserved_asset_types,
    },
    impactHash: result.impact_hash,
    requiresConfirmation: result.requires_confirmation,
    review: {
      verdict: result.review.verdict,
      summary: result.review.summary,
      issues: result.review.issues,
      storySyncNotes: result.review.story_sync_notes,
      relationshipSyncNotes: result.review.relationship_sync_notes,
    },
    provider: result.provider,
    model: result.model,
  }
}

export async function reviewCharacterRevision(projectId: string, input: {
  baseStoryBibleId: string
  baseRelationshipGraphId: string
  characterKey: string
  changes: CharacterRevisionChanges
  expectedVersion: number
}): Promise<CharacterRevisionReview> {
  const result = await requestJson<any>(`/api/v1/projects/${projectId}/character-revision-review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      base_story_bible_id: input.baseStoryBibleId,
      base_relationship_graph_id: input.baseRelationshipGraphId,
      character_key: input.characterKey,
      changes: input.changes,
      expected_version: input.expectedVersion,
    }),
  })
  return mapCharacterRevisionReview(result)
}

export async function confirmCharacterRevision(projectId: string, input: {
  baseStoryBibleId: string
  baseRelationshipGraphId: string
  characterKey: string
  changes: CharacterRevisionChanges
  expectedVersion: number
  impactHash: string
}): Promise<void> {
  await requestJson(`/api/v1/projects/${projectId}/character-revisions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      base_story_bible_id: input.baseStoryBibleId,
      base_relationship_graph_id: input.baseRelationshipGraphId,
      character_key: input.characterKey,
      changes: input.changes,
      expected_version: input.expectedVersion,
      confirmed: true,
      impact_hash: input.impactHash,
      actor: 'demo-user',
    }),
  })
}

export async function createScriptExcerptRewrite(
  scriptId: string,
  lineId: string,
  input: {
    expectedVersion: number
    selectionStart: number
    selectionEnd: number
    action: ScriptExcerptRewriteAction
    customInstruction?: string
    tone?: string
    parentRevisionId?: string
  },
): Promise<ScriptExcerptRewrite> {
  const result = await requestJson<ApiScriptExcerptRewrite>(
    `/api/v1/scripts/${scriptId}/lines/${lineId}/rewrites`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_version: input.expectedVersion,
        selection_start: input.selectionStart,
        selection_end: input.selectionEnd,
        action: input.action,
        custom_instruction: input.customInstruction,
        tone: input.tone,
        parent_revision_id: input.parentRevisionId,
      }),
    },
  )
  return mapScriptExcerptRewrite(result)
}

export async function fetchScriptExcerptRewrites(
  scriptId: string,
  lineId: string,
): Promise<ScriptExcerptRewrite[]> {
  const result = await requestJson<ApiScriptExcerptRewrite[]>(
    `/api/v1/scripts/${scriptId}/lines/${lineId}/rewrites`,
  )
  return result.map(mapScriptExcerptRewrite)
}

export async function applyScriptExcerptRewrite(
  revisionId: string,
  input: {
    expectedVersion: number
    scriptId: string
    lineId: string
  },
): Promise<{ scriptId: string; scriptVersion: number; projectLockVersion: number }> {
  const result = await requestJson<{
    rewrite: ApiScriptExcerptRewrite
    script: {
      id: string
      version: number
      project_lock_version: number
    }
  }>(`/api/v1/script-excerpt-rewrites/${revisionId}/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      expected_version: input.expectedVersion,
      script_id: input.scriptId,
      line_id: input.lineId,
    }),
  })
  return {
    scriptId: result.script.id,
    scriptVersion: result.script.version,
    projectLockVersion: result.script.project_lock_version,
  }
}

export async function approveScriptVersion(
  scriptId: string,
  expectedVersion: number,
): Promise<Job> {
  const result = await requestJson<{ script: unknown; job: ApiJob }>(
    `/api/v1/scripts/${scriptId}/approve`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({ expected_version: expectedVersion, actor: '创作者' }),
    },
  )
  return mapJob(result.job)
}

export async function approveDirectorProposal(
  projectId: string,
  proposalVersion: number,
  expectedVersion: number,
) {
  const result = await requestJson<{ story: unknown; job: ApiJob }>(
    `/api/v1/projects/${projectId}/director-proposals/${proposalVersion}/approve`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({
        expected_version: expectedVersion,
        assumptions_confirmed: true,
        actor: 'demo-user',
      }),
    },
  )
  return mapJob(result.job)
}

function mapCharacter(character: ApiCharacter): CharacterRecord {
  return {
    id: character.id,
    projectId: character.project_id,
    characterKey: character.character_key,
    name: character.name,
    role: character.role,
    visualBrief: character.visual_brief,
    status: character.status,
    ...(character.locked_candidate_id === null
      ? {}
      : { lockedCandidateId: character.locked_candidate_id }),
    lockVersion: character.lock_version,
    candidates: character.candidates.map((candidate) => ({
      id: candidate.id,
      ordinal: candidate.ordinal,
      assetId: candidate.asset_id,
      assetUrl: candidate.asset_url,
      seed: candidate.seed,
      status: candidate.status,
      selected: candidate.selected,
    })),
  }
}

export async function fetchCharacterCandidates(
  projectId: string,
  signal?: AbortSignal,
): Promise<CharacterRecord[]> {
  const characters = await requestJson<ApiCharacter[]>(
    `/api/v1/projects/${projectId}/characters/candidates`,
    { signal },
  )
  return characters.map(mapCharacter)
}

function mapCharacterVisualProfile(
  profile: Record<string, unknown>,
): CharacterVisualProfile {
  return {
    id: String(profile.id),
    version: Number(profile.version),
    status: String(profile.status),
    summary: String(profile.summary ?? ''),
    identityFields: (profile.identity_fields ?? {}) as Record<string, string>,
    appearanceFields: (profile.appearance_fields ?? {}) as Record<string, string>,
    personalityVisualization: (profile.personality_visualization ?? {}) as Record<string, string>,
    stylingFields: (profile.styling_fields ?? {}) as Record<string, string | string[]>,
    projectStyle: (profile.project_style ?? {}) as Record<string, string>,
    negativeConstraints: (profile.negative_constraints ?? []) as string[],
    conflictReport: (profile.conflict_report ?? []) as CharacterVisualProfile['conflictReport'],
    contentHash: String(profile.content_hash),
  }
}

function mapFamilyResemblanceConstraint(
  raw: Record<string, unknown>,
): FamilyResemblanceConstraint {
  const features = (raw.inherited_features ?? []) as Array<Record<string, unknown>>
  const evidence = (raw.relationship_evidence ?? []) as Array<Record<string, unknown>>
  const temperament = isRecord(raw.temperament_affinity) ? raw.temperament_affinity : {}
  return {
    id: String(raw.id),
    version: Number(raw.version),
    status: String(raw.status) as FamilyResemblanceConstraint['status'],
    similarityLevel: String(raw.similarity_level) as FamilyResemblanceConstraint['similarityLevel'],
    sourceCharacterIds: (raw.source_character_ids ?? []) as string[],
    sourceIdentityVersionIds: (raw.source_identity_version_ids ?? []) as string[],
    inheritedFeatures: features.map((item) => ({
      field: String(item.field),
      label: String(item.label),
      value: String(item.value),
      sourceCharacterId: String(item.source_character_id),
      sourceCharacterName: String(item.source_character_name),
      sourceIdentityVersionId: String(item.source_identity_version_id),
    })),
    relationshipEvidence: evidence.map((item) => ({
      relationshipKey: String(item.relationship_key),
      relativeCharacterKey: String(item.relative_character_key),
      relationType: String(item.relation_type) as FamilyRelationType,
      sharedUpbringing: String(item.shared_upbringing) as SharedUpbringing,
      ...(item.upbringing_context == null
        ? {}
        : { upbringingContext: String(item.upbringing_context) }),
    })),
    temperamentAffinity: {
      level: String(temperament.level ?? 'NONE'),
      instruction: String(temperament.instruction ?? ''),
      basis: (temperament.basis ?? []) as Array<Record<string, unknown>>,
    },
    independenceConstraints: (raw.independence_constraints ?? []) as string[],
  }
}

export async function fetchCharacterVisuals(
  projectId: string,
  signal?: AbortSignal,
): Promise<CharacterVisualWorkspace> {
  const data = await requestJson<{
    project_id: string
    project_status: string
    project_lock_version: number
    default_candidate_count: number
    generation_policy: string
    characters: Array<Record<string, unknown>>
  }>(`/api/v1/projects/${projectId}/character-visuals`, { signal })
  return {
    projectId: data.project_id,
    projectStatus: data.project_status,
    projectLockVersion: data.project_lock_version,
    defaultCandidateCount: data.default_candidate_count,
    generationPolicy: data.generation_policy,
    characters: data.characters.map((raw) => {
      const candidates = (raw.candidates ?? []) as Array<Record<string, unknown>>
      const batches = (raw.batches ?? []) as Array<Record<string, unknown>>
      const identities = (raw.identities ?? []) as Array<Record<string, unknown>>
      const looks = (raw.looks ?? []) as Array<Record<string, unknown>>
      const storyStates = (raw.story_states ?? []) as Array<Record<string, unknown>>
      return {
        id: String(raw.id),
        characterKey: String(raw.character_key),
        name: String(raw.name),
        role: String(raw.role),
        visualBrief: String(raw.visual_brief),
        status: String(raw.status),
        lockVersion: Number(raw.lock_version),
        sourceStale: Boolean(raw.source_stale),
        ...(isRecord(raw.pending_source_changes) ? {
          pendingSourceChanges: {
            storyBibleVersion: Number(raw.pending_source_changes.story_bible_version),
            storyBibleStatus: String(raw.pending_source_changes.story_bible_status),
            changedFields: (raw.pending_source_changes.changed_fields ?? []) as string[],
          },
        } : {}),
        ...(raw.locked_candidate_id == null
          ? {}
          : { lockedCandidateId: String(raw.locked_candidate_id) }),
        ...(raw.current_profile_version_id == null
          ? {}
          : { currentProfileVersionId: String(raw.current_profile_version_id) }),
        ...(raw.locked_identity_version_id == null
          ? {}
          : { lockedIdentityVersionId: String(raw.locked_identity_version_id) }),
        ...(raw.active_look_version_id == null
          ? {}
          : { activeLookVersionId: String(raw.active_look_version_id) }),
        ...(raw.active_story_state_version_id == null
          ? {}
          : { activeStoryStateVersionId: String(raw.active_story_state_version_id) }),
        ...(isRecord(raw.profile) ? { profile: mapCharacterVisualProfile(raw.profile) } : {}),
        ...(isRecord(raw.family_resemblance_constraint) ? {
          familyResemblanceConstraint: mapFamilyResemblanceConstraint(
            raw.family_resemblance_constraint,
          ),
        } : {}),
        batches: batches.map((item) => ({
          id: String(item.id),
          version: Number(item.version),
          profileVersionId: String(item.profile_version_id),
          ...(item.family_constraint_version_id == null ? {} : {
            familyConstraintVersionId: String(item.family_constraint_version_id),
          }),
          requestedCount: Number(item.requested_count),
          composition: String(item.composition),
          status: String(item.status),
        })),
        candidates: candidates.map((item) => ({
          id: String(item.id),
          ordinal: Number(item.ordinal),
          ...(item.batch_id == null ? {} : { batchId: String(item.batch_id) }),
          ...(item.profile_version_id == null
            ? {}
            : { profileVersionId: String(item.profile_version_id) }),
          assetId: String(item.asset_id),
          assetUrl: String(item.asset_url),
          seed: String(item.seed),
          status: String(item.status),
          reviewStatus: String(item.review_status),
          selected: Boolean(item.selected),
          deletable: Boolean(item.deletable),
          ...(item.delete_block_reason == null
            ? {}
            : { deleteBlockReason: String(item.delete_block_reason) }),
          ...(item.variant_key == null ? {} : { variantKey: String(item.variant_key) }),
          ...(item.variant_label == null ? {} : { variantLabel: String(item.variant_label) }),
          ...(item.variant_description == null
            ? {}
            : { variantDescription: String(item.variant_description) }),
          ...(item.refinement_note == null
            ? {}
            : { refinementNote: String(item.refinement_note) }),
          ...(item.source_candidate_id == null
            ? {}
            : { sourceCandidateId: String(item.source_candidate_id) }),
          generationPrompt: String(item.generation_prompt ?? ''),
        })),
        identities: identities.map((item) => ({
          id: String(item.id),
          version: Number(item.version),
          sourceCandidateId: String(item.source_candidate_id),
          profileVersionId: String(item.profile_version_id),
          status: String(item.status),
          ...(item.source_candidate_asset_url == null
            ? {}
            : { sourceCandidateAssetUrl: String(item.source_candidate_asset_url) }),
          ...(item.locked_at == null ? {} : { lockedAt: String(item.locked_at) }),
          ...(item.locked_by == null ? {} : { lockedBy: String(item.locked_by) }),
          assets: ((item.assets ?? []) as Array<Record<string, unknown>>).map((asset) => ({
            id: String(asset.id),
            viewType: String(asset.view_type),
            assetId: String(asset.asset_id),
            assetUrl: String(asset.asset_url),
            status: String(asset.status),
          })),
          viewJobs: ((item.view_jobs ?? []) as Array<Record<string, unknown>>).map((job) => ({
            id: String(job.id),
            viewType: String(job.view_type),
            status: String(job.status),
            stage: String(job.stage),
            createdAt: String(job.created_at),
            updatedAt: String(job.updated_at),
            ...(job.completed_at == null ? {} : { completedAt: String(job.completed_at) }),
            retryable: Boolean(job.retryable),
            ...(job.error_code == null ? {} : { errorCode: String(job.error_code) }),
            maxWaitSeconds: Number(job.max_wait_seconds),
          })),
        })),
        looks: looks.map((item) => ({
          id: String(item.id),
          version: Number(item.version),
          label: String(item.label),
          status: String(item.status),
        })),
        storyStates: storyStates.map((item) => ({
          id: String(item.id),
          version: Number(item.version),
          label: String(item.label),
          status: String(item.status),
        })),
      }
    }),
  }
}

export async function updateCharacterVisualProfile(
  projectId: string,
  characterId: string,
  expectedVersion: number,
  changes: {
    identity_fields?: Record<string, string>
    appearance_fields?: Record<string, string>
    personality_visualization?: Record<string, string>
    styling_fields?: Record<string, string | string[]>
    negative_constraints?: string[]
  },
): Promise<CharacterVisualProfile> {
  const result = await requestJson<Record<string, unknown>>(
    `/api/v1/projects/${projectId}/characters/${characterId}/visual-profile`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_version: expectedVersion, actor: '创作者', ...changes }),
    },
  )
  return mapCharacterVisualProfile(result)
}

export async function confirmCharacterVisualProfile(
  projectId: string,
  characterId: string,
  expectedVersion: number,
  profileVersionId: string,
) {
  return requestJson<Record<string, unknown>>(
    `/api/v1/projects/${projectId}/characters/${characterId}/visual-profile/confirm`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_version: expectedVersion,
        profile_version_id: profileVersionId,
        actor: '创作者',
      }),
    },
  )
}

export async function generateCharacterVisualCandidates(
  projectId: string,
  characterId: string,
  expectedVersion: number,
  profileVersionId: string,
  options?: {
    count?: 1 | 2 | 3
    sourceCandidateId?: string
    note?: string
    customPrompt?: string
  },
) {
  return requestJson<{ batch: Record<string, unknown>; jobs: ApiJob[] }>(
    `/api/v1/projects/${projectId}/characters/${characterId}/visual-candidates`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_version: expectedVersion,
        profile_version_id: profileVersionId,
        count: options?.count ?? 3,
        ...(options?.sourceCandidateId && options.note
          ? {
              source_candidate_id: options.sourceCandidateId,
              refinement_note: options.note,
            }
          : {}),
        ...(options?.sourceCandidateId && options.customPrompt
          ? {
              source_candidate_id: options.sourceCandidateId,
              custom_prompt: options.customPrompt,
            }
          : {}),
        actor: '创作者',
      }),
    },
  )
}

export async function deleteCharacterVisualCandidate(
  projectId: string,
  characterId: string,
  candidateId: string,
  expectedVersion: number,
) {
  return requestJson<{
    character_id: string
    candidate_id: string
    deleted: boolean
    lock_version: number
  }>(
    `/api/v1/projects/${projectId}/characters/${characterId}/visual-candidates/${candidateId}`,
    {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_version: expectedVersion,
        actor: '创作者',
      }),
    },
  )
}

export async function selectCharacterVisualCandidate(
  projectId: string,
  characterId: string,
  expectedVersion: number,
  candidateId: string,
) {
  return requestJson<{ identity: { id: string }; jobs: ApiJob[] }>(
    `/api/v1/projects/${projectId}/characters/${characterId}/visual-candidates/select`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_version: expectedVersion,
        candidate_id: candidateId,
        actor: '创作者',
      }),
    },
  )
}

export async function generateCharacterIdentityView(
  projectId: string,
  characterId: string,
  expectedVersion: number,
  identityVersionId: string,
  viewType: string,
  refinementNote?: string,
) {
  return requestJson<{ job: ApiJob }>(
    `/api/v1/projects/${projectId}/characters/${characterId}/identity/${identityVersionId}/views`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_version: expectedVersion,
        view_type: viewType,
        ...(refinementNote ? { refinement_note: refinementNote } : {}),
        actor: '创作者',
      }),
    },
  )
}

export async function lockCharacterVisualIdentity(
  projectId: string,
  characterId: string,
  expectedVersion: number,
  identityVersionId: string,
) {
  return requestJson<{ identity: Record<string, unknown>; script_job: ApiJob | null }>(
    `/api/v1/projects/${projectId}/characters/${characterId}/identity/lock`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_version: expectedVersion,
        identity_version_id: identityVersionId,
        actor: '创作者',
      }),
    },
  )
}

export async function restoreCharacterVisualIdentity(
  projectId: string,
  characterId: string,
  expectedVersion: number,
  identityVersionId: string,
) {
  return requestJson<{
    character_id: string
    identity_version_id: string
    look_version_id: string
    story_state_version_id: string
    status: string
    lock_version: number
    existing_shots_preserved: boolean
  }>(`/api/v1/projects/${projectId}/characters/${characterId}/identity/restore`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      expected_version: expectedVersion,
      identity_version_id: identityVersionId,
      actor: '创作者',
    }),
  })
}

export async function applyCharacterVisualChange(
  projectId: string,
  characterId: string,
  expectedVersion: number,
  decision: 'PRESERVE_IDENTITY' | 'REGENERATE',
) {
  return requestJson<Record<string, unknown>>(
    `/api/v1/projects/${projectId}/characters/${characterId}/changes`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_version: expectedVersion,
        change_type: 'IDENTITY_MAJOR',
        payload: {},
        decision,
        actor: '创作者',
      }),
    },
  )
}

export async function lockCharacterCandidate(
  projectId: string,
  characterId: string,
  candidateId: string,
  expectedVersion: number,
) {
  const result = await requestJson<{ character: ApiCharacter; job: ApiJob }>(
    `/api/v1/projects/${projectId}/characters/${characterId}/lock`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({ expected_version: expectedVersion, candidate_id: candidateId }),
    },
  )
  return { character: mapCharacter(result.character), job: mapJob(result.job) }
}

export async function fetchPreproduction(
  projectId: string,
  signal?: AbortSignal,
): Promise<PreproductionWorkspace> {
  const data = await requestJson<{
    characters: ApiCharacter[]
    looks: Array<{
      id: string
      character_id: string
      version: number
      label: string
      usage_scope: string
      payload: Record<string, unknown>
      reference_asset_ids: string[]
      status: string
      content_hash: string
    }>
    locations: Array<{
      id: string
      key: string
      version: number
      name: string
      payload: Record<string, unknown>
      status: string
      content_hash: string
    }>
    props: Array<{
      id: string
      key: string
      version: number
      name: string
      payload: Record<string, unknown>
      status: string
      content_hash: string
    }>
    voices: Array<{
      id: string
      character_id: string
      version: number
      provider: string
      voice_key: string
      consent_status: string
      cloning_enabled: boolean
      status: string
    }>
    visual_bibles: Array<{ id: string; version: number; status: string; content_hash: string }>
  }>(`/api/v1/projects/${projectId}/preproduction`, { signal })
  return {
    characters: data.characters.map(mapCharacter),
    looks: data.looks.map((item) => ({
      id: item.id,
      characterId: item.character_id,
      version: item.version,
      label: item.label,
      usageScope: item.usage_scope,
      payload: item.payload,
      referenceAssetIds: item.reference_asset_ids,
      status: item.status,
      contentHash: item.content_hash,
    })),
    locations: data.locations.map((item) => ({
      ...item,
      contentHash: item.content_hash,
    })),
    props: data.props.map((item) => ({
      ...item,
      contentHash: item.content_hash,
    })),
    voices: data.voices.map((item) => ({
      id: item.id,
      characterId: item.character_id,
      version: item.version,
      provider: item.provider,
      voiceKey: item.voice_key,
      consentStatus: item.consent_status,
      cloningEnabled: item.cloning_enabled,
      status: item.status,
    })),
    visualBibles: data.visual_bibles.map((item) => ({
      id: item.id,
      version: item.version,
      status: item.status,
      contentHash: item.content_hash,
    })),
  }
}

export async function approvePreproduction(
  projectId: string,
  expectedVersion: number,
): Promise<Job> {
  const result = await requestJson<{ visual_bible: { id: string }; job: ApiJob }>(
    `/api/v1/projects/${projectId}/preproduction/approve`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({ expected_version: expectedVersion, actor: 'demo-user' }),
    },
  )
  return mapJob(result.job)
}

export async function fetchStoryboardWorkspace(
  projectId: string,
  signal?: AbortSignal,
): Promise<StoryboardWorkspace> {
  const data = await requestJson<{
    storyboard: null | {
      id: string
      version: number
      status: string
      episode_id: string
      script_version_id: string
      visual_bible_version_id: string
      content_hash: string
      animatic_url: string | null
    }
    shots: Array<{
      shot_spec_id: string
      shot_id: string
      code: string
      title: string
      description: string
      dialogue: string
      duration_ms: number
      shot_size: string
      camera_movement: string
      character_look_ids: string[]
      location_version_id: string | null
      prop_version_ids: string[]
      status: string
      image_url: string | null
      content_hash: string
    }>
    workflow: null | {
      id: string
      status: string
      current_gate: string | null
      nodes: Array<{
        id: string
        node_key: string
        node_type: string
        status: string
        dependencies: string[]
        degraded: boolean
      }>
    }
    gate: null | { id: string; gate_key: string; status: string; decision: string | null }
  }>(`/api/v1/projects/${projectId}/storyboard-workspace`, { signal })
  return {
    storyboard: data.storyboard === null ? null : {
      id: data.storyboard.id,
      version: data.storyboard.version,
      status: data.storyboard.status,
      episodeId: data.storyboard.episode_id,
      scriptVersionId: data.storyboard.script_version_id,
      visualBibleVersionId: data.storyboard.visual_bible_version_id,
      contentHash: data.storyboard.content_hash,
      ...(data.storyboard.animatic_url === null
        ? {}
        : { animaticUrl: data.storyboard.animatic_url }),
    },
    shots: data.shots.map((item) => ({
      shotSpecId: item.shot_spec_id,
      shotId: item.shot_id,
      code: item.code,
      title: item.title,
      description: item.description,
      dialogue: item.dialogue,
      durationMs: item.duration_ms,
      shotSize: item.shot_size,
      cameraMovement: item.camera_movement,
      characterLookIds: item.character_look_ids,
      ...(item.location_version_id === null
        ? {}
        : { locationVersionId: item.location_version_id }),
      propVersionIds: item.prop_version_ids,
      status: item.status,
      ...(item.image_url === null ? {} : { imageUrl: item.image_url }),
      contentHash: item.content_hash,
    })),
    workflow: data.workflow === null ? null : {
      id: data.workflow.id,
      status: data.workflow.status,
      ...(data.workflow.current_gate === null ? {} : { currentGate: data.workflow.current_gate }),
      nodes: data.workflow.nodes.map((item) => ({
        id: item.id,
        nodeKey: item.node_key,
        nodeType: item.node_type,
        status: item.status,
        dependencies: item.dependencies,
        degraded: item.degraded,
      })),
    },
    gate: data.gate === null ? null : {
      id: data.gate.id,
      gateKey: data.gate.gate_key,
      status: data.gate.status,
      ...(data.gate.decision === null ? {} : { decision: data.gate.decision }),
    },
  }
}

export async function approveStoryboardVersion(
  storyboardId: string,
  expectedVersion: number,
): Promise<Job> {
  const result = await requestJson<{ storyboard: { id: string }; job: ApiJob }>(
    `/api/v1/storyboards/${storyboardId}/approve`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({ expected_version: expectedVersion, actor: 'demo-user' }),
    },
  )
  return mapJob(result.job)
}

export async function fetchAudioWorkspace(
  projectId: string,
  signal?: AbortSignal,
): Promise<AudioWorkspace> {
  const data = await requestJson<{
    sound_brief: null | {
      id: string
      version: number
      status: string
      rights_status: string
      payload: Record<string, unknown>
    }
    cues: Array<{
      id: string
      type: string
      ordinal: number
      start_ms: number
      duration_ms: number
      status: string
      payload: Record<string, unknown>
      take: null | { id: string; asset_id: string; approval: string; quality_status: string }
    }>
    lip_sync: Array<{
      id: string
      shot_id: string
      approval: string
      quality_status: string
      fallback_strategy: string | null
      source_video_preserved: boolean
    }>
  }>(`/api/v1/projects/${projectId}/audio-workspace`, { signal })
  return {
    soundBrief: data.sound_brief === null ? null : {
      id: data.sound_brief.id,
      version: data.sound_brief.version,
      status: data.sound_brief.status,
      rightsStatus: data.sound_brief.rights_status,
      payload: data.sound_brief.payload,
    },
    cues: data.cues.map((item) => ({
      id: item.id,
      type: item.type,
      ordinal: item.ordinal,
      startMs: item.start_ms,
      durationMs: item.duration_ms,
      status: item.status,
      payload: item.payload,
      ...(item.take === null ? {} : { take: {
        id: item.take.id,
        assetId: item.take.asset_id,
        approval: item.take.approval,
        qualityStatus: item.take.quality_status,
      } }),
    })),
    lipSync: data.lip_sync.map((item) => ({
      id: item.id,
      shotId: item.shot_id,
      approval: item.approval,
      qualityStatus: item.quality_status,
      ...(item.fallback_strategy === null ? {} : { fallbackStrategy: item.fallback_strategy }),
      sourceVideoPreserved: item.source_video_preserved,
    })),
  }
}

export async function fetchTimelineWorkspace(
  projectId: string,
  signal?: AbortSignal,
): Promise<TimelineWorkspace> {
  const data = await requestJson<{
    timeline: null | {
      id: string
      version: number
      status: string
      duration_ms: number
      baseline_hash: string
      assets: Record<string, string | null>
    }
    tracks: Array<{
      id: string
      type: string
      name: string
      gain_db: number
      stem_asset_id: string | null
      clips: Array<{
        id: string
        source_entity_type: string
        source_entity_id: string
        asset_id: string | null
        start_ms: number
        end_ms: number
        content_hash: string
        degraded: boolean
      }>
    }>
    quality_checks: Array<{
      type: string
      status: string
      score: number | null
      findings: string[]
      evidence: Record<string, unknown>
    }>
    gate: null | { id: string; key: string; status: string }
  }>(`/api/v1/projects/${projectId}/timeline-workspace`, { signal })
  return {
    timeline: data.timeline === null ? null : {
      id: data.timeline.id,
      version: data.timeline.version,
      status: data.timeline.status,
      durationMs: data.timeline.duration_ms,
      baselineHash: data.timeline.baseline_hash,
      assets: data.timeline.assets,
    },
    tracks: data.tracks.map((track) => ({
      id: track.id,
      type: track.type,
      name: track.name,
      gainDb: track.gain_db,
      ...(track.stem_asset_id === null ? {} : { stemAssetId: track.stem_asset_id }),
      clips: track.clips.map((clip) => ({
        id: clip.id,
        sourceEntityType: clip.source_entity_type,
        sourceEntityId: clip.source_entity_id,
        ...(clip.asset_id === null ? {} : { assetId: clip.asset_id }),
        startMs: clip.start_ms,
        endMs: clip.end_ms,
        contentHash: clip.content_hash,
        degraded: clip.degraded,
      })),
    })),
    qualityChecks: data.quality_checks.map((check) => ({
      type: check.type,
      status: check.status,
      ...(check.score === null ? {} : { score: check.score }),
      findings: check.findings,
      evidence: check.evidence,
    })),
    gate: data.gate,
  }
}

function mapExportProfile(profile: {
  id: string
  project_id: string
  name: string
  version: number
  platform: string
  aspect_ratio: '9:16' | '16:9'
  width: number
  height: number
  caption_mode: 'BURNED_IN' | 'SIDECAR' | 'BOTH'
  languages: string[]
  audio_tracks: string[]
  status: string
}): ExportProfileRecord {
  return {
    id: profile.id,
    projectId: profile.project_id,
    name: profile.name,
    version: profile.version,
    platform: profile.platform,
    aspectRatio: profile.aspect_ratio,
    width: profile.width,
    height: profile.height,
    captionMode: profile.caption_mode,
    languages: profile.languages,
    audioTracks: profile.audio_tracks,
    status: profile.status,
  }
}

export async function fetchExportProfiles(
  projectId: string,
  signal?: AbortSignal,
): Promise<ExportProfileRecord[]> {
  const profiles = await requestJson<Parameters<typeof mapExportProfile>[0][]>(
    `/api/v1/projects/${projectId}/export-profiles`,
    { signal },
  )
  return profiles.map(mapExportProfile)
}

export async function createExportProfile(
  projectId: string,
  expectedVersion: number,
  input: Omit<ExportProfileRecord, 'id' | 'projectId' | 'version' | 'status'>,
): Promise<ExportProfileRecord> {
  const profile = await requestJson<Parameters<typeof mapExportProfile>[0]>(
    `/api/v1/projects/${projectId}/export-profiles`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_version: expectedVersion,
        name: input.name,
        platform: input.platform,
        aspect_ratio: input.aspectRatio,
        width: input.width,
        height: input.height,
        caption_mode: input.captionMode,
        languages: input.languages,
        audio_tracks: input.audioTracks,
        watermark: {},
        actor: 'demo-user',
      }),
    },
  )
  return mapExportProfile(profile)
}

export async function createExportMatrix(
  projectId: string,
  expectedVersion: number,
  profileIds: string[],
  languages: string[],
): Promise<Array<{ export_id: string; job_id: string; profile_id: string; language: string }>> {
  return requestJson(`/api/v1/projects/${projectId}/exports/matrix`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      expected_version: expectedVersion,
      profile_ids: profileIds,
      languages,
      actor: 'demo-user',
    }),
  })
}

export async function fetchPreviews(
  projectId: string,
  signal?: AbortSignal,
): Promise<TimelineRecord[]> {
  const timelines = await requestJson<ApiTimeline[]>(`/api/v1/projects/${projectId}/previews`, {
    signal,
  })
  return timelines.map(mapTimeline)
}

function mapTimeline(timeline: ApiTimeline): TimelineRecord {
  return {
    id: timeline.id,
    projectId: timeline.project_id,
    episodeId: timeline.episode_id,
    version: timeline.version,
    status: timeline.status,
    durationMs: timeline.duration_ms,
    baselineHash: timeline.baseline_hash,
    ...(timeline.approved_at === null ? {} : { approvedAt: timeline.approved_at }),
    assets: timeline.assets,
  }
}

function mapRevisionImpact(impact: ApiRevisionImpact): RevisionImpact {
  return {
    baseTimelineId: impact.base_timeline_id,
    scope: impact.scope,
    intent: impact.intent,
    affected: {
      shots: impact.affected.shots,
      assetTypes: impact.affected.asset_types,
      preservedHashes: impact.affected.preserved_hashes,
    },
    estimatedPoints: impact.estimated_points,
    estimatedSeconds: impact.estimated_seconds,
    requiresConfirmation: impact.requires_confirmation,
    storyDnaChanged: impact.story_dna_changed,
    touchesApproved: impact.touches_approved,
  }
}

function mapExport(record: ApiExport): ExportPackage {
  return {
    id: record.id,
    projectId: record.project_id,
    timelineId: record.timeline_id,
    status: record.status,
    profile: record.profile,
    ...(record.export_profile_id == null ? {} : { exportProfileId: record.export_profile_id }),
    language: record.language ?? 'zh-CN',
    rightsStatus: record.rights_status,
    assets: record.assets,
    createdAt: record.created_at,
    ...(record.completed_at === null ? {} : { completedAt: record.completed_at }),
  }
}

export async function analyzeRevision(
  projectId: string,
  expectedVersion: number,
  shotId: string,
  instruction: string,
): Promise<RevisionImpact> {
  const impact = await requestJson<ApiRevisionImpact>(
    `/api/v1/projects/${projectId}/revision-impact`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_version: expectedVersion,
        scope: { type: 'SHOT', ids: [shotId] },
        instruction,
      }),
    },
  )
  return mapRevisionImpact(impact)
}

export async function createRevision(
  projectId: string,
  expectedVersion: number,
  shotId: string,
  instruction: string,
) {
  const result = await requestJson<{ revision: { id: string; status: string }; job: ApiJob }>(
    `/api/v1/projects/${projectId}/revisions`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({
        expected_version: expectedVersion,
        scope: { type: 'SHOT', ids: [shotId] },
        instruction,
        confirmed: true,
      }),
    },
  )
  return { revision: result.revision, job: mapJob(result.job) }
}

export async function approvePreviewTimeline(
  timelineId: string,
  expectedVersion: number,
): Promise<TimelineRecord> {
  const timeline = await requestJson<ApiTimeline>(`/api/v1/previews/${timelineId}/approve`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({ expected_version: expectedVersion, actor: 'demo-user' }),
  })
  return mapTimeline(timeline)
}

export async function comparePreviewTimelines(
  leftId: string,
  rightId: string,
): Promise<PreviewComparison> {
  const comparison = await requestJson<ApiPreviewComparison>(
    `/api/v1/previews/${leftId}/compare/${rightId}`,
  )
  return {
    left: mapTimeline(comparison.left),
    right: mapTimeline(comparison.right),
    changedAssets: comparison.changed_assets,
    unchangedAssets: comparison.unchanged_assets,
    changedShotIds: comparison.changed_shot_ids,
    summary: comparison.summary,
  }
}

export async function rollbackPreviewTimeline(
  timelineId: string,
  expectedVersion: number,
): Promise<TimelineRecord> {
  const timeline = await requestJson<ApiTimeline>(`/api/v1/previews/${timelineId}/rollback`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({ expected_version: expectedVersion, actor: 'demo-user' }),
  })
  return mapTimeline(timeline)
}

export async function estimateProjectExport(projectId: string): Promise<ExportEstimate> {
  const estimate = await requestJson<ApiExportEstimate>(
    `/api/v1/projects/${projectId}/exports/estimate`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile: 'hybrid_720p' }),
    },
  )
  return {
    timelineId: estimate.timeline_id,
    profile: estimate.profile,
    estimatedPoints: estimate.estimated_points,
    estimatedSeconds: estimate.estimated_seconds,
    rightsStatus: estimate.rights_status,
    blocked: estimate.blocked,
    blockers: estimate.blockers,
    outputs: estimate.outputs,
  }
}

export async function createProjectExport(
  projectId: string,
  expectedVersion: number,
): Promise<{ export: ExportPackage; job: Job }> {
  const result = await requestJson<{ export: ApiExport; job: ApiJob }>(
    `/api/v1/projects/${projectId}/exports`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({
        expected_version: expectedVersion,
        profile: 'hybrid_720p',
        rights_confirmed: true,
        actor: 'demo-user',
      }),
    },
  )
  return { export: mapExport(result.export), job: mapJob(result.job) }
}

export async function fetchProjectExports(
  projectId: string,
  signal?: AbortSignal,
): Promise<ExportPackage[]> {
  const exports = await requestJson<ApiExport[]>(`/api/v1/projects/${projectId}/exports`, {
    signal,
  })
  return exports.map(mapExport)
}

export async function cancelPersistedJob(jobId: string): Promise<Job> {
  const job = await requestJson<ApiJob>(`/api/v1/jobs/${jobId}/cancel`, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  })
  return mapJob(job)
}

export async function retryPersistedJob(jobId: string): Promise<Job> {
  const job = await requestJson<ApiJob>(`/api/v1/jobs/${jobId}/retry`, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  })
  return mapJob(job)
}

export async function recoverPersistedJob(
  jobId: string,
  request: JobRecoveryRequest,
): Promise<Job> {
  const job = await requestJson<ApiJob>(`/api/v1/jobs/${jobId}/recovery`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({
      action: request.action,
      failed_part_ids: request.failedPartIds ?? [],
      model: request.model ?? null,
      strategy: request.strategy ?? null,
      additional_input: request.additionalInput ?? null,
    }),
  })
  return mapJob(job)
}

export interface ImageGenerationOptions {
  model?: string
  resolution: '1K' | '2K' | '3K' | '4K'
  aspectRatio: '1:1' | '4:3' | '3:4' | '16:9' | '9:16' | '3:2' | '2:3' | '21:9'
}

export async function generateShotTake(
  shotId: string,
  options: ImageGenerationOptions,
  idempotencyKey: string,
): Promise<Job> {
  const job = await requestJson<ApiJob>(`/api/v1/shots/${shotId}/takes`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({
      ...(options.model ? { model: options.model } : {}),
      resolution: options.resolution,
      aspect_ratio: options.aspectRatio,
    }),
  })
  return mapJob(job)
}

export interface PromptEnhanceResult {
  original: string
  enhanced: string
  provider: string
  model: string
  warning?: string
}

export async function enhanceShotPrompt(
  shotId: string,
  description: string,
): Promise<PromptEnhanceResult> {
  const result = await requestJson<{
    original: string
    enhanced: string
    provider: string
    model: string
    warning: string | null
  }>(`/api/v1/shots/${shotId}/prompt-enhance`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ description }),
  })
  return {
    original: result.original,
    enhanced: result.enhanced,
    provider: result.provider,
    model: result.model,
    ...(result.warning === null ? {} : { warning: result.warning }),
  }
}

export interface ShotVideoInput {
  prompt?: string
  image_url?: string
  duration: number
  camera_fixed: boolean
  watermark: boolean
}

export async function generateShotVideo(
  shotId: string,
  input: ShotVideoInput,
  idempotencyKey: string,
): Promise<Job> {
  const job = await requestJson<ApiJob>(`/api/v1/shots/${shotId}/video-takes`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify(input),
  })
  return mapJob(job)
}

export async function applyPersistedCandidateTake(shotId: string): Promise<void> {
  await requestJson<ApiShot>(`/api/v1/shots/${shotId}/takes/candidate/apply`, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  })
}

export async function updatePersistedShotCharacterBindings(
  shotId: string,
  expectedVersion: number,
  characterIds: string[],
  lookVersion: string,
): Promise<void> {
  await requestJson<ApiShot>(`/api/v1/shots/${shotId}/character-bindings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      expected_version: expectedVersion,
      character_ids: characterIds,
      look_version: lookVersion,
    }),
  })
}

export async function approvePersistedCandidateIdentity(shotId: string): Promise<void> {
  await requestJson<ApiShot>(`/api/v1/shots/${shotId}/takes/candidate/identity-approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ actor: 'demo-user' }),
  })
}

export async function reviewPersistedCandidateIdentity(
  shotId: string,
  payload: {
    decision: import('../types').IdentityReviewDecision
    issues: import('../types').IdentityReviewIssue[]
    note?: string
    expectedVersion: number
  },
): Promise<{ action: import('../types').IdentityReviewDecision; job?: Job }> {
  const result = await requestJson<{
    action: import('../types').IdentityReviewDecision
    shot: ApiShot
    job: ApiJob | null
  }>(`/api/v1/shots/${shotId}/takes/candidate/review`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({
      decision: payload.decision,
      issues: payload.issues,
      ...(payload.note?.trim() ? { note: payload.note.trim() } : {}),
      expected_version: payload.expectedVersion,
      actor: '创作者',
    }),
  })
  return {
    action: result.action,
    ...(result.job == null ? {} : { job: mapJob(result.job) }),
  }
}
