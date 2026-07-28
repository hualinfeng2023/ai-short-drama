import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ArrowLeft, Ban, Check, Eye, Film, LoaderCircle, LockKeyhole, Maximize2, RefreshCw, Save, Sparkles, UnlockKeyhole, ZoomIn, ZoomOut } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  approveStoryboardVersion,
  cancelPersistedJob,
  fetchProject,
  fetchStoryboardWorkspace,
  recommendShotDuration,
  regenerateStoryboardShot,
  rewriteShotAction,
  rewriteShotEndState,
  updateShotLock,
  updateStructuredShotSpec,
  type ShotLockScope,
  type ShotDurationRecommendation,
  type ShotPromptAdapter,
  type StructuredShotSpec,
  type StoryboardWorkspace,
} from '../api/client'
import {
  Button,
  DurationSlider,
  EmptyState,
  HintTooltip,
  Modal,
  PageHeader,
  StatusBadge,
  Surface,
  Tab,
  TabList,
  Tabs,
} from '../components/ui'
import { ImpactConfirmModal } from '../components/ConfirmModal'
import { PageLoadingSkeleton } from '../components/PageLoadingSkeleton'
import { ServiceRequiredState } from '../components/ServiceRequiredState'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'
import type { JobStatus, ProjectRecord } from '../types'
import { localizeDisplayText } from '../utils/localizeDisplayText'
import {
  getEndActionState,
  getShotFieldReviewStatus,
  getShotIssuePresentation,
  getStoryboardReviewSummary,
} from '../utils/storyboardReview'

const ACTIVE_JOB_STATUSES = new Set<JobStatus>([
  'PENDING',
  'RETRY_WAIT',
  'RUNNING',
  'CANCEL_REQUESTED',
])

/** 与后端 StoryboardShotRegenerateRequest.note max_length 对齐 */
const REGEN_NOTE_MAX = 500

/** 卡片已单独展示镜头号，去掉标题里重复的 code 前缀/后缀。 */
function displayShotTitle(title: string, code: string): string {
  let next = title.trim()
  const suffix = ` · ${code}`
  if (next.endsWith(suffix)) next = next.slice(0, -suffix.length).trim()
  const prefix = `${code} `
  if (next.startsWith(prefix)) next = next.slice(prefix.length).trim()
  next = next.replace(/^S\d+\s+/i, '').trim()
  return next || title
}

type DetailShotState = {
  shotSpecId: string
  code: string
  title: string
  description: string
  dialogue: string
  durationMs: number
  shotSize: string
  cameraMovement: string
  status: string
  imageUrl: string
  imagePrompt: string
  delivery: string
  renderMode: string
  audioCues: string[]
  cameraNotes: string[]
  timelineNotes: string[]
}

function deliveryLabel(value: string): string {
  if (value === 'ACTION') return '动作画面'
  if (value === 'VOICE_OVER') return '画外音'
  if (value === 'DIALOGUE') return '对白'
  return value
}

function renderModeLabel(value: string): string {
  if (value === 'BLACK_FRAME') return '黑场（本地合成）'
  return '生图'
}

function openShotDetail(
  shot: StoryboardWorkspace['shots'][number],
): DetailShotState {
  return {
    shotSpecId: shot.shotSpecId,
    code: shot.code,
    title: shot.title,
    description: shot.description,
    dialogue: shot.dialogue,
    durationMs: shot.durationMs,
    shotSize: shot.shotSize,
    cameraMovement: shot.cameraMovement,
    status: shot.status,
    imageUrl: shot.imageUrl ?? '',
    imagePrompt: shot.imagePrompt ?? '',
    delivery: shot.delivery ?? '',
    renderMode: shot.renderMode ?? 'IMAGE',
    audioCues: shot.audioCues ?? [],
    cameraNotes: shot.cameraNotes ?? [],
    timelineNotes: shot.timelineNotes ?? [],
  }
}

const STRUCTURED_SECTION_FIELDS = [
  ['visual_content', '画面内容'],
  ['start_state', '起始状态'],
  ['end_state', '结束状态'],
  ['camera', '摄影机'],
  ['lighting', '灯光'],
  ['art_direction', '美术指导'],
  ['technique', '导演手法'],
  ['performance', '表演'],
  ['audio', '声音'],
  ['continuity', '连续性'],
  ['generation', '生成参数'],
] as const

type StructuredSectionField = typeof STRUCTURED_SECTION_FIELDS[number][0]

function audioSummary(spec: StructuredShotSpec): string {
  if (spec.audio.dialogue) return `对白：${spec.audio.dialogue}`
  if (spec.audio.voice_over) return `画外音：${spec.audio.voice_over}`
  const cues = [...spec.audio.ambience, ...spec.audio.sfx]
  return cues.join('、') || '无对白 / 待设计'
}

function fieldValue(spec: StructuredShotSpec, fieldPath: string): unknown {
  return spec[fieldPath as keyof StructuredShotSpec]
}

function updateShotDuration(spec: StructuredShotSpec, durationSec: number): StructuredShotSpec {
  const pacing = /^\d+(?:\.\d+)?\s*秒/.test(spec.technique.pacing)
    ? spec.technique.pacing.replace(/^\d+(?:\.\d+)?\s*秒/, `${durationSec} 秒`)
    : spec.technique.pacing
  return {
    ...spec,
    duration_sec: durationSec,
    technique: { ...spec.technique, pacing },
  }
}

