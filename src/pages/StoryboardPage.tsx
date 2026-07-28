import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ArrowLeft, Ban, Check, Eye, Film, GitBranch, LoaderCircle, LockKeyhole, Maximize2, RefreshCw, Save, Sparkles, UnlockKeyhole, ZoomIn, ZoomOut } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  approveStoryboardVersion,
  cancelPersistedJob,
  fetchProject,
  fetchStoryboardWorkspace,
  regenerateStoryboardShot,
  updateShotLock,
  updateStructuredShotSpec,
  type ShotLockScope,
  type ShotPromptAdapter,
  type StructuredShotSpec,
  type StoryboardWorkspace,
} from '../api/client'
import { Button, EmptyState, Modal, PageHeader, StatusBadge, Surface } from '../components/ui'
import { ImpactConfirmModal } from '../components/ConfirmModal'
import { PageLoadingSkeleton } from '../components/PageLoadingSkeleton'
import { ServiceRequiredState } from '../components/ServiceRequiredState'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'
import type { JobStatus, ProjectRecord } from '../types'
import { localizeDisplayText } from '../utils/localizeDisplayText'

const ACTIVE_JOB_STATUSES = new Set<JobStatus>([
  'PENDING',
  'RETRY_WAIT',
  'RUNNING',
  'CANCEL_REQUESTED',
])

/** 与后端 StoryboardShotRegenerateRequest.note max_length 对齐 */
const REGEN_NOTE_MAX = 500

function workflowNodeLabel(value: string): string {
  if (value === 'storyboard.plan') return '分镜规划'
  if (value === 'animatic.render' || value.startsWith('animatic.render.')) return '节奏样片渲染'
  const take = value.match(/^storyboard\.take\.(\d+)(?:\.regen\..+)?$/)
  if (take) return `分镜版本 ${take[1]}`
  const keyframe = value.match(/^keyframe\.(\d+)\.(\d+)$/)
  if (keyframe) return `镜头 ${keyframe[1]} · 关键帧候选 ${keyframe[2]}`
  const video = value.match(/^video\.(\d+)$/)
  if (video) return `镜头 ${video[1]} · 视频`
  if (value === 'audio.pipeline') return '音频流程'
  if (value === 'timeline.multitrack') return '多轨时间线'
  return localizeDisplayText(value)
}

/** 卡片已单独展示镜头号，去掉标题里重复的 code 前缀/后缀。 */
function displayShotTitle(title: string, code: string): string {
  let next = title.trim()
  const suffix = ` · ${code}`
  if (next.endsWith(suffix)) next = next.slice(0, -suffix.length).trim()
  const prefix = `${code} `
  if (next.startsWith(prefix)) next = next.slice(prefix.length).trim()
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
  const regenOpenRef = useRef(false)
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
  const hasNeedsReview = workspace.shots.some(
    (shot) => shot.reviewStatus === 'NEEDS_REVIEW',
  )

  return <div className="page page--storyboard" data-aspect={project.aspectRatio}>
    <PageHeader
      title="动态分镜审核"
      description="镜头数由批准后的剧本动态决定；不满意的镜头可单独重生成，再批准进入正式制作。"
      actions={
        <>
          <Link className="button button--secondary button--md" to={`/projects/${projectId}/preproduction`}>
            <ArrowLeft size={16} />返回第 3 阶段
          </Link>
          <Button onClick={() => void refresh()} variant="secondary">
            <RefreshCw size={16} />刷新
          </Button>
          <Button disabled={busy || hasNeedsReview || project.status !== 'STORYBOARD_READY'} onClick={() => setApproveOpen(true)}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}
            批准第 4 阶段
          </Button>
        </>
      }
    />
    {error ? <div className="brief-save-message brief-save-message--error" role="alert">{error}</div> : null}
    {hasNeedsReview ? (
      <div className="brief-save-message brief-save-message--error" role="alert">
        有镜头未通过 ShotValidator。修正阻断项并保存后，才能批准第 4 阶段。
      </div>
    ) : null}
    <section className="story-gate-summary"><div><span>分镜</span><strong>第 {workspace.storyboard.version} 版</strong></div><div><span>镜头</span><strong>{workspace.shots.length}</strong></div><div><span>总时长</span><strong>{Math.round(workspace.shots.reduce((sum, shot) => sum + shot.durationMs, 0) / 1000)} 秒</strong></div><div><span>审批阶段</span><StatusBadge status={workspace.gate?.status ?? workspace.storyboard.status} /></div></section>
    <div className="storyboard-layout">
      <Surface className="story-section storyboard-board">
        <div className="section-heading">
          <div>
            <p className="eyebrow">导演级分镜表</p>
            <h2>ShotSpec 镜头序列</h2>
            <p>默认查看导演决策；展开任一镜头可编辑全部结构化字段、管理三层锁并重新编译 Prompt。</p>
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
      </Surface>
      <aside>
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
        <Surface className="approval-card"><p className="eyebrow">生成进度</p><h2><GitBranch size={18} />任务顺序</h2><div className="workflow-node-list">{workspace.workflow?.nodes.map((node) => <div key={node.id}><span>{workflowNodeLabel(node.nodeKey)}</span><StatusBadge status={node.status} /><small>{node.dependencies.map(workflowNodeLabel).join(' → ') || '起始步骤'}</small></div>)}</div></Surface>
      </aside>
    </div>

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
