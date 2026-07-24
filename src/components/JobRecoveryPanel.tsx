import { useState } from 'react'
import {
  Archive,
  ArrowLeftRight,
  ChevronDown,
  Gauge,
  ListRestart,
  MessageSquareText,
  Play,
  Save,
} from 'lucide-react'

import type { Job, JobRecoveryAction, JobRecoveryRequest } from '../types'
import { getJobRecoveryPlan } from '../utils/jobRecovery'
import { ConfirmModal } from './ConfirmModal'
import { Button } from './ui'

interface JobRecoveryPanelProps {
  job: Job
  busy: boolean
  onRecover: (
    action: JobRecoveryAction,
    request?: Omit<JobRecoveryRequest, 'action'>,
  ) => Promise<void>
}

export function JobRecoveryPanel({
  job,
  busy,
  onRecover,
}: JobRecoveryPanelProps) {
  const plan = getJobRecoveryPlan(job)
  const [expanded, setExpanded] = useState(job.status === 'FAILED')
  const [showInput, setShowInput] = useState(false)
  const [additionalInput, setAdditionalInput] = useState('')
  const [pendingAction, setPendingAction] = useState<JobRecoveryAction | null>(null)

  if (!plan) return null
  const degraded = plan.completionState === 'DEGRADED_SUCCEEDED'
  const can = (action: JobRecoveryAction) => plan.availableActions.includes(action)

  return <section className={`task-recovery-panel${degraded ? ' task-recovery-panel--degraded' : ''}`}>
    <header>
      <div className="task-recovery-panel__summary">
        <span><Gauge size={16} />{degraded ? '降级完成' : plan.completedPercent > 0 ? `部分完成 ${plan.completedPercent}%` : '尚未完成'}</span>
        <div>
          <strong>{degraded ? '结果可继续使用，但需要复核' : `停在：${plan.failedStep}`}</strong>
          <small>{plan.intermediateResultSaved ? '中间结果已经保存' : '任务参数和进度已经保留'}</small>
        </div>
      </div>
      {!degraded ? <button
        aria-expanded={expanded}
        className="task-recovery-panel__toggle"
        onClick={() => setExpanded((current) => !current)}
        type="button"
      >
        {expanded ? '收起恢复方案' : '查看恢复方案'}
        <ChevronDown size={15} />
      </button> : null}
    </header>

    {expanded && !degraded ? <div className="task-recovery-panel__body">
      {plan.completedSteps.length > 0 ? <div className="task-recovery-panel__checkpoint">
        <Archive size={16} />
        <span><strong>已经完成</strong><small>{plan.completedSteps.join('、')}</small></span>
      </div> : null}

      <div className="task-recovery-panel__actions">
        {can('RESUME_FROM_FAILURE') ? <Button
          disabled={busy}
          onClick={() => onRecover('RESUME_FROM_FAILURE')}
          size="sm"
        ><Play size={15} />从失败步骤继续</Button> : null}
        {can('RETRY_FAILED_PARTS') ? <Button
          disabled={busy}
          onClick={() => onRecover('RETRY_FAILED_PARTS', { failedPartIds: plan.failedParts })}
          size="sm"
          variant="secondary"
        ><ListRestart size={15} />只重试失败部分</Button> : null}
        {can('SWITCH_MODEL') ? <Button
          disabled={busy}
          onClick={() => setPendingAction('SWITCH_MODEL')}
          size="sm"
          variant="secondary"
        ><ArrowLeftRight size={15} />切换模型或方案</Button> : null}
        {can('FALLBACK_EXECUTION') ? <Button
          disabled={busy}
          onClick={() => setPendingAction('FALLBACK_EXECUTION')}
          size="sm"
          variant="ghost"
        ><Gauge size={15} />降级执行</Button> : null}
        {can('SAVE_INTERMEDIATE') ? <Button
          disabled={busy || plan.intermediateResultSaved}
          onClick={() => onRecover('SAVE_INTERMEDIATE')}
          size="sm"
          variant="ghost"
        ><Save size={15} />{plan.intermediateResultSaved ? '中间结果已保存' : '保存中间结果'}</Button> : null}
        {can('PROVIDE_INPUT') ? <Button
          disabled={busy}
          onClick={() => setShowInput((current) => !current)}
          size="sm"
          variant="ghost"
        ><MessageSquareText size={15} />补充信息</Button> : null}
      </div>

      {showInput ? <form
        className="task-recovery-panel__input"
        onSubmit={(event) => {
          event.preventDefault()
          if (!additionalInput.trim()) return
          void onRecover('PROVIDE_INPUT', { additionalInput: additionalInput.trim() })
        }}
      >
        <label htmlFor={`recovery-input-${job.id}`}>补充系统继续执行所需的信息</label>
        <textarea
          id={`recovery-input-${job.id}`}
          onChange={(event) => setAdditionalInput(event.target.value)}
          placeholder="例如：保留前两个镜头，只调整第三个镜头的动作节奏。"
          rows={3}
          value={additionalInput}
        />
        <div><Button disabled={busy || !additionalInput.trim()} size="sm" type="submit"><Play size={15} />补充并继续</Button></div>
      </form> : null}
    </div> : null}

    <ConfirmModal
      confirmLabel={pendingAction === 'FALLBACK_EXECUTION' ? '降级执行' : '切换并继续'}
      confirmVariant={pendingAction === 'FALLBACK_EXECUTION' ? 'danger' : 'primary'}
      description={pendingAction === 'FALLBACK_EXECUTION'
        ? '任务将采用降级方案执行，完成后仍需人工复核结果。'
        : '任务将切换备用模型或方案继续，输出质量可能与原计划不同。'}
      loading={busy}
      onClose={() => setPendingAction(null)}
      onConfirm={() => {
        if (!pendingAction) return
        const action = pendingAction
        setPendingAction(null)
        void onRecover(
          action,
          action === 'FALLBACK_EXECUTION'
            ? { strategy: 'stability-first' }
            : { strategy: 'auto-alternate' },
        )
      }}
      open={pendingAction !== null}
      title={pendingAction === 'FALLBACK_EXECUTION' ? '确认降级执行？' : '确认切换模型或方案？'}
    />
  </section>
}