function FocusedShotReview({
  shot,
  canEdit,
  saving,
  onPreview,
  onRegenerate,
  onRewriteApplied,
  onSave,
}: {
  shot: StoryboardWorkspace['shots'][number]
  canEdit: boolean
  saving: boolean
  onPreview: () => void
  onRegenerate: () => void
  onRewriteApplied: () => Promise<void>
  onSave: (spec: StructuredShotSpec, adapter: ShotPromptAdapter) => Promise<void>
}) {
  const [draft, setDraft] = useState<StructuredShotSpec>(() => structuredClone(shot.shotSpec))
  const [dirty, setDirty] = useState(false)
  const [editorError, setEditorError] = useState<string | null>(null)
  const [durationRecommendation, setDurationRecommendation] =
    useState<ShotDurationRecommendation | null>(null)
  const [durationRecommendationBusy, setDurationRecommendationBusy] = useState(false)
  const [durationRecommendationError, setDurationRecommendationError] = useState<string | null>(null)
  const [actionRewriteBusy, setActionRewriteBusy] = useState(false)
  const [actionRewriteFeedback, setActionRewriteFeedback] = useState<string | null>(null)
  const [actionRewriteError, setActionRewriteError] = useState<string | null>(null)
  const [endStateRewriteBusy, setEndStateRewriteBusy] = useState(false)
  const [endStateRewriteFeedback, setEndStateRewriteFeedback] = useState<string | null>(null)
  const [endStateRewriteError, setEndStateRewriteError] = useState<string | null>(null)
  const issues = shot.validationReport.issues
  const effectiveLocks = shot.lockSnapshot.effective_fields ?? {}
  const actionReviewStatus = getShotFieldReviewStatus(
    issues,
    'visual_content.action',
    draft.visual_content.action !== shot.shotSpec.visual_content.action,
    '画面动作',
  )
  const durationReviewStatus = getShotFieldReviewStatus(
    issues,
    'duration_sec',
    draft.duration_sec !== shot.shotSpec.duration_sec,
    '镜头时长',
  )
  const endStateReviewStatus = getShotFieldReviewStatus(
    issues,
    'end_state.action_state',
    getEndActionState(draft) !== getEndActionState(shot.shotSpec),
    '结尾状态',
  )

  useEffect(() => {
    if (dirty) return
    setDraft(structuredClone(shot.shotSpec))
  }, [dirty, shot.shotSpec])

  function updateEndActionState(value: string) {
    setDraft((current) => ({
      ...current,
      end_state: { ...current.end_state, action_state: value },
    }))
    setDurationRecommendation(null)
    setDirty(true)
  }

  async function requestDurationRecommendation() {
    setDurationRecommendationBusy(true)
    setDurationRecommendationError(null)
    try {
      setDurationRecommendation(await recommendShotDuration(shot.shotSpecId, draft))
    } catch (reason) {
      setDurationRecommendationError(
        reason instanceof Error ? reason.message : '暂时无法生成推荐时长',
      )
    } finally {
      setDurationRecommendationBusy(false)
    }
  }

  async function rewriteActionAndRevalidate() {
    setActionRewriteBusy(true)
    setActionRewriteError(null)
    setActionRewriteFeedback(null)
    try {
      if (dirty) {
        await onSave(draft, shot.promptAdapter)
      }
      const result = await rewriteShotAction(shot.shotSpecId, draft)
      setDraft(result.shotSpec)
      setDurationRecommendation(null)
      setDirty(false)
      setActionRewriteFeedback(
        result.provider === 'deterministic'
          ? '已用本地规则精简动作，并重新运行镜头检查。'
          : `AI 已精简动作：${result.reason}`,
      )
      await onRewriteApplied()
    } catch (reason) {
      setActionRewriteError(
        reason instanceof Error ? reason.message : 'AI 改写失败，请稍后重试',
      )
    } finally {
      setActionRewriteBusy(false)
    }
  }

  async function rewriteEndStateAndRevalidate() {
    setEndStateRewriteBusy(true)
    setEndStateRewriteError(null)
    setEndStateRewriteFeedback(null)
    try {
      if (dirty) {
        await onSave(draft, shot.promptAdapter)
      }
      const result = await rewriteShotEndState(shot.shotSpecId, draft)
      setDraft(result.shotSpec)
      setDirty(false)
      setEndStateRewriteFeedback(
        result.provider === 'deterministic'
          ? '已补全结尾状态，并重新运行镜头检查。'
          : `AI 已优化结尾状态：${result.reason}`,
      )
      await onRewriteApplied()
    } catch (reason) {
      setEndStateRewriteError(
        reason instanceof Error ? reason.message : 'AI 优化失败，请稍后重试',
      )
    } finally {
      setEndStateRewriteBusy(false)
    }
  }

  async function save() {
    setEditorError(null)
    try {
      await onSave(draft, shot.promptAdapter)
      setDirty(false)
    } catch (reason) {
      setEditorError(reason instanceof Error ? reason.message : '镜头保存失败')
    }
  }

  const conceptImageIsCurrent = Boolean(
    shot.imageUrl && shot.imageStatus !== 'SUSPECT',
  )
  const conceptImagePlaceholder = shot.status === 'QUEUED'
    ? '概念图生成中'
    : shot.imageStatus === 'SUSPECT'
      ? '概念图待重新生成'
      : '暂无概念图'

  return (
    <section className="storyboard-focus" aria-labelledby="storyboard-focus-title">
      <header className="storyboard-focus__header">
        <div>
          <span className="storyboard-focus__code">{shot.code}</span>
          <div>
            <h2 id="storyboard-focus-title">{displayShotTitle(shot.title, shot.code)}</h2>
            <p>{shot.shotSpec.narrative_goal || shot.description}</p>
          </div>
        </div>
        <div className="storyboard-focus__actions">
          <Button
            disabled={!conceptImageIsCurrent}
            onClick={onPreview}
            size="sm"
            variant="secondary"
          >
            <Eye size={14} />查看画面
          </Button>
          {canEdit ? (
            <Button onClick={onRegenerate} size="sm" variant="secondary">
              <RefreshCw size={14} />重生成
            </Button>
          ) : null}
        </div>
      </header>

      <div className="storyboard-focus__review-overview">
        <button
          aria-label={`查看 ${shot.code} 分镜概念图`}
          className="storyboard-focus__concept-preview"
          disabled={!conceptImageIsCurrent}
          onClick={onPreview}
          type="button"
        >
          {conceptImageIsCurrent ? (
            <img
              alt={`${shot.code} 分镜概念图`}
              draggable={false}
              loading="lazy"
              src={shot.imageUrl}
            />
          ) : (
            <span className="storyboard-focus__concept-empty">
              <Film aria-hidden size={22} />
              {conceptImagePlaceholder}
            </span>
          )}
          <span className="storyboard-focus__concept-caption">
            <strong>分镜概念图</strong>
            <small>
              {conceptImageIsCurrent ? '点击查看原图' : '修改后需重新生成'}
            </small>
          </span>
        </button>

        {issues.length > 0 ? (
          <section className="storyboard-focus__diagnostics" aria-label="镜头检查结果">
          <header>
            <div>
              <strong>检查结果</strong>
              <span>
                {issues.some((issue) => issue.severity === 'BLOCKER')
                  ? '先处理阻断项，再保存并重新验证。'
                  : '确认提醒内容后，可以继续审核。'}
              </span>
            </div>
            <span>
              {issues.filter((issue) => issue.severity === 'BLOCKER').length} 阻断
              {' · '}
              {issues.filter((issue) => issue.severity === 'WARNING').length} 提醒
            </span>
          </header>
          <div className="storyboard-focus__issue-list">
            {issues.map((issue) => {
              const presentation = getShotIssuePresentation(issue, draft)
              return (
                <article
                  data-severity={issue.severity}
                  key={`${issue.code}:${issue.field_path}`}
                >
                  <span className="storyboard-focus__issue-icon" aria-hidden>
                    <AlertTriangle size={16} />
                  </span>
                  <div className="storyboard-focus__issue-content">
                    <div>
                      <strong>{presentation.fieldLabel}</strong>
                      <span>{issue.severity === 'BLOCKER' ? '阻断' : '提醒'}</span>
                    </div>
                    <p>{presentation.guidance}</p>
                    <details>
                      <summary>技术详情</summary>
                      <p>{issue.message}</p>
                      <code>{issue.field_path}</code>
                    </details>
                  </div>
                </article>
              )
            })}
          </div>
          </section>
        ) : (
          <div className="storyboard-focus__passed">
            <Check size={17} />
            <div>
              <strong>这个镜头已通过检查</strong>
              <span>可以继续检查其他镜头，或查看画面确认导演意图。</span>
            </div>
          </div>
        )}
      </div>

      <div className="storyboard-focus__form">
        <div className="storyboard-focus__field storyboard-focus__field--duration">
          <div className="storyboard-focus__duration-main">
            <div className="storyboard-focus__field-copy">
              <span className="storyboard-focus__field-title">
                <span>镜头时长</span>
                <HintTooltip label="查看镜头时长填写说明">
                  为主要动作留出足够的生成时间。
                </HintTooltip>
                <StatusBadge {...durationReviewStatus} size="sm" variant="inline" />
              </span>
              {effectiveLocks.duration_sec ? <em>该字段已锁定，请在专业质检中管理。</em> : null}
            </div>
            <Button
              aria-label={durationRecommendation ? '重新生成 AI 推荐时长范围' : '生成 AI 推荐时长范围'}
              disabled={durationRecommendationBusy}
              onClick={() => void requestDurationRecommendation()}
              size="sm"
              variant="ai"
            >
              {durationRecommendationBusy
                ? <LoaderCircle aria-hidden className="spin" size={14} />
                : <Sparkles aria-hidden size={14} />}
              {durationRecommendationBusy
                ? '正在分析…'
                : durationRecommendation
                  ? '重新推荐'
                  : 'AI 推荐'}
            </Button>
          </div>
          <DurationSlider
            disabled={!canEdit || Boolean(effectiveLocks.duration_sec)}
            label="镜头时长"
            max={12}
            min={0.5}
            onChange={(value) => {
              setDraft((current) => updateShotDuration(current, value))
              setDirty(true)
            }}
            recommendedMax={durationRecommendation?.recommendedMaxSec}
            recommendedMin={durationRecommendation?.recommendedMinSec}
            recommendedValue={durationRecommendation?.recommendedDurationSec}
            step={0.5}
            value={draft.duration_sec}
          />
          {durationRecommendationError ? (
            <p className="storyboard-focus__duration-error" role="alert">
              {durationRecommendationError}
            </p>
          ) : null}
          {durationRecommendation ? (
            <div className="storyboard-focus__duration-recommendation" role="status">
              <Sparkles aria-hidden size={15} />
              <div>
                <span>
                  {durationRecommendation.provider === 'deterministic'
                    ? '智能建议（本地规则）'
                    : 'AI 建议'}
                </span>
                <strong>
                  推荐范围 {durationRecommendation.recommendedMinSec}–{durationRecommendation.recommendedMaxSec} 秒
                </strong>
                <small>建议时长 {durationRecommendation.recommendedDurationSec} 秒</small>
                <p>{durationRecommendation.reason}</p>
              </div>
              <Button
                disabled={
                  !canEdit
                  || Boolean(effectiveLocks.duration_sec)
                  || draft.duration_sec === durationRecommendation.recommendedDurationSec
                }
                onClick={() => {
                  setDraft((current) => (
                    updateShotDuration(current, durationRecommendation.recommendedDurationSec)
                  ))
                  setDirty(true)
                }}
                size="sm"
                variant="secondary"
              >
                {draft.duration_sec === durationRecommendation.recommendedDurationSec
                  ? <><Check aria-hidden size={14} />已采用</>
                  : '采用建议'}
              </Button>
            </div>
          ) : null}
        </div>
        <div className="storyboard-focus__field storyboard-focus__wide">
          <div className="storyboard-focus__field-heading">
            <span className="storyboard-focus__field-title">
              <label htmlFor={`shot-action-${shot.shotSpecId}`}>画面动作</label>
              <HintTooltip label="查看画面动作填写说明">
                只描述这个镜头中最重要、最适合被看见的动作。
              </HintTooltip>
              <StatusBadge
                {...actionReviewStatus}
                size="sm"
                variant="inline"
              />
            </span>
            <Button
              disabled={
                actionRewriteBusy
                || saving
                || !canEdit
                || Boolean(effectiveLocks.visual_content)
              }
              onClick={() => void rewriteActionAndRevalidate()}
              size="sm"
              variant="ai"
            >
              {actionRewriteBusy
                ? <LoaderCircle aria-hidden className="spin" size={14} />
                : <Sparkles aria-hidden size={14} />}
              {actionRewriteBusy ? '正在优化…' : 'AI 优化'}
            </Button>
          </div>
          <textarea
            disabled={!canEdit || Boolean(effectiveLocks.visual_content)}
            id={`shot-action-${shot.shotSpecId}`}
            onChange={(event) => {
              setDraft((current) => ({
                ...current,
                visual_content: {
                  ...current.visual_content,
                  action: event.target.value,
                },
              }))
              setDurationRecommendation(null)
              setDirty(true)
            }}
            rows={4}
            value={draft.visual_content.action}
          />
          {effectiveLocks.visual_content ? <em>该字段已锁定，请在专业质检中管理。</em> : null}
          {actionRewriteFeedback ? (
            <p className="storyboard-focus__rewrite-feedback" role="status">
              <Check aria-hidden size={14} />{actionRewriteFeedback}
            </p>
          ) : null}
          {actionRewriteError ? (
            <p className="storyboard-focus__rewrite-error" role="alert">
              {actionRewriteError}
            </p>
          ) : null}
        </div>
        <div className="storyboard-focus__field storyboard-focus__wide">
          <div className="storyboard-focus__field-heading">
            <span className="storyboard-focus__field-title">
              <label htmlFor={`shot-end-state-${shot.shotSpecId}`}>结尾状态</label>
              <HintTooltip label="查看结尾状态填写说明">
                说明动作完成后，人物、物体或环境发生了什么变化。
              </HintTooltip>
              <StatusBadge
                {...endStateReviewStatus}
                size="sm"
                variant="inline"
              />
            </span>
            <Button
              disabled={
                endStateRewriteBusy
                || saving
                || !canEdit
                || Boolean(effectiveLocks.end_state)
              }
              onClick={() => void rewriteEndStateAndRevalidate()}
              size="sm"
              variant="ai"
            >
              {endStateRewriteBusy
                ? <LoaderCircle aria-hidden className="spin" size={14} />
                : <Sparkles aria-hidden size={14} />}
              {endStateRewriteBusy ? '正在优化…' : 'AI 优化'}
            </Button>
          </div>
          <textarea
            disabled={!canEdit || Boolean(effectiveLocks.end_state)}
            id={`shot-end-state-${shot.shotSpecId}`}
            onChange={(event) => updateEndActionState(event.target.value)}
            rows={3}
            value={getEndActionState(draft)}
          />
          {effectiveLocks.end_state ? <em>该字段已锁定，请在专业质检中管理。</em> : null}
          {endStateRewriteFeedback ? (
            <p className="storyboard-focus__rewrite-feedback" role="status">
              <Check aria-hidden size={14} />{endStateRewriteFeedback}
            </p>
          ) : null}
          {endStateRewriteError ? (
            <p className="storyboard-focus__rewrite-error" role="alert">
              {endStateRewriteError}
            </p>
          ) : null}
        </div>
      </div>

      {editorError ? (
        <p className="brief-save-message brief-save-message--error" role="alert">{editorError}</p>
      ) : null}
      <footer className="storyboard-focus__save">
        <span>{dirty ? '有未保存的修改' : '修改后将重新运行镜头检查'}</span>
        <Button disabled={!canEdit || !dirty || saving} onClick={() => void save()}>
          {saving ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}
          保存并重新验证
        </Button>
      </footer>
    </section>
  )
}

