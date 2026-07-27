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
  reorderPersistedSceneShots,
  retryPersistedJob,
  reviewPersistedCandidateIdentity,
  updatePersistedShotCharacterBindings,
  updatePersistedShot,
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
  ThemeMode,
  VisualMode,
} from '../types'

const PREFERENCES_KEY = 'ai-short-drama-studio-preferences-v2'
const LEGACY_STORAGE_KEY = 'ai-short-drama-studio-v1'

interface StudioContextValue extends AppState {
  apiStatus: ApiStatus
  projectSummaries: ProjectSummary[]
  setVisualMode: (mode: VisualMode) => void
  themeMode: ThemeMode
  resolvedTheme: 'light' | 'dark'
  setThemeMode: (mode: ThemeMode) => void
  createProject: (
    idea: string,
    idempotencyKey: string,
  ) => Promise<ProjectRecord>
  refreshProjects: () => Promise<void>
  deleteProject: (projectId: string) => Promise<void>
  activateProject: (projectId: string) => Promise<void>
  updateShot: (shotId: string, patch: Partial<Shot>) => Promise<void>
  reorderSceneShots: (sceneId: string, orderedShotIds: string[]) => Promise<void>
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
  cancelJob: (jobId: string) => Promise<void>
  retryJob: (jobId: string) => Promise<void>
  resyncCurrentProject: () => Promise<void>
  resetDemo: () => void
}

const StudioContext = createContext<StudioContextValue | null>(null)

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export interface StudioPreferences {
  visualMode: VisualMode
  themeMode: ThemeMode
}

export function normalizeStudioPreferences(value: unknown): StudioPreferences {
  if (!isRecord(value)) return { visualMode: initialAppState.visualMode, themeMode: 'system' }
  const visualMode = value.visualMode
  const themeMode = value.themeMode
  const restoredLegacyCinemaTheme = visualMode === 'cinema' && themeMode === undefined
  return {
    visualMode:
      visualMode === 'focus' || visualMode === 'cinema' || visualMode === 'standard'
        ? visualMode
        : initialAppState.visualMode,
    themeMode: themeMode === 'light' || themeMode === 'dark' || themeMode === 'system'
      ? themeMode
      : restoredLegacyCinemaTheme ? 'dark' : 'system',
  }
}

function loadStudioPreferences(): StudioPreferences {
  try {
    const saved = localStorage.getItem(PREFERENCES_KEY)
    const legacy = localStorage.getItem(LEGACY_STORAGE_KEY)
    return normalizeStudioPreferences(JSON.parse(saved ?? legacy ?? '{}'))
  } catch {
    localStorage.removeItem(PREFERENCES_KEY)
    localStorage.removeItem(LEGACY_STORAGE_KEY)
    return { visualMode: initialAppState.visualMode, themeMode: 'system' }
  }
}

function summarizeCurrentProject(state: AppState): ProjectSummary {
  return {
    ...state.project,
    episodeCount: 1,
    sceneCount: state.project.scenes.length,
    shotCount: state.project.shots.length,
  }
}

function loadInitialState(): AppState {
  const state = structuredClone(initialAppState)
  return { ...state, visualMode: loadStudioPreferences().visualMode }
}

