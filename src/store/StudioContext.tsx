import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import {
  cancelPersistedJob,
  applyPersistedCandidateTake,
  approvePersistedCandidateIdentity,
  createProjectDraft,
  deleteProjectRecord,
  fetchProjects,
  fetchWorkspace,
  generateShotTake,
  generateShotVideo,
  retryPersistedJob,
  reviewPersistedCandidateIdentity,
  updatePersistedShotCharacterBindings,
  type ImageGenerationOptions,
} from '../api/client'
import { initialAppState, PROJECT_ID } from '../data/demo'
import { prepareCurrentProjectRecovery } from './studioRecovery'
import { recommendGenre } from '../utils/briefTargetingRecommendation'
import type {
  ApiStatus,
  AppState,
  Job,
  ProjectRecord,
  ProjectSummary,
  Shot,
  IdentityReviewDecision,
  IdentityReviewIssue,
  VisualMode,
} from '../types'

const STORAGE_KEY = 'ai-short-drama-studio-v1'

interface StudioContextValue extends AppState {
  apiStatus: ApiStatus
  projectSummaries: ProjectSummary[]
  setVisualMode: (mode: VisualMode) => void
  createProject: (
    idea: string,
    idempotencyKey: string,
  ) => Promise<ProjectRecord>
  refreshProjects: () => Promise<void>
  deleteProject: (projectId: string) => Promise<void>
  activateProject: (projectId: string) => Promise<void>
  updateShot: (shotId: string, patch: Partial<Shot>) => void
  generateTake: (shotId: string, options: ImageGenerationOptions) => void
  generateVideo: (shotId: string, prompt?: string, imageUrl?: string) => void
  applyCandidateTake: (shotId: string) => void
  approveCandidateIdentity: (shotId: string) => Promise<void>
  reviewCandidateIdentity: (
    shotId: string,
    decision: IdentityReviewDecision,
    issues: IdentityReviewIssue[],
    note?: string,
  ) => Promise<void>
  updateShotCharacterBindings: (
    shotId: string,
    characterIds: string[],
    lookVersion: string,
  ) => Promise<void>
  runRevision: (shotId: string, instruction: string) => void
  approvePreview: () => void
  exportProject: () => void
  cancelJob: (jobId: string) => Promise<void>
  retryJob: (jobId: string) => Promise<void>
  resyncCurrentProject: () => Promise<void>
  resetDemo: () => void
}

const StudioContext = createContext<StudioContextValue | null>(null)

function summarizeCurrentProject(state: AppState): ProjectSummary {
  return {
    ...state.project,
    episodeCount: 1,
    sceneCount: state.project.scenes.length,
    shotCount: state.project.shots.length,
  }
}

function loadInitialState(): AppState {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      const parsed = JSON.parse(saved) as Partial<AppState>
      return {
        ...structuredClone(initialAppState),
        ...parsed,
        project: {
          ...structuredClone(initialAppState.project),
          ...parsed.project,
        },
      }
    }
  } catch {
    localStorage.removeItem(STORAGE_KEY)
  }
  return structuredClone(initialAppState)
}

