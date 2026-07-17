import {
  AlertTriangle,
  Check,
  Circle,
  LoaderCircle,
  LockKeyhole,
} from 'lucide-react'
import { Link } from 'react-router'
import { useProjectReadiness } from '../store/ProjectReadinessContext'
import type { ProjectStage } from '../types'

const MODE_LABELS = {
  CLASSIC: '经典镜头工作流',
  PIPELINE: '五阶段制作流',
  HYBRID: '迁移中的五阶段制作流',
} as const

function StageIcon({ stage }: { stage: ProjectStage }) {
  if (stage.status === 'COMPLETE') return <Check size={14} />
  if (stage.status === 'IN_PROGRESS') return <LoaderCircle className="spin" size={14} />
  if (stage.status === 'BLOCKED') return <AlertTriangle size={14} />
  if (stage.status === 'LOCKED') return <LockKeyhole size={13} />
  return <Circle size={12} />
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
          <strong>{readiness.summaryStatus === 'IN_PROGRESS' ? `${readiness.activeJobCount} 个任务进行中` : `当前：${activeStage?.label ?? '项目概览'}`}</strong>
        </div>
        <Link to={readiness.nextActionHref}>{readiness.nextActionLabel}</Link>
      </header>
      <nav aria-label="制作阶段">
        {readiness.stages.map((stage) => stage.status === 'LOCKED' ? (
          <span
            aria-disabled="true"
            className={`project-workflow__stage project-workflow__stage--${stage.status.toLowerCase()}`}
            key={stage.key}
            title={stage.detail}
          >
            <i><StageIcon stage={stage} /></i>
            <span>{stage.label}</span>
          </span>
        ) : (
          <Link
            aria-current={stage.key === readiness.activeStageKey ? 'step' : undefined}
            className={`project-workflow__stage project-workflow__stage--${stage.status.toLowerCase()}`}
            key={stage.key}
            title={stage.detail}
            to={stage.href}
          >
            <i><StageIcon stage={stage} /></i>
            <span>{stage.label}</span>
          </Link>
        ))}
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
