import { Clock3, ShieldCheck } from 'lucide-react'
import type { DirectorTimelinePreview as DirectorTimelinePreviewData } from '../../api/client'
import { StatusBadge } from '../ui'

function formatDuration(milliseconds: number): string {
  const seconds = milliseconds / 1000
  return `${seconds.toFixed(seconds >= 10 ? 0 : 1)} 秒`
}

function TimelineRow({
  label,
  dialogueWindowMs,
  durationBudgetMs,
  maxDurationMs,
  changed = false,
}: {
  label: string
  dialogueWindowMs: number
  durationBudgetMs: number
  maxDurationMs: number
  changed?: boolean
}) {
  const dialogueWidth = Math.min(100, (dialogueWindowMs / maxDurationMs) * 100)
  const budgetPosition = Math.min(100, (durationBudgetMs / maxDurationMs) * 100)
  return (
    <div className={`director-timeline-preview__row ${changed ? 'is-changed' : ''}`}>
      <span>{label}</span>
      <div
        aria-label={`${label}对白 ${formatDuration(dialogueWindowMs)}，场景预算 ${formatDuration(durationBudgetMs)}`}
        className="director-timeline-preview__track"
        role="img"
      >
        <i style={{ width: `${dialogueWidth}%` }} />
        <b aria-hidden="true" style={{ left: `${budgetPosition}%` }} />
      </div>
      <small>{formatDuration(dialogueWindowMs)}</small>
    </div>
  )
}

export function DirectorTimelinePreview({
  preview,
}: {
  preview: DirectorTimelinePreviewData
}) {
  const maxDurationMs = Math.max(
    preview.before.durationBudgetMs,
    preview.before.dialogueWindowMs,
    preview.after.durationBudgetMs,
    preview.after.dialogueWindowMs,
    1,
  )
  const shift = preview.downstreamShiftMs
  const riskMessage = preview.risk === 'DURATION_BUDGET_EXCEEDED'
    ? `修改后对白窗口超出场景预算 ${formatDuration(preview.after.overflowMs)}，应先调整文本或时长，再进入昂贵生成。`
    : preview.risk === 'DOWNSTREAM_TIMING_SHIFT'
      ? `预计后续窗口${shift > 0 ? '顺延' : '提前'} ${formatDuration(Math.abs(shift))}，正式时间线仍保持不变。`
      : '对白窗口未改变；仍需结合导演判断确认内容质量。'

  return (
    <section
      aria-label="低成本时间线预览"
      className={`director-timeline-preview director-timeline-preview--${preview.validationStatus.toLowerCase()}`}
    >
      <header>
        <div>
          <span><Clock3 size={14} />低成本时间线预览</span>
          <small>由剧本派生 · 对白 / 字幕轨道</small>
        </div>
        <StatusBadge
          label={preview.validationStatus === 'PASS' ? '时长可复核' : '需要调整'}
          status={preview.validationStatus}
        />
      </header>
      <div className="director-timeline-preview__legend">
        <span><i />对白窗口（含停顿）</span>
        <span><b />场景时长预算</span>
      </div>
      <TimelineRow
        dialogueWindowMs={preview.before.dialogueWindowMs}
        durationBudgetMs={preview.before.durationBudgetMs}
        label="修改前"
        maxDurationMs={maxDurationMs}
      />
      <TimelineRow
        changed
        dialogueWindowMs={preview.after.dialogueWindowMs}
        durationBudgetMs={preview.after.durationBudgetMs}
        label="修改后"
        maxDurationMs={maxDurationMs}
      />
      <p>{riskMessage}</p>
      <footer>
        <ShieldCheck size={14} />
        正式时间线、视频、配音和音乐均未写入或覆盖
      </footer>
    </section>
  )
}
