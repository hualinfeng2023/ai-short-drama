import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowRight, Ban, Clock3, Filter, LoaderCircle, RotateCcw, Sparkles, X } from 'lucide-react'
import { Link, useSearchParams } from 'react-router'
import {
  cancelPersistedJob,
  fetchJobs,
  fetchProjectJobs,
  retryPersistedJob,
} from '../api/client'
import { Button, PageHeader, ProgressBar, StatusBadge } from '../components/ui'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'
import type { Job, JobStatus } from '../types'
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

function taskSourceLabel(job: Job) {
  const source = job.entity.split(':', 1)[0]
  const labels: Record<string, string> = {
    brief_version: '项目简报',
    proposal_version: '已确认故事方向',
    shot: '镜头任务',
    story_version: '故事与剧本',
  }
  return labels[source] ?? '生成任务'
}

export function TasksPage() {
  const { notify } = useToast()
  const { project, jobs: contextJobs, apiStatus } = useStudio()
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

  useEffect(() => {
    if (!hasActiveJobs) return
    setNowMs(Date.now())
    const interval = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [hasActiveJobs])

  const completedCtaFor = useCallback((job: Job) => {
    const relatedShot = project.id === job.projectId
      ? project.shots.find((shot) => shot.id === job.entityId || job.entity.endsWith(shot.id))
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
  const currentProjectTasksHref = `/tasks?project=${projectId ?? project.id}${focusedJobTypeQuery ? `&${focusedJobTypeQuery}` : ''}`
  const globalTasksHref = focusedJobTypeQuery ? `/tasks?${focusedJobTypeQuery}` : '/tasks'

  async function act(jobId: string, action: 'cancel' | 'retry') {
    setActingJobId(jobId)
    setError(null)
    try {
      const updated = action === 'cancel'
        ? await cancelPersistedJob(jobId)
        : await retryPersistedJob(jobId)
      setJobs((current) => current.map((job) => job.id === jobId ? updated : job))
      notify(action === 'cancel' ? '已发送取消请求，任务会在安全点停止。' : '任务已重新排队。', action === 'cancel' ? 'info' : 'success')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '任务操作失败')
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
    {focusedJobType ? <div className="task-scope-banner"><div><Sparkles size={17} /><span><strong>{{
      GENERATE_STORY_DIRECTIONS: '故事方向生成',
      GENERATE_STORY_STRUCTURE: '故事结构生成',
      GENERATE_SCRIPT_PACKAGE: '分集大纲与剧本生成',
      GENERATE_STORY_PACKAGE: '故事资料生成',
    }[focusedJobType]}</strong><small>{projectId ? '已定位到当前项目的对应任务' : '正在查看所有项目的对应任务'}</small></span></div><Link to={allTasksHref}><X size={14} />查看全部任务</Link></div> : null}
    <div className="task-project-scope"><span>任务范围</span><nav aria-label="任务范围"><Link aria-current={projectId ? 'page' : undefined} className={projectId ? 'active' : ''} to={currentProjectTasksHref}>当前项目</Link><Link aria-current={!projectId ? 'page' : undefined} className={!projectId ? 'active' : ''} to={globalTasksHref}>所有项目</Link></nav></div>
    <div className="task-toolbar"><div><Filter size={16} />{(['ALL', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'] as const).map((item) => <button className={filter === item ? 'active' : ''} key={item} onClick={() => setFilter(item)}>{item === 'ALL' ? '全部状态' : item === 'RUNNING' ? '运行中' : item === 'SUCCEEDED' ? '已完成' : item === 'FAILED' ? '失败' : '已取消'}</button>)}</div><span>{filtered.length} 个任务 · {projectId ? '当前项目' : '所有项目'}</span></div>
    {error ? <div className="brief-save-message brief-save-message--error" role="alert">{error}<Button onClick={refresh} size="sm" variant="ghost"><RotateCcw size={14} />重试读取</Button></div> : null}
    {loading ? <div className="brief-page-state"><LoaderCircle className="spin" size={20} />正在读取持久化任务…</div> : null}
    <section className="task-list">
      <div className="task-list__header" aria-hidden="true"><span>任务</span><span>进度</span><span>用时</span><span /></div>
      {filtered.map((job) => {
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
      return <article data-active={active || undefined} data-focused={focusedJobType === job.jobType || undefined} key={job.id}>
        <div className="task-list__lead"><span className={`activity-dot activity-dot--${job.status.toLowerCase()}`} /><div><strong>{localizeDisplayText(job.label)}</strong><small>{taskSourceLabel(job)}{projectId ? '' : ` · 项目 ${job.projectId.slice(0, 8)}`} · {createdLabel(job.createdAt)}</small></div></div>
        <div className="task-list__stage"><div className={!active ? 'task-list__stage-summary' : undefined}>{active ? <strong title={localizeDisplayText(job.stage)}>{localizeDisplayText(job.stage)}</strong> : null}<span className="task-list__stage-meta">{active ? <span className="task-list__stage-progress">{Math.round(job.progress)}%</span> : null}<StatusBadge status={job.status} /></span></div>{active ? <ProgressBar value={job.progress} /> : job.status === 'SUCCEEDED' ? null : <small>{taskDetail}{visibleErrorCode}</small>}</div>
        <span className="task-list__timing"><span><Clock3 size={14} />{formatElapsedTime(elapsedSeconds)}</span></span>
        <div className="task-list__actions">{active ? <Button disabled={actingJobId === job.id} onClick={() => void act(job.id, 'cancel')} size="sm" variant="ghost">{actingJobId === job.id ? <LoaderCircle className="spin" size={15} /> : <Ban size={15} />}取消</Button> : null}{(job.status === 'FAILED' || job.status === 'CANCELLED') && job.retryable ? <Button disabled={actingJobId === job.id} onClick={() => void act(job.id, 'retry')} size="sm" variant="secondary">{actingJobId === job.id ? <LoaderCircle className="spin" size={15} /> : <RotateCcw size={15} />}{failedGuidance?.retryLabel ?? '重试'}</Button> : null}{failedGuidance?.secondaryCta ? <Link className="button button--secondary button--sm task-list__cta" to={failedGuidance.secondaryCta.href}>{failedGuidance.secondaryCta.label}<ArrowRight size={14} /></Link> : null}{completedCta ? <Link className="button button--secondary button--sm task-list__cta" to={completedCta.href}>{completedCta.label}<ArrowRight size={14} /></Link> : null}</div>
      </article>
    })}</section>
  </div>
}
