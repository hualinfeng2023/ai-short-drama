import { useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Film,
  Lightbulb,
  ListTodo,
  Play,
} from 'lucide-react'
import { Link } from 'react-router'
import { PageHeader, ProgressBar, StatusBadge } from '../components/ui'
import { calculateProgress } from '../data/demo'
import { useStudio } from '../store/StudioContext'
import { localizeDisplayText } from '../utils/localizeDisplayText'
import type { Job, Shot } from '../types'

const ACTIVE_JOB_STATUSES = new Set(['PENDING', 'RETRY_WAIT', 'RUNNING', 'CANCEL_REQUESTED'])

function formatDuration(seconds: number): string {
  return `${seconds.toFixed(1)} 秒`
}

function shotGeneratingJob(shot: Shot, jobs: Job[]): Job | undefined {
  return jobs.find((job) =>
    ACTIVE_JOB_STATUSES.has(job.status)
    && job.entity.includes(shot.id),
  )
}

function buildAssistantSuggestion(shots: Shot[]) {
  const target = shots.find((shot) => shot.continuity === 'RISK')
    ?? shots.find((shot) => shot.continuity === 'NOTICE')
    ?? shots.find((shot) => shot.status === 'PENDING_REVIEW' && shot.dialogue)
  if (!target) return null
  if (target.continuity === 'RISK') {
    return {
      shot: target,
      title: `先确认 ${target.code} 的连续性风险`,
      body: `${target.code} 的光线或场景连续性需要人工确认，避免后续镜头穿帮。`,
      cta: '进入镜头工作台',
      href: (projectId: string, episodeId: string) =>
        `/projects/${projectId}/episodes/${episodeId}/scenes/${target.sceneId}?shot=${target.id}`,
    }
  }
  if (target.dialogue) {
    return {
      shot: target,
      title: `精简 ${target.code} 的对白节奏`,
      body: `当前对白较长，可考虑把部分信息放进动作，缩短约 ${Math.max(0.8, target.durationSec * 0.15).toFixed(1)} 秒。`,
      cta: '带入局部修改',
      href: (projectId: string, episodeId: string) =>
        `/projects/${projectId}/episodes/${episodeId}/preview`,
    }
  }
  return null
}

