import {
  AlertTriangle,
  Check,
  Circle,
  LoaderCircle,
  LockKeyhole,
} from 'lucide-react'
import { Link } from 'react-router'
import { useProjectReadiness } from '../store/ProjectReadinessContext'
import type { ProjectStage, ProjectStageStatus } from '../types'

const MODE_LABELS = {
  CLASSIC: '经典镜头工作流',
  PIPELINE: '五阶段制作流',
  HYBRID: '迁移中的五阶段制作流',
} as const

const STAGE_STATUS_LABELS: Record<ProjectStageStatus, string> = {
  COMPLETE: '已完成',
  CURRENT: '当前阶段',
  IN_PROGRESS: '进行中',
  BLOCKED: '受阻',
  LOCKED: '未解锁',
}

function StageIcon({ stage }: { stage: ProjectStage }) {
  if (stage.status === 'COMPLETE') return <Check size={14} />
  if (stage.status === 'IN_PROGRESS') return <LoaderCircle className="spin" size={14} />
  if (stage.status === 'BLOCKED') return <AlertTriangle size={14} />
  if (stage.status === 'LOCKED') return <LockKeyhole size={13} />
  return <Circle size={12} />
}

function StageTip({ stage }: { stage: ProjectStage }) {
  const statusClass = stage.status.toLowerCase()
  return (
    <span className="project-workflow__tip" role="tooltip">
      <span className="project-workflow__tip-row">
        <strong>{stage.label}</strong>
        <em className={`project-workflow__tip-status project-workflow__tip-status--${statusClass}`}>
          {STAGE_STATUS_LABELS[stage.status]}
        </em>
      </span>
      <small>{stage.detail}</small>
    </span>
  )
}

export function ProjectWorkflowBar() {
  const { loading, readiness } = useProjectReadiness()

  if (loading && !readiness) {
    return <div className="project-workflow project-workflow--loading" role="status"><LoaderCircle className="spin" size={15} />正在同步项目阶段…</div>
  }
  if (!readiness) return null

  const activeStage = readiness.stages.find((stage) => stage.key === readiness.activeStageKey)
  return (
    <section className="project-workflow" aria-label="项目制作阶段">
      <header>
        <div>
          <span>{MODE_LABELS[readiness.workflowMode]}</span>
          <strong>{readiness.summaryStatus === 'IN_PROGRESS' && readiness.activeJobCount > 0
            ? <><LoaderCircle className="spin project-workflow__jobs-icon" size={12} />{readiness.activeJobCount} 个任务进行中</>
            : `当前：${activeStage?.label ?? '项目概览'}`}</strong>
        </div>
        <Link to={readiness.nextActionHref}>{readiness.nextActionLabel}</Link>
      </header>
      <nav aria-label="制作阶段">
        <ol className="project-workflow__stages">
          {readiness.stages.map((stage) => {
            const statusClass = stage.status.toLowerCase()
            const locked = stage.status === 'LOCKED'
            return (
              <li className={`project-workflow__item project-workflow__item--${statusClass}`} key={stage.key}>
                {locked ? (
                  <span
                    aria-disabled="true"
                    className={`project-workflow__stage project-workflow__stage--${statusClass}`}
                  >
                    <i><StageIcon stage={stage} /></i>
                    <span>{stage.label}</span>
                  </span>
                ) : (
                  <Link
                    aria-current={stage.key === readiness.activeStageKey ? 'step' : undefined}
                    className={`project-workflow__stage project-workflow__stage--${statusClass}`}
                    to={stage.href}
                  >
                    <i><StageIcon stage={stage} /></i>
                    <span>{stage.label}</span>
                  </Link>
                )}
                <StageTip stage={stage} />
              </li>
            )
          })}
        </ol>
      </nav>
      {readiness.blockers[0] ? (
        <div className="project-workflow__blocker" role="alert">
          <AlertTriangle size={15} />
          <span>{readiness.blockers[0].message}</span>
          <Link to={readiness.blockers[0].actionHref}>{readiness.blockers[0].actionLabel}</Link>
        </div>
      ) : null}
    </section>
  )
}
