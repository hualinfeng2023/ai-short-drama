import { useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Film,
  ListTodo,
  Play,
  Sparkles,
  WandSparkles,
} from 'lucide-react'
import { Link } from 'react-router'
import { PageHeader, ProgressBar, StatusBadge } from '../components/ui'
import { calculateProgress } from '../data/demo'
import { useStudio } from '../store/StudioContext'
import { localizeDisplayText } from '../utils/localizeDisplayText'

export function EpisodePage() {
  const { project, jobs } = useStudio()
  const [assistantVisible, setAssistantVisible] = useState(true)
  const progress = calculateProgress(project.shots)
  const approvedShots = project.shots.filter((shot) => shot.status === 'APPROVED').length
  const reviewShots = project.shots.filter((shot) => shot.status === 'PENDING_REVIEW')
  const activeJobs = jobs.filter((job) => job.status === 'RUNNING')
  const firstReview = reviewShots[0]

  return (
    <div className="page page--episode">
      <PageHeader
        eyebrow={`${project.name} · 主版本`}
        title="第 1 集 · 验证样片"
        description="60 秒三段式样片 · 所有进度由当前镜头版本聚合"
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
            <h2>故事已成形，先处理 1 个待审版本</h2>
            <p>已批准 {approvedShots}/{project.shots.length} 个镜头；S05 正在生成，未影响的已批准版本保持可用。</p>
          </div>
        </div>
        <div className="overview-metrics">
          <span><small>场景</small><strong>{project.scenes.length}</strong><em>1 个进行中</em></span>
          <span><small>镜头</small><strong>{project.shots.length}</strong><em>{reviewShots.length} 个待审</em></span>
          <span><small>目标时长</small><strong>{project.targetDurationSec} 秒</strong><em>当前 60.0 秒</em></span>
          <span><small>时间线</small><strong>第 {project.timelineVersion} 版</strong><em>当前基线</em></span>
        </div>
      </section>

      <section className="next-action">
        <div className="next-action__icon"><WandSparkles size={20} /></div>
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
            <div className="section-heading"><div><p className="eyebrow">待办事项</p><h2>待处理</h2></div><ListTodo size={19} /></div>
            <Link to="/reviews"><span className="todo-icon todo-icon--warning"><AlertTriangle size={15} /></span><span><strong>{reviewShots.length} 个版本待审核</strong><small>先确认候选版本，再重组小样</small></span><ArrowRight size={15} /></Link>
            <Link to={`/tasks?project=${project.id}`}><span className="todo-icon"><Clock3 size={15} /></span><span><strong>{activeJobs.length} 个任务运行中</strong><small>{activeJobs[0]?.stage ?? '暂无运行中任务'}</small></span><ArrowRight size={15} /></Link>
          </section>

          {assistantVisible ? (
            <section className="assistant-card">
              <div className="assistant-card__mark"><Sparkles size={18} /></div>
              <p className="eyebrow">AI 建议 · 规则估算</p>
              <h2>让 S03 的妹妹只说半句</h2>
              <p>把剩下的威胁放进“手压住钥匙”的动作里，转折会更克制，也能缩短约 1.2 秒对白。</p>
              <div><Link className="button button--secondary button--sm" to={`/projects/${project.id}/episodes/${project.episodeId}/preview`}>带入局部修改</Link><button onClick={() => setAssistantVisible(false)}>忽略</button></div>
            </section>
          ) : null}

          <section className="activity-card">
            <div className="section-heading"><div><p className="eyebrow">活动记录</p><h2>生成动态</h2></div><Link to={`/tasks?project=${project.id}`}>全部</Link></div>
            {jobs.slice(0, 3).map((job) => <div className="activity-row" key={job.id}><span className={`activity-dot activity-dot--${job.status.toLowerCase()}`} /><div><strong>{localizeDisplayText(job.label)}</strong><small>{localizeDisplayText(job.stage)} · {job.createdAt}</small></div><StatusBadge status={job.status} /></div>)}
          </section>
        </aside>
      </div>
    </div>
  )
}
