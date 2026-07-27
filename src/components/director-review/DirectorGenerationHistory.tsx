import { CheckCircle2, GitBranch, History, XCircle } from 'lucide-react'
import type { DirectorGenerationRecord } from '../../api/directorFailures'
import './DirectorGenerationHistory.css'

interface DirectorGenerationHistoryProps {
  records: DirectorGenerationRecord[]
}

const proposalStatusLabels: Record<string, string> = {
  PROPOSED: '等待选择方案',
  APPLIED_PENDING_APPROVAL: '修改版待确认',
  APPROVED: '已确认使用',
  REJECTED: '未采用',
  ROLLED_BACK: '已恢复原稿',
}

function shortId(value: string): string {
  return value.slice(0, 8)
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未记录'
  return date.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function DirectorGenerationHistory({
  records,
}: DirectorGenerationHistoryProps) {
  if (records.length === 0) return null

  return (
    <section className="director-generation-history" aria-label="Director 审查历史">
      <header>
        <span><History size={16} />审查历史</span>
        <small>{records.length} 次</small>
      </header>
      <ol>
        {records.map((record) => (
          <li
            className={`director-generation-history__item is-${record.status.toLowerCase()}`}
            key={record.generationRecordId}
          >
            <span className="director-generation-history__status" aria-hidden="true">
              {record.status === 'SUCCEEDED'
                ? <CheckCircle2 size={16} />
                : <XCircle size={16} />}
            </span>
            <div className="director-generation-history__body">
              <div>
                <strong>
                  {record.status === 'SUCCEEDED' ? '已生成审查方案' : '审查失败'}
                </strong>
                <time dateTime={record.createdAt}>{formatTime(record.createdAt)}</time>
              </div>
              <p>
                {record.provider} · {record.model}
                {' · '}
                {record.attemptCount} 次尝试
                {record.repairAttempts ? `（修复 ${record.repairAttempts} 次）` : ''}
                {record.latencyMs == null ? '' : ` · ${record.latencyMs} ms`}
              </p>
              <footer>
                {record.proposalStatus ? (
                  <span>{proposalStatusLabels[record.proposalStatus] ?? record.proposalStatus}</span>
                ) : record.errorCode ? (
                  <code>{record.errorCode}</code>
                ) : null}
                {record.retryOfGenerationRecordId ? (
                  <small>
                    <GitBranch size={12} />
                    重试自 {shortId(record.retryOfGenerationRecordId)}
                  </small>
                ) : (
                  <small>记录 {shortId(record.generationRecordId)}</small>
                )}
              </footer>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}
