import {
  AlertTriangle,
  ArrowRight,
  Check,
  Circle,
  LoaderCircle,
  LockKeyhole,
} from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Link, useLocation } from 'react-router'
import { useProjectReadiness } from '../store/ProjectReadinessContext'
import type { ProjectStage, ProjectStageStatus } from '../types'

const STAGE_STATUS_LABELS: Record<ProjectStageStatus, string> = {
  COMPLETE: '已完成',
  CURRENT: '当前阶段',
  IN_PROGRESS: '进行中',
  BLOCKED: '受阻',
  LOCKED: '未解锁',
}

function normalizeRoutePath(path: string) {
  const pathname = path.split(/[?#]/)[0] || '/'
  return pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
}

function StageIcon({ stage }: { stage: ProjectStage }) {
  if (stage.status === 'COMPLETE') return <Check size={14} />
  if (stage.status === 'IN_PROGRESS') return <LoaderCircle className="spin" size={14} />
  if (stage.status === 'BLOCKED') return <AlertTriangle size={14} />
  if (stage.status === 'LOCKED') return <LockKeyhole size={13} />
  return <Circle size={12} />
}

export function ProjectWorkflowBar() {
  const location = useLocation()
  const { error, loading, readiness } = useProjectReadiness()
  const stageNavRef = useRef<HTMLElement>(null)

  useEffect(() => {
    const nav = stageNavRef.current
    if (!nav) return

    const frame = window.requestAnimationFrame(() => {
      if (nav.scrollWidth <= nav.clientWidth) return
      const currentStage = nav.querySelector<HTMLElement>('[aria-current="step"]')
      if (!currentStage) return

      const targetLeft = currentStage.offsetLeft
        - ((nav.clientWidth - currentStage.offsetWidth) / 2)
      nav.scrollTo({ left: Math.max(0, targetLeft), behavior: 'auto' })
    })

    return () => window.cancelAnimationFrame(frame)
  }, [location.pathname, readiness?.activeStageKey])

  if (loading && !readiness) {
    return (
      <div aria-live="polite" className="project-workflow project-workflow--loading" role="status">
        <span className="project-workflow__state-icon" aria-hidden="true">
          <LoaderCircle className="spin" size={17} />
        </span>
        <span className="project-workflow__state-copy">
          <strong>正在同步项目阶段</strong>
          <small>正在读取最新制作进度与可用操作</small>
        </span>
      </div>
    )
  }
  if (error && !readiness) {
    return (
      <div aria-live="polite" className="project-workflow project-workflow--error" role="status">
        <span className="project-workflow__state-icon" aria-hidden="true">
          <AlertTriangle size={17} />
        </span>
        <span className="project-workflow__state-copy">
          <strong>项目阶段暂未同步</strong>
          <small>不影响当前页面操作，系统将在后台继续尝试</small>
        </span>
        <span className="project-workflow__retry-status" aria-hidden="true">
          <LoaderCircle className="spin" size={13} />
          自动重试中
        </span>
      </div>
    )
  }
  if (!readiness) return null

  const activeStage = readiness.stages.find((stage) => stage.key === readiness.activeStageKey)
  const activeStageIndex = readiness.stages.findIndex((stage) => stage.key === readiness.activeStageKey)
  const stagePosition = activeStageIndex >= 0
    ? `第 ${activeStageIndex + 1}/${readiness.stages.length} 阶段`
    : '项目阶段'
  const stageSummary = readiness.summaryStatus === 'IN_PROGRESS' && readiness.activeJobCount > 0
    ? `${stagePosition} · ${readiness.activeJobCount} 个任务进行中`
    : `${stagePosition} · ${activeStage?.label ?? '项目概览'}`
  const nextActionIsCurrentRoute = normalizeRoutePath(location.pathname)
    === normalizeRoutePath(readiness.nextActionHref)

  return (
    <section className="project-workflow" aria-label="项目制作阶段">
      <header>
        <div>
          <strong>
            {readiness.summaryStatus === 'IN_PROGRESS' && readiness.activeJobCount > 0
              ? <LoaderCircle aria-hidden="true" className="spin project-workflow__jobs-icon" size={12} />
              : null}
            {stageSummary}
          </strong>
        </div>
        {nextActionIsCurrentRoute ? null : (
          <Link to={readiness.nextActionHref}>
            {readiness.nextActionLabel}
            <ArrowRight aria-hidden="true" size={13} />
          </Link>
        )}
      </header>
      <div
        aria-label={`制作进度：第 ${activeStageIndex + 1}/${readiness.stages.length} 阶段`}
        className="project-workflow__mobile-progress"
      >
        <span>{activeStage?.label ?? '项目概览'}</span>
        <div
          className="project-workflow__mobile-track"
          style={{ gridTemplateColumns: `repeat(${readiness.stages.length}, minmax(0, 1fr))` }}
        >
          {readiness.stages.map((stage) => (
            <i
              className={
                stage.status === 'COMPLETE'
                  ? 'is-complete'
                  : stage.key === readiness.activeStageKey
                    ? 'is-current'
                    : stage.status === 'IN_PROGRESS'
                      ? 'is-progress'
                      : undefined
              }
              key={stage.key}
            />
          ))}
        </div>
        <small>第 {activeStageIndex + 1}/{readiness.stages.length} 阶段</small>
      </div>
      <nav aria-label="制作阶段" ref={stageNavRef}>
        <ol className="project-workflow__stages">
          {readiness.stages.map((stage) => {
            const statusClass = stage.status.toLowerCase()
            const locked = stage.status === 'LOCKED'
            const statusLabel = STAGE_STATUS_LABELS[stage.status]
            return (
              <li className={`project-workflow__item project-workflow__item--${statusClass}`} key={stage.key}>
                {locked ? (
                  <span
                    aria-disabled="true"
                    aria-label={`${stage.label}，${statusLabel}`}
                    className={`project-workflow__stage project-workflow__stage--${statusClass}`}
                    tabIndex={0}
                  >
                    <i><StageIcon stage={stage} /></i>
                    <span>{stage.label}</span>
                  </span>
                ) : (
                  <Link
                    aria-current={stage.key === readiness.activeStageKey ? 'step' : undefined}
                    aria-label={`${stage.label}，${statusLabel}`}
                    className={`project-workflow__stage project-workflow__stage--${statusClass}`}
                    to={stage.href}
                  >
                    <i><StageIcon stage={stage} /></i>
                    <span>{stage.label}</span>
                  </Link>
                )}
              </li>
            )
          })}
        </ol>
      </nav>
      {readiness.blockers[0] ? (
        <div aria-live="polite" className="project-workflow__blocker" role="status">
          <AlertTriangle size={15} />
          <span>{readiness.blockers[0].message}</span>
          <Link to={readiness.blockers[0].actionHref}>{readiness.blockers[0].actionLabel}</Link>
        </div>
      ) : null}
    </section>
  )
}
