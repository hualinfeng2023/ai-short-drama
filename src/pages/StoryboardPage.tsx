import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, Ban, Check, Film, GitBranch, LoaderCircle, LockKeyhole, Maximize2, RefreshCw, Sparkles, ZoomIn, ZoomOut } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  approveStoryboardVersion,
  cancelPersistedJob,
  fetchProject,
  fetchStoryboardWorkspace,
  regenerateStoryboardShot,
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
          <Button disabled={busy || project.status !== 'STORYBOARD_READY'} onClick={() => setApproveOpen(true)}>
            {busy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}
            批准第 4 阶段
          </Button>
        </>
      }
    />
    {error ? <div className="brief-save-message brief-save-message--error" role="alert">{error}</div> : null}
    <section className="story-gate-summary"><div><span>分镜</span><strong>第 {workspace.storyboard.version} 版</strong></div><div><span>镜头</span><strong>{workspace.shots.length}</strong></div><div><span>总时长</span><strong>{Math.round(workspace.shots.reduce((sum, shot) => sum + shot.durationMs, 0) / 1000)} 秒</strong></div><div><span>审批阶段</span><StatusBadge status={workspace.gate?.status ?? workspace.storyboard.status} /></div></section>
    <div className="storyboard-layout">
      <Surface className="story-section storyboard-board"><div className="section-heading"><div><p className="eyebrow">镜头规格</p><h2>剧本驱动的镜头序列</h2></div></div><div className="storyboard-shot-grid">{workspace.shots.map((shot) => {
            const regenerating = shot.status === 'QUEUED' || regenBusyId === shot.shotSpecId
            const failed = shot.status === 'FAILED'
            const openDetail = () => {
              setPreviewShot(openShotDetail(shot))
              setPreviewZoom(100)
            }
            return (
            <article
              aria-label={`查看 ${shot.code} 分镜详情`}
              className={[
                'storyboard-shot-card',
                regenerating ? 'is-generating' : '',
                failed ? 'is-failed' : '',
              ].filter(Boolean).join(' ')}
              key={shot.shotSpecId}
              onClick={openDetail}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  openDetail()
                }
              }}
              role="button"
              tabIndex={0}
              title={shot.contentHash}
            >
              {shot.imageUrl ? (
                <div className="storyboard-shot-card__media-wrap">
                  <div aria-hidden className="storyboard-shot-card__media">
                    <img alt="" src={shot.imageUrl} />
                  </div>
                  {regenerating ? (
                    <div className="storyboard-shot-card__generating-overlay" aria-busy="true">
                      <span className="storyboard-placeholder__aurora" aria-hidden />
                      <span className="storyboard-placeholder__sheen" aria-hidden />
                      <span className="storyboard-placeholder__status">
                        <LoaderCircle className="spin" size={14} strokeWidth={1.5} />
                        绘制分镜
                      </span>
                    </div>
                  ) : null}
                </div>
              ) : regenerating ? (
                <div className="storyboard-placeholder storyboard-placeholder--generating" aria-busy="true" aria-label={`${shot.code} 正在生成分镜`}>
                  <span className="storyboard-placeholder__aurora" aria-hidden />
                  <span className="storyboard-placeholder__sheen" aria-hidden />
                  <span className="storyboard-placeholder__frame" aria-hidden />
                  <span className="storyboard-placeholder__status">
                    <LoaderCircle className="spin" size={14} strokeWidth={1.5} />
                    绘制分镜
                  </span>
                </div>
              ) : (
                <div className="storyboard-placeholder storyboard-placeholder--failed">
                  <span className="storyboard-placeholder__status">生成失败</span>
                </div>
              )}
              <div className="storyboard-shot-card__body">
                <header>
                  <div className="storyboard-shot-card__heading">
                    <strong>{shot.code}</strong>
                    <span>{displayShotTitle(shot.title, shot.code)}</span>
                  </div>
                  <small>{(shot.durationMs / 1000).toFixed(1)} 秒</small>
                </header>
                <p>{shot.description}</p>
                {shot.dialogue ? <blockquote>{shot.dialogue}</blockquote> : null}
                <footer>
                  <span>{localizeDisplayText(shot.shotSize)}</span>
                  <span>{localizeDisplayText(shot.cameraMovement)}</span>
                  {canRegenerate ? (
                    <Button
                      disabled={Boolean(regenBusyId) || regenerating}
                      onClick={(event) => {
                        event.stopPropagation()
                        openRegen(openShotDetail(shot))
                      }}
                      size="sm"
                      variant="secondary"
                    >
                      {regenerating ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}
                      {regenerating ? '生成中' : failed ? '重试' : '重生成'}
                    </Button>
                  ) : null}
                </footer>
              </div>
            </article>
            )
          })}</div></Surface>
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
