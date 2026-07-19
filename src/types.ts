export type VisualMode = 'standard' | 'focus' | 'cinema'
export type ApiStatus = 'loading' | 'connected' | 'mock_fallback'
export type NarrativeProtagonist = 'unspecified' | 'male' | 'female' | 'dual' | 'ensemble'
export type TargetAudience = 'male_frequency' | 'female_frequency' | 'general'
export type EmotionalReward =
  | 'romance' | 'identity' | 'career' | 'revenge' | 'family' | 'power' | 'public_mission'
export type ProductionFormat = 'live_action' | 'ai_comic' | 'high_concept_fantasy'

export interface NarrativeTargeting {
  narrativeProtagonist: NarrativeProtagonist
  targetAudience: TargetAudience
  emotionalRewards: EmotionalReward[]
  audienceProfile: string
  productionFormat: ProductionFormat
}
export type ProjectStatus =
  | 'DRAFT'
  | 'PROPOSAL_RUNNING'
  | 'PROPOSAL_READY'
  | 'STORY_STRUCTURE_RUNNING'
  | 'RELATIONSHIP_READY'
  | 'CHARACTER_VISUAL_READY'
  | 'SCRIPT_PACKAGE_RUNNING'
  | 'STORY_PACKAGE_RUNNING'
  | 'SCRIPT_READY'
  | 'STORY_APPROVED'
  | 'PREPRODUCTION_READY'
  | 'PREPRODUCTION_APPROVED'
  | 'STORYBOARD_READY'
  | 'STORYBOARD_APPROVED'
  | 'CHARACTER_LOCKED'
  | 'PRODUCING'
  | 'PREVIEW_READY'
  | 'APPROVED'
  | 'EXPORTING'
  | 'EXPORTED'
  | 'BLOCKED'
  | 'ARCHIVED'

export type ShotStatus =
  | 'DRAFT'
  | 'READY'
  | 'QUEUED'
  | 'GENERATING'
  | 'GENERATED'
  | 'PENDING_REVIEW'
  | 'APPROVED'
  | 'FAILED'
  | 'BLOCKED'

export type JobStatus =
  | 'PENDING'
  | 'RETRY_WAIT'
  | 'RUNNING'
  | 'CANCEL_REQUESTED'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'

export interface Shot {
  id: string
  sceneId: string
  code: string
  ordinal: number
  title: string
  description: string
  dialogue: string
  durationSec: number
  status: ShotStatus
  shotSize: 'WS' | 'MS' | 'MCU' | 'CU'
  cameraMovement: 'STATIC' | 'PAN' | 'DOLLY_IN' | 'TRACK' | 'HANDHELD'
  currentTake: number
  candidateTake?: number
  continuity: 'CLEAR' | 'NOTICE' | 'RISK'
  location: string
  timeOfDay: string
  currentImageUrl?: string
  candidateImageUrl?: string
  currentImageModel?: string
  candidateImageModel?: string
  currentVideoUrl?: string
  candidateVideoUrl?: string
  lockVersion?: number
  characterIds?: string[]
  characterLookVersion?: string
  characterIdentityVersionIds?: string[]
  characterLookVersionIds?: string[]
  characterStoryStateVersionIds?: string[]
  characterBindings?: ShotCharacterBinding[]
  currentIdentityStatus?: IdentityStatus
  candidateIdentityStatus?: IdentityStatus
  candidateIdentityScore?: number
  candidateIdentityMessage?: string
  currentIdentityReview?: IdentityReviewRecord
  candidateIdentityReview?: IdentityReviewRecord
  latestIdentityReview?: IdentityReviewRecord
}

export type IdentityStatus = 'PASSED' | 'REVIEW_REQUIRED' | 'NOT_APPLICABLE'
export type IdentityReviewDecision = 'APPROVE_AND_APPLY' | 'REGENERATE' | 'OVERRIDE_AND_APPLY'
export type IdentityReviewIssue =
  | 'FACE_SHAPE'
  | 'FACIAL_FEATURES'
  | 'HAIR'
  | 'AGE_IMPRESSION'
  | 'WARDROBE'
  | 'BODY_PROPORTIONS'
  | 'SIGNATURE_ACCESSORIES'

