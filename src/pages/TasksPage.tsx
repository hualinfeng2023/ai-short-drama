import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowRight, Ban, Clock3, Filter, LoaderCircle, RotateCcw, X } from 'lucide-react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import {
  cancelPersistedJob,
  fetchJobs,
  fetchProjectJobs,
  recoverPersistedJob,
} from '../api/client'
import { JobRecoveryPanel } from '../components/JobRecoveryPanel'
import { Button, PageHeader, ProgressBar, SelectControl, StatusBadge, Tab, TabList, Toolbar } from '../components/ui'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'
import type { Job, JobRecoveryAction, JobRecoveryRequest, JobStatus } from '../types'
import { getCompletedJobCta, getFailedJobGuidance } from '../utils/jobCta'
import { elapsedJobSeconds, formatElapsedTime } from '../utils/jobTiming'
import { localizeDisplayText } from '../utils/localizeDisplayText'

const ACTIVE_JOB_STATUSES = new Set<JobStatus>([
  'PENDING',
  'RETRY_WAIT',
  'RUNNING',
  'CANCEL_REQUESTED',
])

const eventTypes = [
  'job.created',
  'job.running',
  'job.progress',
  'job.diagnostics',
  'job.retry_wait',
  'job.cancel_requested',
  'job.cancelled',
  'job.failed',
  'job.succeeded',
  'job.recovery_requested',
  'job.intermediate_saved',
  'proposal.ready',
  'story.approved',
  'characters.candidates_ready',
  'character.locked',
  'storyboards.ready',
  'preview.ready',
  'revision.created',
  'revision.ready',
  'preview.approved',
  'preview.rolled_back',
  'export.created',
  'export.ready',
  'preproduction.approved',
  'storyboard.planned',
  'animatic.ready',
  'storyboard.approved',
  'media_production.started',
  'audio.pipeline_planned',
  'audio.take_ready',
  'lip_sync.batch_ready',
  'timeline.multitrack_ready',
  'delivery.export_ready',
]

function createdLabel(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', {
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
}

function taskDisplayLabel(job: Job) {
  const label = localizeDisplayText(job.label)
  const projectPrefix = `${localizeDisplayText(job.projectName)} · `
  return label.startsWith(projectPrefix) ? label.slice(projectPrefix.length) : label
}