export function EpisodePage() {
  const { project, jobs } = useStudio()
  const [assistantDismissed, setAssistantDismissed] = useState(false)
  const progress = calculateProgress(project.shots)
  const approvedShots = project.shots.filter((shot) => shot.status === 'APPROVED').length
  const reviewShots = project.shots.filter((shot) => shot.status === 'PENDING_REVIEW')
  const generatingShots = project.shots.filter((shot) =>
    shot.status === 'GENERATING' || shot.status === 'QUEUED',
  )
  const activeJobs = jobs.filter((job) => ACTIVE_JOB_STATUSES.has(job.status))
  const inProgressScenes = project.scenes.filter((scene) => scene.status === 'IN_PROGRESS').length
  const currentDurationSec = project.shots.reduce((sum, shot) => sum + shot.durationSec, 0)
  const firstReview = reviewShots[0]
  const firstGenerating = generatingShots[0]
  const generatingJob = firstGenerating ? shotGeneratingJob(firstGenerating, jobs) : undefined

  const overview = useMemo(() => {
    if (reviewShots.length > 0) {
      return {
        title: reviewShots.length === 1
          ? '故事已成形，先处理 1 个待审版本'
          : `故事已成形，先处理 ${reviewShots.length} 个待审版本`,
        detail: `已批准 ${approvedShots}/${project.shots.length} 个镜头${
          firstGenerating
            ? `；${firstGenerating.code} 正在生成，未影响的已批准版本保持可用。`
            : activeJobs.length > 0
              ? `；${activeJobs.length} 个任务进行中。`
              : '。'
        }`,
      }
    }
    if (firstGenerating) {
      return {
        title: `${firstGenerating.code} 正在生成新版本`,
        detail: generatingJob
          ? `${localizeDisplayText(generatingJob.stage)} · 已批准 ${approvedShots}/${project.shots.length} 个镜头保持可播放。`
          : `已批准 ${approvedShots}/${project.shots.length} 个镜头；候选版本就绪后会进入审核。`,
      }
    }
    if (activeJobs.length > 0) {
      return {
        title: `${activeJobs.length} 个生成任务进行中`,
        detail: `${localizeDisplayText(activeJobs[0].label)} · 已批准 ${approvedShots}/${project.shots.length} 个镜头。`,
      }
    }
    if (approvedShots === project.shots.length && project.shots.length > 0) {
      return {
        title: '本集镜头已全部批准',
        detail: `当前小样时长 ${formatDuration(currentDurationSec)}，可进入完整小样确认节奏。`,
      }
    }
    return {
      title: '继续完善镜头版本',
      detail: `已批准 ${approvedShots}/${project.shots.length} 个镜头；完成审核后小样会自动更新。`,
    }
  }, [
    activeJobs,
    approvedShots,
    currentDurationSec,
    firstGenerating,
    generatingJob,
    project.shots.length,
    reviewShots.length,
  ])

  const assistantSuggestion = useMemo(
    () => buildAssistantSuggestion(project.shots),
    [project.shots],
  )
  const showAssistant = Boolean(assistantSuggestion) && !assistantDismissed

  return (
    <div className="page page--episode">
      <PageHeader
        eyebrow={`${project.name} · 主版本`}
        title="第 1 集 · 验证样片"
        description={`${project.targetDurationSec} 秒三段式样片 · 所有进度由当前镜头版本聚合`}
        actions={
          <Link className="button button--secondary button--md" to={`/projects/${project.id}/episodes/${project.episodeId}/preview`}>
            <Play size={16} /> 查看完整小样
          </Link>
        }
      />

      <section className="episode-overview">
        <div className="overview-progress">
          <div className="overview-progress__ring" style={{ '--progress': `${progress * 3.6}deg` } as React.CSSProperties}>
            <div><strong>{progress}%</strong><small>本集</small></div>
          </div>
          <div>
            <p className="eyebrow">当前进度</p>
            <h2>{overview.title}</h2>
            <p>{overview.detail}</p>
          </div>
        </div>
        <div className="overview-metrics">
          <span><small>场景</small><strong>{project.scenes.length}</strong><em>{inProgressScenes > 0 ? `${inProgressScenes} 个进行中` : '全部就绪'}</em></span>
          <span><small>镜头</small><strong>{project.shots.length}</strong><em>{reviewShots.length > 0 ? `${reviewShots.length} 个待审` : '无待审'}</em></span>
          <span><small>目标时长</small><strong>{project.targetDurationSec} 秒</strong><em>当前 {formatDuration(currentDurationSec)}</em></span>
          <span><small>时间线</small><strong>第 {project.timelineVersion} 版</strong><em>当前基线</em></span>
        </div>
      </section>

      <section className="next-action">
        <div className="next-action__icon"><ListTodo size={20} /></div>
        <div>
          <p className="eyebrow">下一步最佳动作</p>
          <h2>{firstReview ? `审核 ${firstReview.code} 的候选版本` : '查看完整小样并批准'}</h2>
          <p>{firstReview ? '候选版本已就绪。批准前可比较对白节奏与镜头推进，不会覆盖当前版本。' : '所有镜头已经进入可播放基线。'}</p>
        </div>
        <Link
          className="button button--primary button--md"
          to={firstReview
            ? `/projects/${project.id}/episodes/${project.episodeId}/scenes/${firstReview.sceneId}?shot=${firstReview.id}`
            : `/projects/${project.id}/episodes/${project.episodeId}/preview`}
        >
          {firstReview ? '去审核版本' : '观看小样'} <ArrowRight size={16} />
        </Link>
      </section>

      <div className="episode-columns">
        <section className="scene-section" aria-labelledby="scene-section-title">
          <div className="section-heading">
            <div><p className="eyebrow">场景</p><h2 id="scene-section-title">场景进度</h2></div>
            <span>{project.scenes.length} 场 · {project.shots.length} 镜头</span>
          </div>
          <div className="scene-grid">
            {project.scenes.map((scene) => {
              const sceneShots = project.shots.filter((shot) => scene.shotIds.includes(shot.id))
              const sceneProgress = calculateProgress(sceneShots)
              const primaryIssue = sceneShots.find((shot) => shot.continuity === 'RISK')
              const sceneImage = sceneShots.find((shot) => shot.currentImageUrl)?.currentImageUrl
              return (
                <article className="scene-card" key={scene.id}>
                  <div className={`scene-card__visual scene-card__visual--${scene.code}`}>
                    {sceneImage ? <img alt="" className="scene-card__image" src={sceneImage} /> : null}
                    <span>场景 {scene.code}</span>
                    <strong>{scene.title}</strong>
                    <small>{scene.purpose}</small>
                  </div>
                  <div className="scene-card__body">
                    <div className="scene-card__title"><div><small>场景 {scene.code}</small><h3>{scene.title}</h3></div><StatusBadge status={scene.status} label={scene.status === 'IN_PROGRESS' ? '制作中' : undefined} /></div>
                    <p>{scene.purpose}</p>
                    <div className="scene-card__meta"><span><Film size={14} />{sceneShots.length} 个镜头</span><span><Clock3 size={14} />{scene.durationSec} 秒</span></div>
                    <ProgressBar value={sceneProgress} />
                    {primaryIssue ? <p className="scene-card__risk"><AlertTriangle size={14} />{primaryIssue.code} 的光线方向需要确认</p> : <p className="scene-card__clear"><CheckCircle2 size={14} />未发现阻断风险</p>}
                    <Link to={`/projects/${project.id}/episodes/${project.episodeId}/scenes/${scene.id}?shot=${sceneShots[0]?.id}`}>进入场景工作台 <ArrowRight size={15} /></Link>
                  </div>
                </article>
              )
            })}
          </div>
        </section>

        <aside className="episode-side">
          <section className="todo-card">
            <div className="section-heading"><div><p className="eyebrow">当前状态</p><h2>注意事项</h2></div><ListTodo size={19} /></div>
            {reviewShots.length > 0 ? (
              <Link to="/reviews"><span className="todo-icon todo-icon--warning"><AlertTriangle size={15} /></span><span><strong>{reviewShots.length} 个版本待审核</strong><small>先确认候选版本，再重组小样</small></span><ArrowRight size={15} /></Link>
            ) : null}
            {activeJobs.length > 0 ? (
              <Link to={`/tasks?project=${project.id}`}><span className="todo-icon"><Clock3 size={15} /></span><span><strong>{activeJobs.length} 个任务运行中</strong><small>{localizeDisplayText(activeJobs[0]?.stage ?? '处理中')}</small></span><ArrowRight size={15} /></Link>
            ) : reviewShots.length === 0 ? (
              <p className="todo-card__empty" role="status">当前没有待审版本或运行中任务。</p>
            ) : null}
          </section>

          {showAssistant && assistantSuggestion ? (
            <section className="assistant-card">
              <div className="assistant-card__mark"><Lightbulb size={18} /></div>
              <p className="eyebrow">镜头建议 · {assistantSuggestion.shot.code}</p>
              <h2>{assistantSuggestion.title}</h2>
              <p>{assistantSuggestion.body}</p>
              <div>
                <Link
                  className="button button--secondary button--sm"
                  to={assistantSuggestion.href(project.id, project.episodeId)}
                >
                  {assistantSuggestion.cta}
                </Link>
                <button onClick={() => setAssistantDismissed(true)} type="button">忽略</button>
              </div>
            </section>
          ) : assistantSuggestion && assistantDismissed ? (
            <button
              className="assistant-card assistant-card--collapsed"
              onClick={() => setAssistantDismissed(false)}
              type="button"
            >
              <Lightbulb size={16} />
              <span>查看 {assistantSuggestion.shot.code} 的镜头建议</span>
            </button>
          ) : null}

          <section className="activity-card">
            <div className="section-heading"><div><p className="eyebrow">活动记录</p><h2>生成动态</h2></div><Link to={`/tasks?project=${project.id}`}>全部</Link></div>
            {jobs.slice(0, 3).map((job) => (
              <div className="activity-row" key={job.id}>
                <span className={`activity-dot activity-dot--${job.status.toLowerCase()}`} />
                <div><strong>{localizeDisplayText(job.label)}</strong><small>{localizeDisplayText(job.stage)} · {job.createdAt}</small></div>
                <StatusBadge status={job.status} />
              </div>
            ))}
          </section>
        </aside>
      </div>
    </div>
  )
}