function ShotSpecEditorRow({
  shot,
  canEdit,
  saving,
  lockBusy,
  onPreview,
  onRegenerate,
  onSave,
  onToggleLock,
}: {
  shot: StoryboardWorkspace['shots'][number]
  canEdit: boolean
  saving: boolean
  lockBusy: string | null
  onPreview: () => void
  onRegenerate: () => void
  onSave: (spec: StructuredShotSpec, adapter: ShotPromptAdapter) => Promise<void>
  onToggleLock: (
    scope: ShotLockScope,
    fieldPath: string,
    value: unknown,
    locked: boolean,
  ) => Promise<void>
}) {
  const [draft, setDraft] = useState<StructuredShotSpec>(() => structuredClone(shot.shotSpec))
  const [jsonDrafts, setJsonDrafts] = useState<Record<StructuredSectionField, string>>(
    () => Object.fromEntries(
      STRUCTURED_SECTION_FIELDS.map(([field]) => [
        field,
        JSON.stringify(shot.shotSpec[field], null, 2),
      ]),
    ) as Record<StructuredSectionField, string>,
  )
  const [adapter, setAdapter] = useState<ShotPromptAdapter>(shot.promptAdapter)
  const [dirty, setDirty] = useState(false)
  const [editorError, setEditorError] = useState<string | null>(null)

  useEffect(() => {
    if (dirty) return
    setDraft(structuredClone(shot.shotSpec))
    setJsonDrafts(Object.fromEntries(
      STRUCTURED_SECTION_FIELDS.map(([field]) => [
        field,
        JSON.stringify(shot.shotSpec[field], null, 2),
      ]),
    ) as Record<StructuredSectionField, string>)
    setAdapter(shot.promptAdapter)
  }, [dirty, shot.promptAdapter, shot.shotSpec])

  const effectiveLocks = shot.lockSnapshot.effective_fields ?? {}
  const allLocks = shot.lockSnapshot.field_locks ?? []

  function lockFor(scope: ShotLockScope, fieldPath: string) {
    return allLocks.find((item) => item.scope === scope && item.field_path === fieldPath)
  }

  function lockControls(fieldPath: string, value: unknown) {
    const effective = effectiveLocks[fieldPath]
    return (
      <div className="shot-field-locks" aria-label={`${fieldPath} 锁定层级`}>
        {([
          ['PROJECT', '项目'],
          ['SCENE', '场景'],
          ['FIELD', '字段'],
        ] as const).map(([scope, label]) => {
          const existing = lockFor(scope, fieldPath)
          const key = `${shot.shotSpecId}:${scope}:${fieldPath}`
          const locked = Boolean(existing)
          return (
            <button
              aria-pressed={locked}
              className={locked ? 'is-locked' : ''}
              disabled={!canEdit || Boolean(lockBusy)}
              key={scope}
              onClick={() => void onToggleLock(scope, fieldPath, value, locked)}
              title={locked
                ? `${label}锁由 ${existing?.owner ?? '未知'} 设置，点击解除`
                : `以${label}层级锁定此字段`}
              type="button"
            >
              {lockBusy === key
                ? <LoaderCircle className="spin" size={12} />
                : locked
                  ? <LockKeyhole size={12} />
                  : <UnlockKeyhole size={12} />}
              {label}
            </button>
          )
        })}
        {effective ? <small>当前生效：{effective.scope} · {effective.owner}</small> : null}
      </div>
    )
  }

  async function save() {
    setEditorError(null)
    try {
      const next = structuredClone(draft)
      for (const [field] of STRUCTURED_SECTION_FIELDS) {
        next[field] = JSON.parse(jsonDrafts[field]) as never
      }
      next.generation.adapter = adapter
      await onSave(next, adapter)
      setDirty(false)
    } catch (reason) {
      setEditorError(reason instanceof Error ? reason.message : '结构化字段 JSON 格式不正确')
    }
  }

  const blockingIssues = shot.validationReport.issues.filter(
    (item) => item.severity === 'BLOCKER',
  )
  const warningIssues = shot.validationReport.issues.filter(
    (item) => item.severity === 'WARNING',
  )

  return (
    <details className="director-shot-row">
      <summary>
        <span className="director-shot-row__code">
          <strong>{shot.code}</strong>
          <StatusBadge status={shot.reviewStatus === 'NEEDS_REVIEW' ? 'NEEDS_REVIEW' : shot.status} />
        </span>
        <span>{shot.shotSpec.duration_sec.toFixed(1)} 秒</span>
        <span>{shot.shotSpec.visual_content.description}</span>
        <span>{shot.shotSpec.lighting.style}</span>
        <span>{localizeDisplayText(shot.shotSpec.camera.movement)}</span>
        <span>{shot.shotSpec.art_direction.visual_style}</span>
        <span>{shot.shotSpec.technique.pacing}</span>
        <span>{audioSummary(shot.shotSpec)}</span>
      </summary>
      <div className="director-shot-editor">
        <div className="director-shot-editor__toolbar">
          <div>
            <strong>{displayShotTitle(shot.title, shot.code)}</strong>
            <small>
              ShotSpec 是唯一事实源 · {shot.compilerVersion} · {shot.promptAdapter}
            </small>
          </div>
          <div>
            <Button onClick={onPreview} size="sm" variant="secondary">
              <Eye size={14} />查看画面
            </Button>
            {canEdit ? (
              <Button onClick={onRegenerate} size="sm" variant="secondary">
                <RefreshCw size={14} />重生成
              </Button>
            ) : null}
          </div>
        </div>

        {blockingIssues.length || warningIssues.length ? (
          <div className={[
            'shot-validation-report',
            blockingIssues.length ? 'is-blocking' : '',
          ].filter(Boolean).join(' ')}>
            <strong><AlertTriangle size={15} />ShotValidator</strong>
            {[...blockingIssues, ...warningIssues].map((issue) => (
              <p key={`${issue.code}:${issue.field_path}`}>
                <span>{issue.severity === 'BLOCKER' ? '阻断' : '提醒'}</span>
                {issue.message}
                <code>{issue.field_path}</code>
              </p>
            ))}
          </div>
        ) : null}

        <div className="structured-shot-form">
          <label>
            <span>时长（秒）</span>
            {lockControls('duration_sec', draft.duration_sec)}
            <input
              disabled={!canEdit || Boolean(effectiveLocks.duration_sec)}
              min="0.5"
              onChange={(event) => {
                setDraft((current) => ({
                  ...current,
                  duration_sec: Number(event.target.value),
                }))
                setDirty(true)
              }}
              step="0.5"
              type="number"
              value={draft.duration_sec}
            />
          </label>
          <label className="structured-shot-form__wide">
            <span>叙事目标</span>
            {lockControls('narrative_goal', draft.narrative_goal)}
            <textarea
              disabled={!canEdit || Boolean(effectiveLocks.narrative_goal)}
              onChange={(event) => {
                setDraft((current) => ({
                  ...current,
                  narrative_goal: event.target.value,
                }))
                setDirty(true)
              }}
              rows={3}
              value={draft.narrative_goal}
            />
          </label>
          {STRUCTURED_SECTION_FIELDS.map(([field, label]) => (
            <label className="structured-shot-form__json" key={field}>
              <span>{label}</span>
              {lockControls(field, fieldValue(draft, field))}
              <textarea
                aria-label={`${shot.code} ${label}`}
                disabled={!canEdit || Boolean(effectiveLocks[field])}
                onChange={(event) => {
                  setJsonDrafts((current) => ({ ...current, [field]: event.target.value }))
                  setDirty(true)
                }}
                rows={field === 'visual_content' ? 10 : 8}
                spellCheck={false}
                value={jsonDrafts[field]}
              />
            </label>
          ))}
        </div>

        <div className="structured-shot-compile">
          <label>
            <span>Prompt Adapter</span>
            <select
              disabled={!canEdit}
              onChange={(event) => {
                setAdapter(event.target.value as ShotPromptAdapter)
                setDirty(true)
              }}
              value={adapter}
            >
              <option value="generic">Generic</option>
              <option value="veo">Veo</option>
              <option value="kling">Kling</option>
              <option value="seedance">Seedance</option>
            </select>
          </label>
          <div>
            <span>可重新生成的 Prompt 缓存</span>
            <pre>{shot.promptCompiled || '保存 ShotSpec 后自动编译'}</pre>
            <small>输入哈希 {shot.compilerInputHash || '—'} · Prompt 哈希 {shot.promptCompiledHash || '—'}</small>
          </div>
        </div>
        {editorError ? <p className="brief-save-message brief-save-message--error" role="alert">{editorError}</p> : null}
        <div className="director-shot-editor__actions">
          <Button disabled={!canEdit || !dirty || saving} onClick={() => void save()}>
            {saving ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}
            保存并重新编译 Prompt
          </Button>
        </div>
      </div>
    </details>
  )
}