function newJob(label: string, entity: string, stage: string): Job {
  const now = new Date().toISOString()
  return {
    id: `job-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    projectId: PROJECT_ID,
    projectName: initialAppState.project.name,
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
  const [themeMode, setThemeModeState] = useState<ThemeMode>(() => loadStudioPreferences().themeMode)
  const [systemTheme, setSystemTheme] = useState<'light' | 'dark'>('light')
  const [apiStatus, setApiStatus] = useState<ApiStatus>('loading')
  const [projectSummaries, setProjectSummaries] = useState<ProjectSummary[]>(() => [
    summarizeCurrentProject(initialAppState),
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
      'domain.command.executed',
    ]
    eventTypes.forEach((type) => source.addEventListener(type, refresh))
    const refreshProjectThumbnails = () => {
      void fetchProjects()
        .then((projects) => {
          if (active) setProjectSummaries(projects)
        })
        .catch(() => undefined)
    }
    source.addEventListener('project.thumbnail_ready', refreshProjectThumbnails)
    return () => {
      active = false
      window.clearInterval(interval)
      source.close()
    }
  }, [apiStatus, state.project.id])

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const syncSystemTheme = () => setSystemTheme(mediaQuery.matches ? 'dark' : 'light')
    syncSystemTheme()
    mediaQuery.addEventListener('change', syncSystemTheme)
    return () => mediaQuery.removeEventListener('change', syncSystemTheme)
  }, [])

  const resolvedTheme = themeMode === 'system' ? systemTheme : themeMode

  useEffect(() => {
    localStorage.setItem(
      PREFERENCES_KEY,
      JSON.stringify({ visualMode: state.visualMode, themeMode } satisfies StudioPreferences),
    )
    localStorage.removeItem(LEGACY_STORAGE_KEY)
    document.documentElement.dataset.visualMode = state.visualMode
    document.documentElement.dataset.theme = resolvedTheme
    const themeColor = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')
    if (themeColor) themeColor.content = resolvedTheme === 'dark' ? '#090b10' : '#f5f5f7'
  }, [resolvedTheme, state.visualMode, themeMode])

  const setVisualMode = useCallback((visualMode: VisualMode) => {
    setState((current) => ({ ...current, visualMode }))
  }, [])

  const setThemeMode = useCallback((mode: ThemeMode) => {
    setThemeModeState(mode)
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

  const updateShot = useCallback(async (shotId: string, patch: Partial<Shot>) => {
    if (apiStatus !== 'connected') {
      throw new Error('当前未连接项目服务，镜头修改未保存。')
    }
    const shot = state.project.shots.find((item) => item.id === shotId)
    if (!shot) throw new Error('找不到要修改的镜头。')
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
    try {
      await updatePersistedShot(shotId, shot.lockVersion ?? 1, patch)
      const workspace = await fetchWorkspace(state.project.id)
      setState((current) => ({ ...current, ...workspace }))
    } catch (error) {
      const workspace = await fetchWorkspace(state.project.id).catch(() => null)
      if (workspace) setState((current) => ({ ...current, ...workspace }))
      throw error
    }
  }, [apiStatus, state.project.id, state.project.shots])

  const reorderSceneShots = useCallback(async (
    sceneId: string,
    orderedShotIds: string[],
  ) => {
    if (apiStatus !== 'connected') {
      throw new Error('当前未连接项目服务，镜头排序未保存。')
    }
    const scene = state.project.scenes.find((item) => item.id === sceneId)
    if (!scene) throw new Error('找不到要排序的场景。')
    const validIds = new Set(scene.shotIds)
    if (
      orderedShotIds.length !== scene.shotIds.length
      || !orderedShotIds.every((id) => validIds.has(id))
    ) {
      throw new Error('镜头排序范围无效，请刷新后重试。')
    }
    const ordinalById = new Map(orderedShotIds.map((id, index) => [id, index + 1]))
    setState((current) => ({
      ...current,
      project: {
        ...current.project,
        scenes: current.project.scenes.map((item) => (
          item.id === sceneId ? { ...item, shotIds: orderedShotIds } : item
        )),
        shots: current.project.shots.map((shot) => (
          shot.sceneId === sceneId && ordinalById.has(shot.id)
            ? { ...shot, ordinal: ordinalById.get(shot.id)! }
            : shot
        )),
        updatedAt: new Date().toISOString(),
      },
    }))
    try {
      await reorderPersistedSceneShots(
        sceneId,
        state.project.lockVersion,
        orderedShotIds,
      )
      const workspace = await fetchWorkspace(state.project.id)
      setState((current) => ({ ...current, ...workspace }))
    } catch (error) {
      const workspace = await fetchWorkspace(state.project.id).catch(() => null)
      if (workspace) setState((current) => ({ ...current, ...workspace }))
      throw error
    }
  }, [
    apiStatus,
    state.project.id,
    state.project.lockVersion,
    state.project.scenes,
  ])

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
          void fetchWorkspace(state.project.id)
            .then((workspace) => {
              setState((current) => ({
                ...current,
                ...workspace,
                jobs: [failed, ...workspace.jobs],
              }))
            })
            .catch(() => {
              setState((current) => ({ ...current, jobs: [failed, ...current.jobs] }))
            })
        })
      return
    }
    const failed = newJob(`${shot.code} · Take V${candidate}`, shot.id, '图片生成需要连接后端 API')
    failed.status = 'FAILED'
    failed.progress = 0
    setState((current) => ({ ...current, jobs: [failed, ...current.jobs] }))
  }, [apiStatus, state.project.id, state.project.shots])

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
    const shot = state.project.shots.find((item) => item.id === shotId)
    if (!shot) return
    const failed = newJob(`${shot.code} · 应用候选版本`, shot.id, '应用候选版本需要连接后端 API')
    failed.status = 'FAILED'
    failed.progress = 0
    setState((current) => ({ ...current, jobs: [failed, ...current.jobs] }))
  }, [apiStatus, state.project.id, state.project.shots])

  const approveCandidateIdentity = useCallback(async (shotId: string) => {
    if (apiStatus !== 'connected') throw new Error('连接后端后才能确认角色一致性')
    const shot = state.project.shots.find((item) => item.id === shotId)
    if (!shot) throw new Error('没有找到要确认的镜头')
    await approvePersistedCandidateIdentity(shotId, shot.lockVersion ?? 1)
    const workspace = await fetchWorkspace(state.project.id)
    setState((current) => ({ ...current, ...workspace }))
  }, [apiStatus, state.project.id, state.project.shots])

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
      clearLocalCache: () => localStorage.removeItem(LEGACY_STORAGE_KEY),
    })
    setState((current) => ({ ...current, ...workspace }))
    setProjectSummaries(projects)
    setApiStatus('connected')
  }, [state.project.id])

  const resetDemo = useCallback(() => {
    localStorage.removeItem(PREFERENCES_KEY)
    localStorage.removeItem(LEGACY_STORAGE_KEY)
    setState(structuredClone(initialAppState))
    setThemeModeState('system')
  }, [])

  const value = useMemo<StudioContextValue>(
    () => ({
      ...state,
      apiStatus,
      projectSummaries,
      setVisualMode,
      themeMode,
      resolvedTheme,
      setThemeMode,
      createProject,
      refreshProjects,
      deleteProject,
      activateProject,
      updateShot,
      reorderSceneShots,
      generateTake,
      generateVideo,
      applyCandidateTake,
      approveCandidateIdentity,
      reviewCandidateIdentity,
      updateShotCharacterBindings,
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
      themeMode,
      resolvedTheme,
      setThemeMode,
      createProject,
      refreshProjects,
      deleteProject,
      activateProject,
      updateShot,
      reorderSceneShots,
      generateTake,
      generateVideo,
      applyCandidateTake,
      approveCandidateIdentity,
      reviewCandidateIdentity,
      updateShotCharacterBindings,
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