function newJob(label: string, entity: string, stage: string): Job {
  const now = new Date().toISOString()
  return {
    id: `job-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    projectId: PROJECT_ID,
    jobType: 'LOCAL_MOCK',
    entityType: 'local',
    entityId: entity,
    label,
    entity,
    stage,
    status: 'RUNNING',
    progress: 12,
    attempt: 1,
    maxAttempts: 1,
    availableAt: now,
    createdAt: now,
    updatedAt: now,
    estimatedSeconds: 12,
    retryable: true,
  }
}

export function StudioProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AppState>(loadInitialState)
  const [apiStatus, setApiStatus] = useState<ApiStatus>('loading')
  const [projectSummaries, setProjectSummaries] = useState<ProjectSummary[]>(() => [
    summarizeCurrentProject(loadInitialState()),
  ])

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchWorkspace(PROJECT_ID, controller.signal),
      fetchProjects(controller.signal),
    ])
      .then(([workspace, projects]) => {
        setState((current) => ({ ...current, ...workspace }))
        setProjectSummaries(projects)
        setApiStatus('connected')
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setApiStatus('mock_fallback')
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (apiStatus !== 'connected') return
    let active = true
    let refreshInFlight = false
    const projectId = state.project.id
    const refresh = async () => {
      if (refreshInFlight) return
      refreshInFlight = true
      try {
        const workspace = await fetchWorkspace(projectId)
        if (active) setState((current) => ({ ...current, ...workspace }))
      } catch {
        // The initial workspace remains usable; the 3-second poll is the fallback path.
      } finally {
        refreshInFlight = false
      }
    }
    const interval = window.setInterval(refresh, 3000)
    const source = new EventSource(`/api/v1/projects/${projectId}/events`)
    const eventTypes = [
      'job.created',
      'job.running',
      'job.progress',
      'job.retry_wait',
      'job.cancelled',
      'job.failed',
      'job.succeeded',
      'proposal.ready',
      'story.approved',
      'characters.candidates_ready',
      'character.locked',
      'storyboards.ready',
      'hero.fallback',
      'preview.ready',
      'revision.created',
      'revision.ready',
      'preview.approved',
      'preview.rolled_back',
      'export.created',
      'export.ready',
      'shot.image_generation_started',
      'shot.image_ready',
      'shot.character_bindings_updated',
      'shot.identity_reviewed',
      'shot.video_generation_started',
      'shot.video_ready',
      'shot.take_applied',
    ]
    eventTypes.forEach((type) => source.addEventListener(type, refresh))
    return () => {
      active = false
      window.clearInterval(interval)
      source.close()
    }
  }, [apiStatus, state.project.id])

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
    document.documentElement.dataset.visualMode = state.visualMode
    const themeColor = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
    if (themeColor) themeColor.content = state.visualMode === 'cinema' ? '#090b10' : '#f5f5f7'
  }, [state])

  const setVisualMode = useCallback((visualMode: VisualMode) => {
    setState((current) => ({ ...current, visualMode }))
  }, [])

  const refreshProjects = useCallback(async () => {
    const projects = await fetchProjects()
    setProjectSummaries(projects)
    setApiStatus('connected')
  }, [])

  const activateProject = useCallback(async (projectId: string) => {
    const workspace = await fetchWorkspace(projectId)
    setState((current) => ({ ...current, ...workspace }))
    setApiStatus('connected')
  }, [])

  const deleteProject = useCallback(async (projectId: string) => {
    if (projectId === state.project.id) {
      throw new Error('当前正在编辑的项目不能删除，请先切换到其他项目。')
    }
    await deleteProjectRecord(projectId)
    setProjectSummaries((current) => current.filter((item) => item.id !== projectId))
  }, [state.project.id])

  const createProject = useCallback(async (
    idea: string,
    idempotencyKey: string,
  ) => {
    const genre = recommendGenre(idea)
    const result = await createProjectDraft(
      {
        idea,
        genre,
        style: 'realistic_cinematic',
        target_duration_sec: 60,
        aspect_ratio: '9:16',
        target_platform: 'douyin',
        reference_asset_ids: [],
        assumptions: [],
        narrative_protagonist: 'unspecified',
        target_audience: 'general',
        emotional_rewards: [],
        audience_profile: '',
        production_format: 'live_action',
      },
      idempotencyKey,
    )
    setProjectSummaries((current) => {
      const summary: ProjectSummary = {
        ...result.project,
        episodeCount: 0,
        sceneCount: 0,
        shotCount: 0,
      }
      return [summary, ...current.filter((item) => item.id !== summary.id)]
    })
    setApiStatus('connected')
    return result.project
  }, [])

  const updateShot = useCallback((shotId: string, patch: Partial<Shot>) => {
    setState((current) => ({
      ...current,
      project: {
        ...current.project,
        shots: current.project.shots.map((shot) =>
          shot.id === shotId ? { ...shot, ...patch } : shot,
        ),
        updatedAt: new Date().toISOString(),
      },
    }))
  }, [])

  const generateTake = useCallback((shotId: string, options: ImageGenerationOptions) => {
    const shot = state.project.shots.find((item) => item.id === shotId)
    const activeImageJob = state.jobs.some((job) =>
      job.entity.includes(shotId)
      && job.jobType === 'GENERATE_SHOT_IMAGE'
      && ['PENDING', 'RETRY_WAIT', 'RUNNING', 'CANCEL_REQUESTED'].includes(job.status),
    )
    if (!shot || activeImageJob) return
    const candidate = (shot.candidateTake ?? shot.currentTake) + 1
    if (apiStatus === 'connected') {
      setState((current) => ({
        ...current,
        project: {
          ...current.project,
          shots: current.project.shots.map((item) =>
            item.id === shotId
              ? { ...item, status: 'GENERATING', candidateTake: candidate }
              : item,
          ),
        },
      }))
      void generateShotTake(shotId, options, crypto.randomUUID())
        .then((job) => {
          setState((current) => ({
            ...current,
            jobs: [job, ...current.jobs.filter((item) => item.id !== job.id)],
          }))
        })
        .catch((error: unknown) => {
          const failed = newJob(
            `${shot.code} · Take V${candidate}`,
            shot.id,
            error instanceof Error ? error.message : '提交生成任务失败',
          )
          failed.status = 'FAILED'
          failed.progress = 0
          setState((current) => ({
            ...current,
            project: {
              ...current.project,
              shots: current.project.shots.map((item) =>
                item.id === shotId
                  ? { ...item, status: 'FAILED', candidateTake: undefined }
                  : item,
              ),
            },
            jobs: [failed, ...current.jobs],
          }))
        })
      return
    }
    const job = newJob(`${shot.code} · Take V${candidate}`, shot.id, '生成关键帧 · 组装动态分镜')
    setState((current) => ({
      ...current,
      project: {
        ...current.project,
        shots: current.project.shots.map((item) =>
          item.id === shotId
            ? { ...item, status: 'GENERATING', candidateTake: candidate }
            : item,
        ),
      },
      jobs: [job, ...current.jobs],
    }))

    window.setTimeout(() => {
      setState((current) => ({
        ...current,
        project: {
          ...current.project,
          shots: current.project.shots.map((item) =>
            item.id === shotId ? { ...item, status: 'PENDING_REVIEW' } : item,
          ),
        },
        jobs: current.jobs.map((item) =>
          item.id === job.id
            ? { ...item, status: 'SUCCEEDED', progress: 100, stage: '候选版本已就绪' }
            : item,
        ),
      }))
    }, 1400)
  }, [apiStatus, state.project.shots])

  const generateVideo = useCallback((shotId: string, prompt?: string, imageUrl?: string) => {
    const shot = state.project.shots.find((item) => item.id === shotId)
    if (!shot) return
    if (apiStatus !== 'connected') {
      const failed = newJob(`${shot.code} · Take V${shot.candidateTake ?? shot.currentTake} 动态视频`, shot.id, '视频生成需要连接后端 API')
      failed.status = 'FAILED'
      failed.progress = 0
      setState((current) => ({ ...current, jobs: [failed, ...current.jobs] }))
      return
    }
    void generateShotVideo(
      shotId,
      {
        ...(prompt?.trim() ? { prompt: prompt.trim() } : {}),
        ...(imageUrl?.trim() ? { image_url: imageUrl.trim() } : {}),
        duration: 5,
        camera_fixed: false,
        watermark: true,
      },
      crypto.randomUUID(),
    )
      .then((job) => {
        setState((current) => ({
          ...current,
          jobs: [job, ...current.jobs.filter((item) => item.id !== job.id)],
        }))
      })
      .catch((error: unknown) => {
        const failed = newJob(
          `${shot.code} · Take V${shot.candidateTake ?? shot.currentTake} 动态视频`,
          shot.id,
          error instanceof Error ? error.message : '提交视频生成任务失败',
        )
        failed.status = 'FAILED'
        failed.progress = 0
        setState((current) => ({ ...current, jobs: [failed, ...current.jobs] }))
      })
  }, [apiStatus, state.project.shots])

  const applyCandidateTake = useCallback((shotId: string) => {
    if (apiStatus === 'connected') {
      void applyPersistedCandidateTake(shotId)
        .then(() => fetchWorkspace(state.project.id))
        .then((workspace) => setState((current) => ({ ...current, ...workspace })))
      return
    }
    setState((current) => ({
      ...current,
      project: {
        ...current.project,
        shots: current.project.shots.map((shot) =>
          shot.id === shotId && shot.candidateTake
            ? {
                ...shot,
                currentTake: shot.candidateTake,
                candidateTake: undefined,
                status: 'APPROVED',
              }
            : shot,
        ),
        timelineVersion: current.project.timelineVersion + 1,
        status: 'PREVIEW_READY',
      },
    }))
  }, [apiStatus, state.project.id])

  const approveCandidateIdentity = useCallback(async (shotId: string) => {
    if (apiStatus !== 'connected') return
    await approvePersistedCandidateIdentity(shotId)
    const workspace = await fetchWorkspace(state.project.id)
    setState((current) => ({ ...current, ...workspace }))
  }, [apiStatus, state.project.id])

  const reviewCandidateIdentity = useCallback(async (
    shotId: string,
    decision: IdentityReviewDecision,
    issues: IdentityReviewIssue[],
    note?: string,
  ) => {
    if (apiStatus !== 'connected') {
      throw new Error('连接后端后才能完成角色一致性复核')
    }
    const shot = state.project.shots.find((item) => item.id === shotId)
    if (!shot) throw new Error('没有找到要复核的镜头')
    const result = await reviewPersistedCandidateIdentity(shotId, {
      decision,
      issues,
      ...(note === undefined ? {} : { note }),
      expectedVersion: shot.lockVersion ?? 1,
    })
    const workspace = await fetchWorkspace(state.project.id)
    setState((current) => ({
      ...current,
      ...workspace,
      jobs: result.job
        ? [result.job, ...workspace.jobs.filter((item) => item.id !== result.job?.id)]
        : workspace.jobs,
    }))
  }, [apiStatus, state.project.id, state.project.shots])

  const updateShotCharacterBindings = useCallback(async (
    shotId: string,
    characterIds: string[],
    lookVersion: string,
  ) => {
    if (apiStatus !== 'connected') return
    const shot = state.project.shots.find((item) => item.id === shotId)
    if (!shot) return
    await updatePersistedShotCharacterBindings(
      shotId,
      shot.lockVersion ?? 1,
      characterIds,
      lookVersion,
    )
    const workspace = await fetchWorkspace(state.project.id)
    setState((current) => ({ ...current, ...workspace }))
  }, [apiStatus, state.project.id, state.project.shots])

  const runRevision = useCallback((shotId: string, instruction: string) => {
    const shot = state.project.shots.find((item) => item.id === shotId)
    if (!shot) return
    const job = newJob(`${shot.code} · 局部修改`, shot.id, `解析：${instruction.slice(0, 18)}`)
    setState((current) => ({
      ...current,
      jobs: [job, ...current.jobs],
      project: {
        ...current.project,
        shots: current.project.shots.map((item) =>
          item.id === shotId ? { ...item, status: 'GENERATING' } : item,
        ),
      },
    }))
    window.setTimeout(() => {
      setState((current) => ({
        ...current,
        jobs: current.jobs.map((item) =>
          item.id === job.id
            ? { ...item, status: 'SUCCEEDED', progress: 100, stage: '时间线已重组' }
            : item,
        ),
        project: {
          ...current.project,
          timelineVersion: current.project.timelineVersion + 1,
          status: 'PREVIEW_READY',
          previewApproved: false,
          shots: current.project.shots.map((item) =>
            item.id === shotId
              ? {
                  ...item,
                  status: 'PENDING_REVIEW',
                  candidateTake: (item.candidateTake ?? item.currentTake) + 1,
                }
              : item,
          ),
        },
      }))
    }, 1600)
  }, [state.project.shots])

  const approvePreview = useCallback(() => {
    setState((current) => ({
      ...current,
      project: {
        ...current.project,
        previewApproved: true,
        status: 'APPROVED',
      },
    }))
  }, [])

  const exportProject = useCallback(() => {
    if (!state.project.previewApproved || state.project.exportReady) return
    const job = newJob(
      `Export · Timeline V${state.project.timelineVersion}`,
      state.project.id,
      '权利预检 · 媒体封装',
    )
    setState((current) => ({ ...current, jobs: [job, ...current.jobs] }))
    window.setTimeout(() => {
      setState((current) => ({
        ...current,
        jobs: current.jobs.map((item) =>
          item.id === job.id
            ? { ...item, status: 'SUCCEEDED', progress: 100, stage: '导出清单已就绪' }
            : item,
        ),
        project: { ...current.project, exportReady: true, status: 'EXPORTED' },
      }))
    }, 1300)
  }, [state.project.exportReady, state.project.id, state.project.previewApproved, state.project.timelineVersion])

  const cancelJob = useCallback(async (jobId: string) => {
    if (apiStatus === 'connected' && !jobId.startsWith('job-')) {
      const updated = await cancelPersistedJob(jobId)
      setState((current) => ({
        ...current,
        jobs: current.jobs.map((job) => job.id === jobId ? updated : job),
      }))
      return
    }
    setState((current) => ({
      ...current,
      jobs: current.jobs.map((job) =>
        job.id === jobId && job.status === 'RUNNING'
          ? { ...job, status: 'CANCELLED', progress: job.progress, stage: '已取消未开始的步骤' }
          : job,
      ),
    }))
  }, [apiStatus])

  const retryJob = useCallback(async (jobId: string) => {
    if (apiStatus === 'connected' && !jobId.startsWith('job-')) {
      const updated = await retryPersistedJob(jobId)
      setState((current) => ({
        ...current,
        jobs: current.jobs.map((job) => job.id === jobId ? updated : job),
      }))
      return
    }
    setState((current) => ({
      ...current,
      jobs: current.jobs.map((job) =>
        job.id === jobId && (job.status === 'FAILED' || job.status === 'CANCELLED')
          ? { ...job, status: 'RUNNING', progress: 18, stage: '恢复中' }
          : job,
      ),
    }))
  }, [apiStatus])

  const resyncCurrentProject = useCallback(async () => {
    const { workspace, projects } = await prepareCurrentProjectRecovery({
      projectId: state.project.id,
      fetchCurrentWorkspace: fetchWorkspace,
      fetchProjectSummaries: fetchProjects,
      clearLocalCache: () => localStorage.removeItem(STORAGE_KEY),
    })
    setState((current) => ({ ...current, ...workspace }))
    setProjectSummaries(projects)
    setApiStatus('connected')
  }, [state.project.id])

  const resetDemo = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    setState(structuredClone(initialAppState))
  }, [])

  const value = useMemo<StudioContextValue>(
    () => ({
      ...state,
      apiStatus,
      projectSummaries,
      setVisualMode,
      createProject,
      refreshProjects,
      deleteProject,
      activateProject,
      updateShot,
      generateTake,
      generateVideo,
      applyCandidateTake,
      approveCandidateIdentity,
      reviewCandidateIdentity,
      updateShotCharacterBindings,
      runRevision,
      approvePreview,
      exportProject,
      cancelJob,
      retryJob,
      resyncCurrentProject,
      resetDemo,
    }),
    [
      state,
      apiStatus,
      projectSummaries,
      setVisualMode,
      createProject,
      refreshProjects,
      deleteProject,
      activateProject,
      updateShot,
      generateTake,
      generateVideo,
      applyCandidateTake,
      approveCandidateIdentity,
      reviewCandidateIdentity,
      updateShotCharacterBindings,
      runRevision,
      approvePreview,
      exportProject,
      cancelJob,
      retryJob,
      resyncCurrentProject,
      resetDemo,
    ],
  )

  return <StudioContext.Provider value={value}>{children}</StudioContext.Provider>
}

export function useStudio(): StudioContextValue {
  const context = useContext(StudioContext)
  if (!context) throw new Error('useStudio must be used inside StudioProvider')
  return context
}