export function StoryboardPage() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const { notify } = useToast()
  const { jobs } = useStudio()
  const [project, setProject] = useState<ProjectRecord | null>(null)
  const [workspace, setWorkspace] = useState<StoryboardWorkspace | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [regenBusyId, setRegenBusyId] = useState<string | null>(null)
  const [saveBusyId, setSaveBusyId] = useState<string | null>(null)
  const [lockBusy, setLockBusy] = useState<string | null>(null)
  const [animaticCancelBusy, setAnimaticCancelBusy] = useState(false)
  const [approveOpen, setApproveOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [previewShot, setPreviewShot] = useState<DetailShotState | null>(null)
  const [previewZoom, setPreviewZoom] = useState(100)
  const [regenTarget, setRegenTarget] = useState<DetailShotState | null>(null)
  const [regenNote, setRegenNote] = useState('')
  const [regenError, setRegenError] = useState<string | null>(null)
  const [selectedShotId, setSelectedShotId] = useState<string | null>(null)
  const [reviewFilter, setReviewFilter] = useState<'ALL' | 'ISSUES'>('ALL')
  const [workspaceMode, setWorkspaceMode] = useState<'REVIEW' | 'QUALITY'>('REVIEW')
  const regenOpenRef = useRef(false)
  const reviewWorkspaceRef = useRef<HTMLDivElement | null>(null)
  regenOpenRef.current = regenTarget !== null

  function openRegen(shot: DetailShotState) {
    // 避免与详情弹窗双 dialog 叠层：下层弹窗会拦截确认按钮点击
    setPreviewShot(null)
    setPreviewZoom(100)
    setRegenError(null)
    setRegenTarget(shot)
    setRegenNote('')
  }

  const activeAnimaticJob = useMemo(() => {
    if (!projectId) return null
    const fromJobs = jobs
      .filter(
        (job) =>
          job.projectId === projectId
          && job.jobType === 'GENERATE_ANIMATIC'
          && ACTIVE_JOB_STATUSES.has(job.status),
      )
      .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
    if (fromJobs[0]) return fromJobs[0]
    const nodeJobId = workspace?.workflow?.nodes
      .filter(
        (node) =>
          (node.nodeKey === 'animatic.render' || node.nodeKey.startsWith('animatic.render.'))
          && node.jobId
          && !['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(node.status),
      )
      .map((node) => node.jobId)
      .find((jobId): jobId is string => Boolean(jobId))
    return nodeJobId ? { id: nodeJobId, status: 'RUNNING' as JobStatus, stage: '正在装配' } : null
  }, [jobs, projectId, workspace?.workflow?.nodes])

  const refresh = useCallback(async () => {
    if (!projectId) return
    const [nextProject, nextWorkspace] = await Promise.all([
      fetchProject(projectId),
      fetchStoryboardWorkspace(projectId),
    ])
    setProject(nextProject)
    setWorkspace(nextWorkspace)
  }, [projectId])

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        await refresh()
        // 重生成弹窗打开时保留错误提示，避免 3s 轮询清掉失败原因
        if (active && !regenOpenRef.current) setError(null)
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : '分镜读取失败')
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    const interval = window.setInterval(load, 3000)
    return () => { active = false; window.clearInterval(interval) }
  }, [refresh])

  async function approve() {
    if (!project || !workspace?.storyboard || busy) return
    setBusy(true)
    setError(null)
    try {
      await approveStoryboardVersion(workspace.storyboard.id, project.lockVersion)
      setApproveOpen(false)
      notify('第 4 阶段已批准，正式制作任务已入队。')
      navigate(`/tasks?project=${project.id}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '第 4 阶段批准失败')
    } finally {
      setBusy(false)
    }
  }

  async function confirmRegenerate() {
    if (!project || !regenTarget || regenBusyId) return
    const note = regenNote.trim()
    if (note.length > REGEN_NOTE_MAX) {
      setRegenError(`修改意见最多 ${REGEN_NOTE_MAX} 字，当前 ${note.length} 字`)
      return
    }
    const target = regenTarget
    setRegenBusyId(target.shotSpecId)
    setRegenError(null)
    setError(null)
    try {
      await regenerateStoryboardShot(target.shotSpecId, project.lockVersion, note || undefined)
      setRegenTarget(null)
      setRegenNote('')
      setPreviewShot(null)
      notify(`${target.code} 已开始重生成，完成后会自动刷新节奏样片。`)
      await refresh()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '分镜重生成失败'
      setRegenError(message)
      setError(message)
      notify(message, 'error')
    } finally {
      setRegenBusyId(null)
    }
  }

  async function saveStructuredShot(
    shot: StoryboardWorkspace['shots'][number],
    spec: StructuredShotSpec,
    adapter: ShotPromptAdapter,
  ) {
    if (saveBusyId) return
    setSaveBusyId(shot.shotSpecId)
    setError(null)
    try {
      await updateStructuredShotSpec(
        shot.shotSpecId,
        shot.shotLockVersion,
        spec,
        adapter,
      )
      notify(`${shot.code} 已保存，Prompt 已按 ${adapter} 重新编译。`)
      await refresh()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '结构化镜头保存失败'
      setError(message)
      throw reason
    } finally {
      setSaveBusyId(null)
    }
  }

  async function toggleShotLock(
    shot: StoryboardWorkspace['shots'][number],
    scope: ShotLockScope,
    fieldPath: string,
    value: unknown,
    locked: boolean,
  ) {
    if (!project || lockBusy) return
    const targetId = scope === 'PROJECT'
      ? project.id
      : scope === 'SCENE'
        ? shot.sceneId
        : shot.shotSpecId
    const key = `${shot.shotSpecId}:${scope}:${fieldPath}`
    setLockBusy(key)
    setError(null)
    try {
      await updateShotLock(project.id, project.lockVersion, {
        scope,
        targetId,
        fieldPath,
        locked: !locked,
        value,
      })
      notify(`${fieldPath} 已${locked ? '解除' : '设置'}${scope === 'PROJECT' ? '项目' : scope === 'SCENE' ? '场景' : '字段'}锁。`)
      await refresh()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '镜头字段锁更新失败'
      setError(message)
    } finally {
      setLockBusy(null)
    }
  }

  async function stopAnimatic() {
    if (!activeAnimaticJob || animaticCancelBusy) return
    setAnimaticCancelBusy(true)
    setError(null)
    try {
      await cancelPersistedJob(activeAnimaticJob.id)
      notify('已发送停止请求，节奏样片会在当前检查点结束。', 'info')
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '停止节奏样片失败')
    } finally {
      setAnimaticCancelBusy(false)
    }
  }

  if (!loading && (!project || !workspace || !projectId)) {
    return <ServiceRequiredState feature="动态分镜" projectId={projectId} />
  }
  if (loading || !project || !workspace || !projectId) {
    return <PageLoadingSkeleton label="正在读取动态分镜" stage="镜头序列与节奏样片" />
  }
  if (!workspace.storyboard) {
    return <div className="page page--storyboard"><PageHeader title="动态分镜审核" description="批准前期资产后，系统会在这里装配镜头序列、节奏样片与任务依赖。" actions={<Link className="button button--secondary button--md" to={`/projects/${projectId}/preproduction`}><ArrowLeft size={16} />返回第 3 阶段</Link>} /><EmptyState title="分镜尚未生成" description="先完成第 3 阶段的前期资产锁定；任务启动后，本页会自动显示动态拆镜进度。" action={<div className="empty-state__actions"><Link className="button button--primary button--md" to={`/projects/${projectId}/preproduction`}>检查前期资产</Link><Link className="button button--secondary button--md" to={`/tasks?project=${projectId}`}>查看生成任务</Link></div>} /></div>
  }

  const canRegenerate =
    workspace.storyboard.status !== 'APPROVED'
    && project.status !== 'STORYBOARD_APPROVED'
  const reviewSummary = getStoryboardReviewSummary(
    workspace.shots,
    workspace.storyboard.status === 'APPROVED' || project.status === 'STORYBOARD_APPROVED',
  )
  const issueShots = workspace.shots.filter(
    (shot) => shot.validationReport.issues.length > 0,
  )
  const selectedShot = workspace.shots.find(
    (shot) => shot.shotSpecId === selectedShotId,
  ) ?? issueShots[0] ?? workspace.shots[0]
  const visibleShots = reviewFilter === 'ISSUES' ? issueShots : workspace.shots
  const totalDurationSeconds = Math.round(
    workspace.shots.reduce((sum, shot) => sum + shot.durationMs, 0) / 1000,
  )

  const phaseCopy = {
    APPROVED: {
      description: '这一版分镜已经锁定，正式制作将使用当前镜头规范。',
      title: '第 4 阶段已批准',
    },
    BLOCKED: {
      description: `先修复阻断项；全片另有 ${reviewSummary.warningCount} 条提醒可继续确认。`,
      title: `${reviewSummary.blockerCount} 个阻断问题待修复`,
    },
    READY: {
      description: '所有镜头均已通过检查，可以进行最终确认并进入正式制作。',
      title: '动态分镜已准备好批准',
    },
    WARNINGS: {
      description: `没有阻断问题，仍有 ${reviewSummary.warningCount} 个提醒需要确认。`,
      title: '检查提醒后即可批准',
    },
  }[reviewSummary.phase]

  function focusFirstIssue() {
    const target = issueShots[0] ?? selectedShot
    if (!target) return
    setWorkspaceMode('REVIEW')
    setReviewFilter('ISSUES')
    setSelectedShotId(target.shotSpecId)
    window.requestAnimationFrame(() => {
      reviewWorkspaceRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  return <div className="page page--storyboard" data-aspect={project.aspectRatio}>
    <PageHeader
      title="动态分镜审核"
      description="按成片顺序检查镜头，优先修复阻断问题，确认后进入正式制作。"
      actions={
        <>
          <Link className="button button--secondary button--md" to={`/projects/${projectId}/preproduction`}>
            <ArrowLeft size={16} />返回第 3 阶段
          </Link>
          <Button onClick={() => void refresh()} variant="secondary">
            <RefreshCw size={16} />刷新
          </Button>
        </>
      }
    />
    {error ? <div className="brief-save-message brief-save-message--error" role="alert">{error}</div> : null}
    <Surface
      className={`storyboard-review-status is-${reviewSummary.phase.toLowerCase()}`}
      outline={reviewSummary.phase === 'BLOCKED' ? 'danger' : reviewSummary.phase === 'WARNINGS' ? 'warning' : 'none'}
      padding="md"
      tone={reviewSummary.phase === 'BLOCKED' ? 'danger' : reviewSummary.phase === 'WARNINGS' ? 'warning' : reviewSummary.phase === 'READY' ? 'success' : 'subtle'}
    >
      <div className="storyboard-review-status__message">
        {reviewSummary.phase === 'BLOCKED' || reviewSummary.phase === 'WARNINGS'
          ? <AlertTriangle size={20} />
          : <Check size={20} />}
        <div>
          <strong>{phaseCopy.title}</strong>
          <span>{phaseCopy.description}</span>
        </div>
      </div>
      <div className="storyboard-review-status__meta" aria-label="分镜版本信息">
        <span>第 {workspace.storyboard.version} 版</span>
        <span>{workspace.shots.length} 个镜头</span>
        <span>{totalDurationSeconds} 秒</span>
      </div>
      {reviewSummary.phase === 'BLOCKED' || reviewSummary.phase === 'WARNINGS' ? (
        <Button
          onClick={focusFirstIssue}
          variant={reviewSummary.phase === 'BLOCKED' ? 'danger' : 'secondary'}
        >
          <AlertTriangle size={16} />
          {reviewSummary.phase === 'BLOCKED' ? '修复阻断项' : '检查提醒'}
        </Button>
      ) : reviewSummary.phase === 'READY' ? (
        <Button
          disabled={busy || project.status !== 'STORYBOARD_READY'}
          onClick={() => setApproveOpen(true)}
        >
          {busy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}
          批准进入正式制作
        </Button>
      ) : (
        <StatusBadge status={workspace.gate?.status ?? workspace.storyboard.status} />
      )}
    </Surface>

    <Surface className="storyboard-review-shell" padding="none">
      <div className="storyboard-review-toolbar">
        <div>
          <p className="eyebrow">审核工作台</p>
          <h2>按镜头完成检查与修复</h2>
        </div>
        <Tabs>
          <TabList aria-label="分镜审核视图">
            <Tab
              controls="storyboard-review-panel"
              onClick={() => setWorkspaceMode('REVIEW')}
              selected={workspaceMode === 'REVIEW'}
            >
              导演审核
            </Tab>
            <Tab
              controls="storyboard-quality-panel"
              onClick={() => setWorkspaceMode('QUALITY')}
              selected={workspaceMode === 'QUALITY'}
            >
              专业质检
            </Tab>
          </TabList>
        </Tabs>
      </div>

      {workspaceMode === 'REVIEW' ? (
        <div
          className="storyboard-review-workspace"
          id="storyboard-review-panel"
          ref={reviewWorkspaceRef}
          role="tabpanel"
        >
          <aside className="storyboard-shot-navigator" aria-label="镜头导航">
            <div className="storyboard-shot-navigator__heading">
              <div>
                <strong>镜头顺序</strong>
                <span>{reviewFilter === 'ISSUES' ? `${issueShots.length} 个需要处理` : `${workspace.shots.length} 个镜头`}</span>
              </div>
              <div className="storyboard-shot-navigator__filters">
                <button
                  aria-pressed={reviewFilter === 'ALL'}
                  onClick={() => setReviewFilter('ALL')}
                  type="button"
                >
                  全部
                </button>
                <button
                  aria-pressed={reviewFilter === 'ISSUES'}
                  onClick={() => setReviewFilter('ISSUES')}
                  type="button"
                >
                  只看问题
                </button>
              </div>
            </div>
            <div className="storyboard-shot-navigator__list">
              {visibleShots.map((shot) => {
                const blockers = shot.validationReport.issues.filter(
                  (issue) => issue.severity === 'BLOCKER',
                ).length
                const warnings = shot.validationReport.issues.filter(
                  (issue) => issue.severity === 'WARNING',
                ).length
                return (
                  <button
                    aria-current={selectedShot?.shotSpecId === shot.shotSpecId ? 'true' : undefined}
                    key={shot.shotSpecId}
                    onClick={() => setSelectedShotId(shot.shotSpecId)}
                    type="button"
                  >
                    <span className="storyboard-shot-navigator__code">{shot.code}</span>
                    <span className="storyboard-shot-navigator__title">
                      <strong>{displayShotTitle(shot.title, shot.code)}</strong>
                      <small>{shot.shotSpec.duration_sec.toFixed(1)} 秒 · {shot.shotSpec.visual_content.description}</small>
                    </span>
                    <span
                      className={[
                        'storyboard-shot-navigator__state',
                        blockers ? 'is-blocking' : warnings ? 'is-warning' : 'is-passed',
                      ].join(' ')}
                    >
                      {blockers ? `阻断 ${blockers}` : warnings ? `提醒 ${warnings}` : '通过'}
                    </span>
                  </button>
                )
              })}
              {visibleShots.length === 0 ? (
                <div className="storyboard-shot-navigator__empty">
                  <Check size={18} />
                  <span>当前没有需要处理的问题</span>
                </div>
              ) : null}
            </div>
          </aside>
          <main>
            {selectedShot ? (
              <FocusedShotReview
                canEdit={canRegenerate}
                key={selectedShot.shotSpecId}
                onPreview={() => {
                  setPreviewShot(openShotDetail(selectedShot))
                  setPreviewZoom(100)
                }}
                onRegenerate={() => openRegen(openShotDetail(selectedShot))}
                onRewriteApplied={async () => {
                  setError(null)
                  setSelectedShotId(selectedShot.shotSpecId)
                  await refresh()
                }}
                onSave={async (spec, adapter) => {
                  setSelectedShotId(selectedShot.shotSpecId)
                  await saveStructuredShot(selectedShot, spec, adapter)
                }}
                saving={saveBusyId === selectedShot.shotSpecId}
                shot={selectedShot}
              />
            ) : (
              <EmptyState
                title="没有可审核的镜头"
                description="刷新页面，或返回第 3 阶段检查分镜生成任务。"
              />
            )}
          </main>
        </div>
      ) : (
        <div
          className="storyboard-quality-panel"
          id="storyboard-quality-panel"
          role="tabpanel"
        >
          <div className="section-heading">
            <div>
              <p className="eyebrow">完整镜头规范</p>
              <h2>ShotSpec 专业质检</h2>
              <p>用于逐字段排查、管理三层锁和查看 Prompt 编译结果。所有修改与导演审核共用同一份 ShotSpec。</p>
            </div>
          </div>
          <div className="director-shot-table">
            <div className="director-shot-table__head" aria-hidden>
              <span>镜头</span>
              <span>时长</span>
              <span>画面内容</span>
              <span>灯光</span>
              <span>运镜</span>
              <span>画风</span>
              <span>手法</span>
              <span>声音</span>
            </div>
            {workspace.shots.map((shot) => (
              <ShotSpecEditorRow
                canEdit={canRegenerate}
                key={shot.shotSpecId}
                lockBusy={lockBusy}
                onPreview={() => {
                  setPreviewShot(openShotDetail(shot))
                  setPreviewZoom(100)
                }}
                onRegenerate={() => openRegen(openShotDetail(shot))}
                onSave={(spec, adapter) => saveStructuredShot(shot, spec, adapter)}
                onToggleLock={(scope, fieldPath, value, locked) => (
                  toggleShotLock(shot, scope, fieldPath, value, locked)
                )}
                saving={saveBusyId === shot.shotSpecId}
                shot={shot}
              />
            ))}
          </div>
        </div>
      )}
    </Surface>

    <Surface className="approval-card">
      <p className="eyebrow">节奏样片</p>
      <h2>低成本节奏样片</h2>
      {workspace.storyboard.animaticUrl && !activeAnimaticJob ? (
        <video controls preload="metadata" src={workspace.storyboard.animaticUrl} />
      ) : activeAnimaticJob ? (
        <div className="preview-media-wait">
          <LoaderCircle className="spin" size={20} />
          <span>
            {activeAnimaticJob.status === 'CANCEL_REQUESTED'
              ? '正在停止'
              : activeAnimaticJob.stage || '正在装配'}
          </span>
          <Button
            disabled={animaticCancelBusy || activeAnimaticJob.status === 'CANCEL_REQUESTED'}
            onClick={() => void stopAnimatic()}
            size="sm"
            variant="secondary"
          >
            {animaticCancelBusy || activeAnimaticJob.status === 'CANCEL_REQUESTED'
              ? <LoaderCircle className="spin" size={14} />
              : <Ban size={14} />}
            {activeAnimaticJob.status === 'CANCEL_REQUESTED' ? '停止中' : '停止'}
          </Button>
        </div>
      ) : (
        <div className="preview-media-wait">
          <span>尚未生成</span>
        </div>
      )}
      <p>包含分镜、临时音轨、字幕与逐镜头时长。装配中可随时停止。</p>
    </Surface>

    <ImpactConfirmModal
      confirmLabel="批准第 4 阶段"
      description="分镜版本冻结后，修改需通过局部变更或创建修改版。"
      items={[
        { icon: <LockKeyhole size={16} />, title: '锁定分镜版本', detail: `第 ${workspace.storyboard.version} 版 · ${workspace.shots.length} 个镜头序列将冻结。` },
        { icon: <Film size={16} />, title: '启动正式制作任务', detail: '关键帧、视频、对白、背景音乐、环境音和音效任务将依次入队。' },
        { icon: <Sparkles size={16} />, title: '进入第 5 阶段', detail: '批准后会跳转到任务页，等待正式媒体生成。' },
      ]}
      loading={busy}
      onClose={() => { if (!busy) setApproveOpen(false) }}
      onConfirm={() => void approve()}
      open={approveOpen}
      subtitle="确认节奏样片与镜头序列符合预期后再继续。"
      title="批准第 4 阶段？"
    />

    <Modal
      className="modal--identity-image-viewer modal--storyboard-shot-detail"
      description={previewShot ? `${displayShotTitle(previewShot.title, previewShot.code)} · 分镜详情` : undefined}
      footer={<>
        {canRegenerate && previewShot ? (
          <Button
            disabled={Boolean(regenBusyId)}
            onClick={() => {
              if (previewShot) openRegen(previewShot)
            }}
            variant="secondary"
          >
            <RefreshCw size={15} />重生成此镜
          </Button>
        ) : null}
        <Button onClick={() => { setPreviewShot(null); setPreviewZoom(100) }} variant="secondary">关闭</Button>
      </>}
      onClose={() => { setPreviewShot(null); setPreviewZoom(100) }}
      open={previewShot !== null}
      title={previewShot ? `${previewShot.code} 分镜详情` : '分镜详情'}
    >
      {previewShot ? (
        <div className="storyboard-shot-detail">
          <div className="storyboard-shot-detail__media">
            {previewShot.imageUrl ? (
              <div className="identity-image-viewer">
                <div className="identity-image-viewer__toolbar">
                  <span><Maximize2 size={15} /><strong>{previewZoom === 100 ? '适应画面' : `${previewZoom}%`}</strong></span>
                  <div>
                    <Button aria-label="缩小分镜原图" disabled={previewZoom <= 100} onClick={() => setPreviewZoom((current) => Math.max(100, current - 25))} size="sm" variant="secondary"><ZoomOut size={15} /></Button>
                    <Button disabled={previewZoom === 100} onClick={() => setPreviewZoom(100)} size="sm" variant="secondary">复位</Button>
                    <Button aria-label="放大分镜原图" disabled={previewZoom >= 200} onClick={() => setPreviewZoom((current) => Math.min(200, current + 25))} size="sm" variant="secondary"><ZoomIn size={15} /></Button>
                  </div>
                </div>
                <div className="identity-image-viewer__viewport">
                  <div className="identity-image-viewer__canvas" style={{ height: `${previewZoom}%`, width: `${previewZoom}%` }}>
                    <img alt={`${previewShot.code} 分镜原图`} draggable={false} src={previewShot.imageUrl} />
                  </div>
                </div>
              </div>
            ) : (
              <div className="storyboard-placeholder storyboard-placeholder--failed storyboard-shot-detail__empty">
                <span className="storyboard-placeholder__status">
                  {previewShot.status === 'QUEUED' ? '绘制分镜中' : '暂无画面'}
                </span>
              </div>
            )}
          </div>
          <aside className="storyboard-shot-detail__meta">
            <p className="eyebrow">镜头规格</p>
            <h3>{displayShotTitle(previewShot.title, previewShot.code)}</h3>
            <dl>
              <div><dt>镜头号</dt><dd>{previewShot.code}</dd></div>
              <div><dt>时长</dt><dd>{(previewShot.durationMs / 1000).toFixed(1)} 秒</dd></div>
              <div><dt>景别</dt><dd>{localizeDisplayText(previewShot.shotSize)}</dd></div>
              <div><dt>运镜</dt><dd>{localizeDisplayText(previewShot.cameraMovement)}</dd></div>
              <div><dt>出图方式</dt><dd>{renderModeLabel(previewShot.renderMode)}</dd></div>
              {previewShot.delivery ? (
                <div><dt>交付</dt><dd>{deliveryLabel(previewShot.delivery)}</dd></div>
              ) : null}
              <div><dt>状态</dt><dd><StatusBadge status={previewShot.status} /></dd></div>
            </dl>
            <div>
              <span>画面描述</span>
              <p>{previewShot.description || '—'}</p>
            </div>
            {previewShot.dialogue ? (
              <div>
                <span>台词 / 画外音</span>
                <blockquote>{previewShot.dialogue}</blockquote>
              </div>
            ) : null}
            {previewShot.audioCues.length > 0 ? (
              <div>
                <span>声音线索（不进生图）</span>
                <p>{previewShot.audioCues.join('；')}</p>
              </div>
            ) : null}
            {previewShot.cameraNotes.length > 0 ? (
              <div>
                <span>运镜衔接（不进生图）</span>
                <p>{previewShot.cameraNotes.join('；')}</p>
              </div>
            ) : null}
            {previewShot.timelineNotes.length > 0 ? (
              <div>
                <span>时间轴备注（不进生图）</span>
                <p>{previewShot.timelineNotes.join('；')}</p>
              </div>
            ) : null}
            <div className="storyboard-shot-detail__prompt">
              <span>生图提示词</span>
              {previewShot.renderMode === 'BLACK_FRAME' ? (
                <p>黑场镜头：不调用生图模型，本地合成纯黑静帧。</p>
              ) : previewShot.imagePrompt ? (
                <pre>{previewShot.imagePrompt}</pre>
              ) : (
                <p>暂无记录。重新生成此镜后可查看实际出图提示词。</p>
              )}
            </div>
          </aside>
        </div>
      ) : null}
    </Modal>

    <Modal
      description={regenTarget ? `${displayShotTitle(regenTarget.title, regenTarget.code)} · ${regenTarget.code} · 将保留镜头时长与台词，按新种子与身份参考重绘画面` : undefined}
      footer={<>
        <Button disabled={Boolean(regenBusyId)} onClick={() => { setRegenTarget(null); setRegenNote(''); setRegenError(null) }} variant="secondary">取消</Button>
        <Button disabled={Boolean(regenBusyId)} onClick={() => void confirmRegenerate()}>
          {regenBusyId ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}
          确认重生成
        </Button>
      </>}
      onClose={() => { if (!regenBusyId) { setRegenTarget(null); setRegenNote(''); setRegenError(null) } }}
      open={regenTarget !== null}
      title={regenTarget ? `重生成 ${regenTarget.code}` : '重生成分镜'}
    >
      {regenTarget ? (
        <label className="storyboard-regen-note">
          <span>修改意见（可选）</span>
          <textarea
            maxLength={REGEN_NOTE_MAX}
            onChange={(event) => {
              setRegenNote(event.target.value)
              if (regenError) setRegenError(null)
            }}
            placeholder="例如：必须与锁定女主同一张脸；减少路人；更近景看清表情"
            rows={4}
            value={regenNote}
          />
          <small>{regenNote.trim().length}/{REGEN_NOTE_MAX} · 可留空</small>
          {regenError ? <p className="storyboard-regen-note__error" role="alert">{regenError}</p> : null}
        </label>
      ) : null}
    </Modal>
  </div>
}