export interface IdentityReviewRecord {
  decision: IdentityReviewDecision
  issues: IdentityReviewIssue[]
  note?: string
  actor: string
  reviewedAt: string
  score?: number
  referenceAssetIds: string[]
  lookVersion?: string
}

export interface ShotCharacterBinding {
  id: string
  name: string
  role: string
  visualBrief: string
  lookVersion: string
  lockedCandidateId: string
  referenceAssetId: string
  referenceAssetUrl: string
  identityVersionId?: string
  lookVersionId?: string
  storyStateVersionId?: string
}

export interface Scene {
  id: string
  code: string
  title: string
  purpose: string
  durationSec: number
  status: 'READY' | 'IN_PROGRESS' | 'PENDING_REVIEW' | 'APPROVED'
  shotIds: string[]
}

export interface Job {
  id: string
  projectId: string
  jobType: string
  entityType: string
  entityId: string
  label: string
  entity: string
  status: JobStatus
  progress: number
  stage: string
  attempt: number
  maxAttempts: number
  availableAt: string
  heartbeatAt?: string
  createdAt: string
  updatedAt: string
  completedAt?: string
  estimatedSeconds?: number
  retryable?: boolean
  errorCode?: string
  errorMessage?: string
  errorDetails?: Record<string, unknown>
}

export type JobRecoveryAction =
  | 'RESUME_FROM_FAILURE'
  | 'RETRY_FAILED_PARTS'
  | 'SWITCH_MODEL'
  | 'FALLBACK_EXECUTION'
  | 'SAVE_INTERMEDIATE'
  | 'PROVIDE_INPUT'
  | 'ESCALATE_HUMAN'

export interface JobRecoveryRequest {
  action: JobRecoveryAction
  failedPartIds?: string[]
  model?: string
  strategy?: string
  additionalInput?: string
  note?: string
}

export interface ProjectRecord {
  id: string
  name: string
  idea: string
  genre: string
  style: string
  targetDurationSec: number
  aspectRatio: '9:16' | '16:9'
  targetPlatform: string
  status: ProjectStatus
  lockVersion: number
  availablePoints: number
  timelineVersion: number
  previewApproved: boolean
  exportReady: boolean
  createdAt: string
  updatedAt: string
}

export interface ProjectSummary extends ProjectRecord {
  episodeCount: number
  sceneCount: number
  shotCount: number
}

export type ProjectWorkflowMode = 'CLASSIC' | 'PIPELINE' | 'HYBRID'
export type ProjectStageStatus = 'COMPLETE' | 'CURRENT' | 'IN_PROGRESS' | 'BLOCKED' | 'LOCKED'

export interface ProjectStage {
  key: string
  label: string
  status: ProjectStageStatus
  href: string
  detail: string
}

export interface ProjectReadinessBlocker {
  code: string
  message: string
  actionLabel: string
  actionHref: string
}

export interface ProjectReadiness {
  projectId: string
  workflowMode: ProjectWorkflowMode
  projectStatus: ProjectStatus
  summaryStatus: 'READY' | 'IN_PROGRESS' | 'ACTION_REQUIRED' | 'BLOCKED'
  activeStageKey: string
  activeJobCount: number
  stages: ProjectStage[]
  blockers: ProjectReadinessBlocker[]
  nextActionLabel: string
  nextActionHref: string
  updatedAt: string
}

export interface PlatformTarget {
  platform: string
  priority: 'PRIMARY' | 'SECONDARY'
  aspectRatio: '9:16' | '16:9'
  targetDurationSec: number
  captionMode: 'BURNED_IN' | 'SIDECAR' | 'BOTH'
}

export interface BriefVersionRecord {
  id: string
  projectId: string
  version: number
  projectName: string
  rawInput: string
  genre: string
  style: string
  targetDurationSec: number
  aspectRatio: '9:16' | '16:9'
  targetPlatform: string
  referenceAssetIds: string[]
  assumptions: string[]
  narrativeProtagonist: NarrativeProtagonist
  targetAudience: TargetAudience
  emotionalRewards: EmotionalReward[]
  audienceProfile: string
  productionFormat: ProductionFormat
  primaryAudience: string
  secondaryAudiences: string[]
  primaryMarket: string
  secondaryMarkets: string[]
  canonicalLanguage: string
  localizationTargets: string[]
  platformTargets: PlatformTarget[]
  contentRequirements: string[]
  contentAvoidances: string[]
  creativeDefaults: Record<string, string | number | boolean>
  blockingQuestions: string[]
  payloadSchemaVersion: string
  contentHash: string
  status: string
  createdAt: string
}