export function TasksPage() {
  const navigate = useNavigate()
  const { notify } = useToast()
  const { project, projectSummaries, jobs: contextJobs, apiStatus } = useStudio()
  const [searchParams] = useSearchParams()
  const projectId = searchParams.get('project')
  const requestedJobType = searchParams.get('jobType')
  const focusedJobType = requestedJobType === 'GENERATE_STORY_PACKAGE'
    || requestedJobType === 'GENERATE_STORY_DIRECTIONS'
    || requestedJobType === 'GENERATE_STORY_STRUCTURE'
    || requestedJobType === 'GENERATE_SCRIPT_PACKAGE'
    ? requestedJobType
    : null
  const [jobs, setJobs] = useState<Job[]>(projectId === project.id ? contextJobs : [])
  const [filter, setFilter] = useState<'ALL' | JobStatus>('ALL')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actingJobId, setActingJobId] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())

  const refresh = useCallback(async () => {
    try {
      const latest = projectId ? await fetchProjectJobs(projectId) : await fetchJobs()
      setJobs(latest)
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '任务读取失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    if (apiStatus !== 'connected') {
      setJobs(projectId === project.id || projectId === null ? contextJobs : [])
      setLoading(false)
    }
  }, [apiStatus, contextJobs, project.id, projectId])

  useEffect(() => {
    if (apiStatus !== 'connected') return
    let refreshInFlight = false
    const refreshLatest = async () => {
      if (refreshInFlight) return
      refreshInFlight = true
      try {
        await refresh()
      } finally {
        refreshInFlight = false
      }
    }
    setLoading(true)
    void refreshLatest()
    const interval = window.setInterval(refreshLatest, 3000)
    const source = projectId ? new EventSource(`/api/v1/projects/${projectId}/events`) : null
    if (source) {
      eventTypes.forEach((type) => source.addEventListener(type, refreshLatest))
      source.onerror = () => {
        // Polling stays active as the documented fallback when SSE reconnects.
      }
    }
    return () => {
      window.clearInterval(interval)
      source?.close()
    }
  }, [apiStatus, projectId, refresh])

  const filtered = useMemo(
    () => jobs.filter((job) => (
      (filter === 'ALL' || (filter === 'RUNNING'
        ? ACTIVE_JOB_STATUSES.has(job.status)
        : job.status === filter))
      && (!focusedJobType || job.jobType === focusedJobType)
    )),
    [filter, focusedJobType, jobs],
  )
  const hasActiveJobs = jobs.some((job) => ACTIVE_JOB_STATUSES.has(job.status))
  const projectFilterOptions = useMemo(() => {
    const options = new Map<string, string>()
    projectSummaries.forEach((item) => options.set(item.id, item.name))
    jobs.forEach((job) => options.set(job.projectId, job.projectName))
    return [...options.entries()].sort((left, right) => left[1].localeCompare(right[1], 'zh-CN'))
  }, [jobs, projectSummaries])

  function navigateTasksScope(nextProjectId: string) {
    const params = new URLSearchParams()
    if (nextProjectId) params.set('project', nextProjectId)
    if (focusedJobType) params.set('jobType', focusedJobType)
    const query = params.toString()
    navigate(query ? `/tasks?${query}` : '/tasks')
  }

  useEffect(() => {
    if (!hasActiveJobs) return
    setNowMs(Date.now())
    const interval = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [hasActiveJobs])

  const completedCtaFor = useCallback((job: Job) => {
    const relatedShot = project.id === job.projectId
      ? project.shots.find((shot) => (
        shot.id === job.entityId || job.entity?.endsWith(shot.id)
      ))
      : undefined
    return getCompletedJobCta(job, job.projectId, relatedShot
      ? {
        episodeId: project.episodeId,
        sceneId: relatedShot.sceneId,
        shotId: relatedShot.id,
      }
      : undefined)
  }, [project.episodeId, project.id, project.shots])

  const nextWorkspace = projectId
    ? jobs.map(completedCtaFor).find((cta) => cta !== null)?.href ?? `/projects/${projectId}`
    : '/projects'
  const allTasksHref = projectId ? `/tasks?project=${projectId}` : '/tasks'
  const focusedJobTypeQuery = focusedJobType ? `jobType=${focusedJobType}` : ''
  const globalTasksHref = focusedJobTypeQuery ? `/tasks?${focusedJobTypeQuery}` : '/tasks'

  async function cancel(jobId: string) {
    setActingJobId(jobId)
    setError(null)
    try {
      const updated = await cancelPersistedJob(jobId)
      setJobs((current) => current.map((job) => job.id === jobId ? updated : job))
      notify('已发送取消请求，任务会在安全点停止。', 'info')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '任务操作失败')
    } finally {
      setActingJobId(null)
    }
  }

  async function recover(
    jobId: string,
    action: JobRecoveryAction,
    request: Omit<JobRecoveryRequest, 'action'> = {},
  ) {
    setActingJobId(jobId)
    setError(null)
    try {
      const updated = await recoverPersistedJob(jobId, { action, ...request })
      setJobs((current) => current.map((job) => job.id === jobId ? updated : job))
      notify({
        RESUME_FROM_FAILURE: '任务将从失败步骤继续。',
        RETRY_FAILED_PARTS: '只重新排队了失败部分。',
        SWITCH_MODEL: '任务将使用备用模型或方案继续。',
        FALLBACK_EXECUTION: '任务将采用降级方案执行，完成后仍需人工复核。',
        SAVE_INTERMEDIATE: '中间结果已经保存。',
        PROVIDE_INPUT: '补充信息已保存，任务将从失败处继续。',
      }[action], action === 'FALLBACK_EXECUTION' ? 'info' : 'success')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '任务恢复失败')
    } finally {
      setActingJobId(null)
    }
  }

  return <div className="page page--tasks">
    <PageHeader
      eyebrow="任务运行"
      title="生成任务"
      description={projectId ? '查看当前项目的生成进度与历史记录。' : '汇总查看所有项目的生成进度与历史记录。'}
      actions={<Link className="button button--primary button--md" to={nextWorkspace}>{projectId ? '打开当前工作区' : '返回项目列表'} <ArrowRight size={16} /></Link>}
    />
    {focusedJobType ? <div className="task-scope-banner"><div><Clock3 size={17} /><span><strong>{{
      GENERATE_STORY_DIRECTIONS: '故事方向生成',
      GENERATE_STORY_STRUCTURE: '故事结构生成',
      GENERATE_SCRIPT_PACKAGE: '分集大纲与剧本生成',
      GENERATE_STORY_PACKAGE: '故事资料生成',
    }[focusedJobType]}</strong><small>{projectId ? '已定位到当前项目的对应任务' : '正在查看所有项目的对应任务'}</small></span></div><Link to={allTasksHref}><X size={14} />查看全部任务</Link></div> : null}
    <div className="task-project-scope">
      <span>任务范围</span>
      <div className="task-project-scope__controls">
        <SelectControl
          aria-label="按项目筛选"
          onChange={(event) => navigateTasksScope(event.target.value)}
          value={projectId ?? ''}
        >
          <option value="">所有项目</option>
          {projectFilterOptions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </SelectControl>
        {projectId ? <Link to={globalTasksHref}>查看全部项目</Link> : null}
      </div>
    </div>
    <Toolbar className="task-toolbar" label="任务筛选"><TabList aria-label="按状态筛选"><Filter size={16} />{(['ALL', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'] as const).map((item) => <Tab className={filter === item ? 'active' : ''} key={item} onClick={() => setFilter(item)} selected={filter === item}>{item === 'ALL' ? '全部状态' : item === 'RUNNING' ? '运行中' : item === 'SUCCEEDED' ? '已完成' : item === 'FAILED' ? '失败' : '已取消'}</Tab>)}</TabList><span>{filtered.length} 个任务 · {projectId ? '当前项目' : '所有项目'}</span></Toolbar>
    {error ? <div className="brief-save-message brief-save-message--error" role="alert">{error}<Button onClick={refresh} size="sm" variant="ghost"><RotateCcw size={14} />重试读取</Button></div> : null}
    {loading ? <div className="brief-page-state"><LoaderCircle className="spin" size={20} />正在读取持久化任务…</div> : null}
    <section className={`task-list${projectId ? ' task-list--project-scoped' : ''}`}>
      <div className="task-list__header" aria-hidden="true">
        <span>任务</span>{projectId ? null : <span>项目</span>}<span>创建时间</span>
        <span>任务状态</span><span>用时</span><span>操作</span>
      </div>
      {filtered.map((job, index) => {
      const active = ACTIVE_JOB_STATUSES.has(job.status)
      const elapsedSeconds = elapsedJobSeconds(job, nowMs)
      const completedCta = completedCtaFor(job)
      const failedGuidance = getFailedJobGuidance(job, job.projectId)
      const taskDetail = failedGuidance?.description ?? (job.errorMessage
        ? localizeDisplayText(job.errorMessage)
        : `已执行 ${job.attempt}/${job.maxAttempts} 次`)
      const visibleErrorCode = job.status === 'FAILED' && job.errorCode
        ? ` · 错误码：${job.errorCode}`
        : ''
      return <article data-active={active || undefined} data-focused={focusedJobType === job.jobType || undefined} key={job.id} title={`任务编号 ${job.id}`}>
        <div className="task-list__lead"><span className={`activity-dot activity-dot--${job.status.toLowerCase()}`} /><div><strong>{taskDisplayLabel(job)}</strong><small>任务 #{String(filtered.length - index).padStart(2, '0')}</small></div></div>
        {projectId ? null : <Link className="task-list__project" title={`打开项目：${job.projectName}`} to={`/projects/${job.projectId}`}>{job.projectName}</Link>}
        <span className="task-list__created">{createdLabel(job.createdAt)}</span>
        <div className="task-list__state">
          <div className="task-list__state-summary">
            <StatusBadge status={job.status} />
            {active ? <span className="task-list__stage-progress">{Math.round(job.progress)}%</span> : null}
          </div>
          {job.status === 'FAILED' ? <small>{taskDetail}{visibleErrorCode}</small> : null}
          {active ? <>
            <small title={localizeDisplayText(job.stage)}>{localizeDisplayText(job.stage)}</small>
            <ProgressBar value={job.progress} />
          </> : null}
        </div>
        <span className="task-list__timing"><span><Clock3 size={14} />{formatElapsedTime(elapsedSeconds)}</span></span>
        <div className="task-list__actions">{active ? <Button disabled={actingJobId === job.id} onClick={() => void cancel(job.id)} size="sm" variant="ghost">{actingJobId === job.id ? <LoaderCircle className="spin" size={15} /> : <Ban size={15} />}取消</Button> : null}{failedGuidance?.secondaryCta ? <Link className="button button--secondary button--sm task-list__cta" to={failedGuidance.secondaryCta.href}>{failedGuidance.secondaryCta.label}<ArrowRight size={14} /></Link> : null}{completedCta ? <Link className="button button--secondary button--sm task-list__cta" to={completedCta.href}>{completedCta.label}<ArrowRight size={14} /></Link> : null}</div>
        <JobRecoveryPanel
          busy={actingJobId === job.id}
          job={job}
          onRecover={(action, request) => recover(job.id, action, request)}
        />
      </article>
    })}</section>
  </div>
}
