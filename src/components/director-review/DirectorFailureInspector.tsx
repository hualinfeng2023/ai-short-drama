import { AlertTriangle, LoaderCircle, RefreshCw } from 'lucide-react'
import type { DirectorGenerationFailure } from '../../api/directorFailures'
import { Button } from '../ui'
import './DirectorFailureInspector.css'

interface DirectorFailureInspectorProps {
  failure: DirectorGenerationFailure
  busy: boolean
  onRetry: (failure: DirectorGenerationFailure) => void
}

function failureStageLabel(stage?: string): string {
  if (stage === 'COMMAND_VALIDATION') return '执行合同校验'
  if (stage === 'PROVIDER_VALIDATION') return '模型结构校验'
  return 'Director 审查'
}

export function DirectorFailureInspector({
  failure,
  busy,
  onRetry,
}: DirectorFailureInspectorProps) {
  return (
    <section className="director-failure-inspector" role="alert">
      <header>
        <span><AlertTriangle size={16} />上次审查未生成 Proposal</span>
        <code>{failure.errorCode ?? 'DIRECTOR_REVIEW_FAILED'}</code>
      </header>
      <p>{failure.errorMessage ?? 'Director 输出未通过结构化创作合同。'}</p>
      <dl>
        <div>
          <dt>失败阶段</dt>
          <dd>{failureStageLabel(failure.failureStage)}</dd>
        </div>
        <div>
          <dt>模型</dt>
          <dd>{failure.provider} · {failure.model}</dd>
        </div>
        <div>
          <dt>校验尝试</dt>
          <dd>{failure.attemptCount} 次，其中修复 {failure.repairAttempts} 次</dd>
        </div>
        <div>
          <dt>耗时</dt>
          <dd>{failure.latencyMs == null ? '未记录' : `${failure.latencyMs} ms`}</dd>
        </div>
      </dl>
      <footer>
        <small>
          重试会创建新 GenerationRecord 和新 Proposal，不覆盖本次失败记录，也不会触发媒体生成。
        </small>
        <Button
          disabled={busy}
          onClick={() => onRetry(failure)}
          size="sm"
          variant="secondary"
        >
          {busy ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}
          {busy ? '重新审查中…' : '重新审查'}
        </Button>
      </footer>
    </section>
  )
}