export interface ProjectState extends ProjectRecord {
  episodeId: string
  scenes: Scene[]
  shots: Shot[]
}

export interface AppState {
  project: ProjectState
  jobs: Job[]
  visualMode: VisualMode
}

export interface DirectorProposal {
  id: string
  projectId: string
  version: number
  briefVersion: number
  directionKey: string
  sourceProposalIds: string[]
  schemaVersion: string
  narrativeTargeting?: NarrativeTargeting
  differentiator?: string
  audienceFit?: string
  visualSignature?: string
  selectionTradeoff?: string
  keyTurns?: string[]
  riskNotes?: string[]
  sequelSetup?: {
    currentArcClosure: string
    finalRevealOrAction: string
    nextInstallmentConflict: string
    nextInstallmentObjective: string
  }
  storyDna?: {
    core_premise: string
    protagonist_want: string
    protagonist_need: string
    central_conflict: string
    stakes?: string
    emotional_promise: string
    payoff?: string
    ending_hook: string
    tone_keywords: string[]
  }
  briefCompliance?: {
    status: 'ALL_MET' | 'PARTIAL' | 'CONFLICT'
    items: Array<{
      category: 'REQUIREMENT' | 'AVOIDANCE'
      item: string
      status: 'MET' | 'PARTIAL' | 'CONFLICT'
      evidence: string
    }>
  }
  productionComplexity?: {
    characterCount: number
    sceneCount: number
    exteriorSceneCount: number
    exteriorRequirements: string[]
    vfxRequirements: string[]
    estimatedGeneration: {
      keyframeImages: number
      videoClips: number
      voiceSegments: number
    }
  }
  firstEpisodeRhythm?: {
    opening3sHook: string
    firstPayoff: string
    endingAction: string
  }
  aiRecommendation?: {
    recommended: boolean
    briefMatches: string[]
    reason: string
  }
  title: string
  logline: string
  directorStatement: string
  totalDurationSec: number
  scenes: Array<{
    code: string
    title: string
    purpose: string
    durationSec: number
    shots: Array<{ code: string; durationSec: number; shotSize: string; camera: string }>
  }>
  assumptions: string[]
  provider: string
  status: string
}

export interface CharacterCandidate {
  id: string
  ordinal: number
  assetId: string
  assetUrl: string
  seed: string
  status: string
  selected: boolean
}

export interface CharacterRecord {
  id: string
  projectId: string
  characterKey: string
  name: string
  role: string
  visualBrief: string
  status: string
  lockedCandidateId?: string
  lockVersion: number
  candidates: CharacterCandidate[]
}

export interface TimelineRecord {
  id: string
  projectId: string
  episodeId: string
  version: number
  status: string
  durationMs: number
  baselineHash: string
  approvedAt?: string
  assets: {
    mp4: string
    srt: string
    vtt: string
    manifest: string
    stems_manifest?: string
    qc_report?: string
  }
}

export interface RevisionImpact {
  baseTimelineId: string
  scope: { type: 'SHOT' | 'SCENE' | 'PROJECT'; ids: string[] }
  intent: { type: string; instruction: string }
  affected: {
    shots: string[]
    assetTypes: string[]
    preservedHashes: string[]
  }
  estimatedPoints: number
  estimatedSeconds: number
  requiresConfirmation: boolean
  storyDnaChanged: boolean
  touchesApproved: boolean
}

export interface PreviewComparison {
  left: TimelineRecord
  right: TimelineRecord
  changedAssets: string[]
  unchangedAssets: string[]
  changedShotIds: string[]
  summary: string
}

export interface ExportEstimate {
  timelineId: string
  profile: string
  estimatedPoints: number
  estimatedSeconds: number
  rightsStatus: string
  blocked: boolean
  blockers: string[]
  outputs: string[]
}

export interface ExportPackage {
  id: string
  projectId: string
  timelineId: string
  status: string
  profile: string
  exportProfileId?: string
  language: string
  rightsStatus: string
  assets: Partial<{
    mp4: string
    srt: string
    vtt: string
    manifest: string
    cover: string
    stems_manifest: string
    qc_report: string
  }>
  createdAt: string
  completedAt?: string
}
