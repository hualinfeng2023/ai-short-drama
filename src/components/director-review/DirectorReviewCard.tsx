import {
  Check,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  Sparkles,
  WandSparkles,
  X,
} from 'lucide-react'
import type { DirectorReviewProposal } from '../../api/client'
import { Button, StatusBadge } from '../ui'
import { DirectorTimelinePreview } from './DirectorTimelinePreview'

export type DirectorReviewAction =
  | { type: 'EXECUTE'; proposal: DirectorReviewProposal; optionId: string }
  | {
      type: 'DECIDE'
      proposal: DirectorReviewProposal
      decision: 'APPROVE' | 'REJECT' | 'ROLLBACK'
    }

interface DirectorReviewCardProps {
  proposal: DirectorReviewProposal | null
  busy: boolean
  selectedOptionId?: string
  targetLabel?: string
  onReview: () => void
  onSelectOption: (proposalId: string, optionId: string) => void
  onAction: (action: DirectorReviewAction) => void
}

const DIRECTOR_ISSUE_LABELS: Record<DirectorReviewProposal['issueType'], string> = {
  STORY_LOGIC: '故事逻辑',
  CHARACTER_MOTIVATION: '人物动机',
  AI_DIALOGUE: '对白自然度',
  PACING: '场景节奏',
}

const DIRECTOR_FIELD_LABELS: Record<string, string> = {
  text: '对白',
  purpose: '场景目的',
  emotion: '情绪',
  speech_rate: '语速',
  pause_after_ms: '句后停顿',
  bgm_intent: '背景音乐意图',
  sfx_intents: '音效意图',
}

function directorValues(value: Record<string, unknown>): string {
  return Object.entries(value)
    .map(([key, item]) => {
      const display = Array.isArray(item) ? item.join('、') : String(item)
      return `${DIRECTOR_FIELD_LABELS[key] ?? key}：${display}`
    })
    .join('；')
}

export function DirectorReviewCard({
  proposal,
  busy,
  selectedOptionId,
  targetLabel = '这一场',
  onReview,
  onSelectOption,
  onAction,
}: DirectorReviewCardProps) {
  if (!proposal) {
    return (
      <section className="director-review-entry">
        <div>
          <span><WandSparkles size={14} />AI Director</span>
          <strong>审查{targetLabel}的逻辑、动机、对白与节奏</strong>
          <p>先给出导演判断和 2–3 个方案，不会自动修改剧本或触发媒体生成。</p>
        </div>
        <Button disabled={busy} onClick={onReview} size="sm" variant="secondary">
          {busy ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}
          开始审查
        </Button>
      </section>
    )
  }

  const resolvedOptionId = selectedOptionId ?? proposal.recommendedOption
  const selectedOption =
    proposal.alternatives.find((item) => item.optionId === resolvedOptionId)
    ?? proposal.alternatives[0]
  const finalStatus = ['APPROVED', 'REJECTED', 'ROLLED_BACK'].includes(proposal.status)

  return (
    <section className={`director-review director-review--${proposal.status.toLowerCase()}`}>
      <header>
        <div>
          <span>
            <WandSparkles size={14} />
            AI Director · {DIRECTOR_ISSUE_LABELS[proposal.issueType]}
          </span>
          <strong>{proposal.observation}</strong>
        </div>
        <StatusBadge status={proposal.status} />
      </header>
      <p>{proposal.rationale}</p>
      <div className="director-review__meta">
        <span>判断置信度 {Math.round(proposal.confidence * 100)}%</span>
        <span>
          {proposal.estimatedCostUsd === 0
            ? '不触发媒体生成'
            : `预计成本 $${proposal.estimatedCostUsd}`}
        </span>
        <span>
          影响 {proposal.affectedObjects.length} 项 · 保留 {proposal.preservedObjects.length} 项
        </span>
      </div>

      {proposal.status === 'PROPOSED' ? (
        <>
          <div className="director-review__options" role="radiogroup" aria-label="Director 修复方案">
            {proposal.alternatives.map((option) => {
              const selected = option.optionId === selectedOption?.optionId
              return (
                <button
                  aria-checked={selected}
                  className={selected ? 'is-selected' : ''}
                  key={option.optionId}
                  onClick={() => onSelectOption(proposal.proposalId, option.optionId)}
                  role="radio"
                  type="button"
                >
                  <span>
                    <strong>{option.title}</strong>
                    {option.optionId === proposal.recommendedOption ? <em>推荐</em> : null}
                  </span>
                  <p>{option.rationale}</p>
                  <small>{directorValues(option.proposedChange.changes)}</small>
                </button>
              )
            })}
          </div>
          <footer>
            <Button
              disabled={busy}
              onClick={() => onAction({ type: 'DECIDE', proposal, decision: 'REJECT' })}
              size="sm"
              variant="ghost"
            >
              <X size={14} />拒绝建议
            </Button>
            <Button
              disabled={busy || !selectedOption}
              onClick={() => selectedOption && onAction({
                type: 'EXECUTE',
                proposal,
                optionId: selectedOption.optionId,
              })}
              size="sm"
            >
              <Check size={14} />采用所选方案
            </Button>
          </footer>
        </>
      ) : null}

      {proposal.comparison ? (
        <>
          <div className="director-review__comparison">
            <article>
              <span>修改前</span>
              <p>{directorValues(proposal.comparison.before)}</p>
            </article>
            <article>
              <span>修改后</span>
              <p>{directorValues(proposal.comparison.after)}</p>
            </article>
            <small>
              估算台词时长（不含停顿）：
              {(proposal.comparison.estimatedDurationBeforeMs / 1000).toFixed(1)} 秒
              {' → '}
              {(proposal.comparison.estimatedDurationAfterMs / 1000).toFixed(1)} 秒
            </small>
          </div>
          {proposal.comparison.timelinePreview ? (
            <DirectorTimelinePreview preview={proposal.comparison.timelinePreview} />
          ) : null}
        </>
      ) : null}

      {proposal.status === 'APPLIED_PENDING_APPROVAL' ? (
        <footer>
          <Button
            disabled={busy}
            onClick={() => onAction({ type: 'DECIDE', proposal, decision: 'ROLLBACK' })}
            size="sm"
            variant="secondary"
          >
            <RotateCcw size={14} />回退修改
          </Button>
          <Button
            disabled={busy}
            onClick={() => onAction({ type: 'DECIDE', proposal, decision: 'APPROVE' })}
            size="sm"
          >
            <Check size={14} />批准修改版
          </Button>
        </footer>
      ) : null}

      {finalStatus ? (
        <footer>
          <span>
            {proposal.status === 'APPROVED'
              ? '修改版已批准，审计记录已保存。'
              : proposal.status === 'ROLLED_BACK'
                ? '恢复版本已创建，两个版本均可追溯。'
                : '建议已拒绝，没有修改剧本。'}
          </span>
          <Button disabled={busy} onClick={onReview} size="sm" variant="ghost">
            <RefreshCw size={14} />重新审查
          </Button>
        </footer>
      ) : null}
    </section>
  )
}
