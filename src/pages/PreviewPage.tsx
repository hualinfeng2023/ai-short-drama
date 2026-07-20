import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeftRight,
  Check,
  CheckCircle2,
  Clock3,
  Download,
  FileJson,
  Film,
  LoaderCircle,
  Lock,
  RotateCcw,
  ShieldCheck,
  Subtitles,
} from 'lucide-react'
import { Link, useParams } from 'react-router'
import {
  analyzeRevision,
  approvePreviewTimeline,
  comparePreviewTimelines,
  createProjectExport,
  createRevision,
  estimateProjectExport,
  fetchPreviews,
  fetchProjectExports,
  rollbackPreviewTimeline,
} from '../api/client'
import { Button, EmptyState, Modal, PageHeader, ProgressBar, StatusBadge, getStatusLabel } from '../components/ui'
import { ImpactConfirmModal } from '../components/ConfirmModal'
import { useStudio } from '../store/StudioContext'
import { useProjectReadiness } from '../store/ProjectReadinessContext'
import { useToast } from '../store/ToastContext'
import { localizeDisplayText } from '../utils/localizeDisplayText'
import type {
  ExportEstimate,
  ExportPackage,
  PreviewComparison,
  RevisionImpact,
  TimelineRecord,
} from '../types'

export function PreviewPage() {
  const { projectId: routeProjectId } = useParams()
  const { project, jobs, activateProject } = useStudio()
  const { readiness } = useProjectReadiness()
  const { notify } = useToast()
  const projectId = routeProjectId ?? project.id
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [timelines, setTimelines] = useState<TimelineRecord[]>([])
  const [exports, setExports] = useState<ExportPackage[]>([])
  const [selectedShotId, setSelectedShotId] = useState<string | null>(null)
  const [instruction, setInstruction] = useState('妹妹只说半句，把威胁放在动作里')
  const [impact, setImpact] = useState<RevisionImpact | null>(null)
  const [comparison, setComparison] = useState<PreviewComparison | null>(null)
  const [exportEstimate, setExportEstimate] = useState<ExportEstimate | null>(null)
  const [rightsConfirmed, setRightsConfirmed] = useState(false)
  const [approveOpen, setApproveOpen] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const previewPollingActive = jobs.some((job) =>
    (job.jobType.includes('PREVIEW')
      || job.jobType.includes('TIMELINE')
      || job.jobType === 'APPLY_REVISION'
      || job.jobType === 'EXPORT_PACKAGE')
    && ['PENDING', 'RETRY_WAIT', 'RUNNING', 'CANCEL_REQUESTED'].includes(job.status),
  )

  const refresh = useCallback(async () => {
    try {
      const [nextTimelines, nextExports] = await Promise.all([
        fetchPreviews(projectId),
        fetchProjectExports(projectId),
      ])
      setTimelines(nextTimelines)
      setExports(nextExports)
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '小样数据读取失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    if (project.id !== projectId) {
      void activateProject(projectId).catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : '项目工作台读取失败')
      })
    }
    void refresh()
    const interval = previewPollingActive ? window.setInterval(refresh, 3000) : null
    return () => {
      if (interval !== null) window.clearInterval(interval)
    }
  }, [activateProject, previewPollingActive, project.id, projectId, refresh])

  useEffect(() => {
    if (!selectedShotId && project.id === projectId && project.shots[0]) {
      setSelectedShotId(project.shots[0].id)
    }
  }, [project.id, project.shots, projectId, selectedShotId])

  const currentTimeline = timelines.find((item) => item.version === project.timelineVersion)
    ?? timelines[0]
  const previousTimeline = currentTimeline
    ? timelines.find((item) => item.version < currentTimeline.version)
    : undefined
  const latestExport = exports[0]
  const selectedShot = project.shots.find((shot) => shot.id === selectedShotId)
    ?? project.shots[0]
  const totalDuration = project.shots.reduce((sum, shot) => sum + shot.durationSec, 0)
  const activeRevision = jobs.find(
    (job) => job.jobType === 'APPLY_REVISION' && ['PENDING', 'RETRY_WAIT', 'RUNNING'].includes(job.status),
  )
  const activeExport = jobs.find(
    (job) => job.jobType === 'EXPORT_PACKAGE' && ['PENDING', 'RETRY_WAIT', 'RUNNING'].includes(job.status),
  )
  const activePreview = jobs.find((job) =>
    (job.jobType.includes('PREVIEW') || job.jobType.includes('TIMELINE'))
    && ['PENDING', 'RETRY_WAIT', 'RUNNING', 'CANCEL_REQUESTED'].includes(job.status),
  )
  const starts = useMemo(() => {
    let cursor = 0
    return Object.fromEntries(project.shots.map((shot) => {
      const start = cursor
      cursor += shot.durationSec
      return [shot.id, start]
    }))
  }, [project.shots])

  const rulerMarks = useMemo(() => {
    const total = Math.max(totalDuration, 15)
    const step = total <= 30 ? 5 : total <= 60 ? 15 : 30
    const marks = [0]
    for (let sec = step; sec < total; sec += step) marks.push(sec)
    if (marks[marks.length - 1] !== total) marks.push(total)
    return marks
  }, [totalDuration])

  function formatTimelineTime(sec: number) {
    const minutes = Math.floor(sec / 60)
    const seconds = Math.floor(sec % 60)
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  }

  useEffect(() => {
    if (currentTimeline?.status !== 'APPROVED') {
      setExportEstimate(null)
      return
    }
    void estimateProjectExport(projectId)
      .then(setExportEstimate)
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : '导出估算失败'))
  }, [currentTimeline?.id, currentTimeline?.status, projectId])

  function selectShot(shotId: string) {
    setSelectedShotId(shotId)
    if (videoRef.current) videoRef.current.currentTime = starts[shotId] ?? 0
  }

  async function inspectImpact() {
    if (!selectedShot || instruction.trim().length < 6) return
    setBusy('impact')
    setError(null)
    try {
      setImpact(await analyzeRevision(projectId, project.lockVersion, selectedShot.id, instruction))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '局部修改影响分析失败')
    } finally {
      setBusy(null)
    }
  }

  async function confirmRevision() {
    if (!selectedShot || !impact) return
    setBusy('revision')
    setError(null)
    try {
      await createRevision(projectId, project.lockVersion, selectedShot.id, instruction)
      setImpact(null)
      await activateProject(projectId)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '局部修改创建失败')
    } finally {
      setBusy(null)
    }
  }

  async function approve() {
    if (!currentTimeline) return
    setBusy('approve')
    setError(null)
    try {
      await approvePreviewTimeline(currentTimeline.id, project.lockVersion)
      setApproveOpen(false)
      notify(`时间线第 ${currentTimeline.version} 版已批准，导出已解锁。`)
      await activateProject(projectId)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '小样批准失败')
    } finally {
      setBusy(null)
    }
  }

  async function compare() {
    if (!currentTimeline || !previousTimeline) return
    setBusy('compare')
    setError(null)
    try {
      setComparison(await comparePreviewTimelines(previousTimeline.id, currentTimeline.id))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '时间线比较失败')
    } finally {
      setBusy(null)
    }
  }

  async function rollback() {
    if (!previousTimeline) return
    setBusy('rollback')
    setError(null)
    try {
      await rollbackPreviewTimeline(previousTimeline.id, project.lockVersion)
      setComparison(null)
      await activateProject(projectId)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '时间线回退失败')
    } finally {
      setBusy(null)
    }
  }

  async function startExport() {
    if (!rightsConfirmed || !exportEstimate || exportEstimate.blocked) return
    setBusy('export')
    setError(null)
    try {
      await createProjectExport(projectId, project.lockVersion)
      await activateProject(projectId)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '导出任务创建失败')
    } finally {
      setBusy(null)
    }
  }

  if (!loading && error && project.id !== projectId) {
    return <div className="page"><EmptyState title="无法打开完整小样" description="项目工作台暂时没有返回，请确认本地服务已连接后重试。" action={<Link className="button button--secondary button--md" to="/projects">返回项目列表</Link>} /></div>
  }
  if (!loading && project.id === projectId && !selectedShot) {
    return <div className="page"><EmptyState title="还没有可预览的镜头" description="完成分镜与制作任务后，完整小样会显示在这里。" action={<Link className="button button--secondary button--md" to={`/tasks?project=${projectId}`}>查看生成任务</Link>} /></div>
  }
  if (loading || project.id !== projectId || !selectedShot) {
    return <div className="page brief-page-state"><LoaderCircle className="spin" size={22} /><strong>正在读取真实小样与时间线…</strong></div>
  }
  if (!currentTimeline && !activePreview) {
    return <div className="page"><EmptyState title="当前没有正在组装的小样" description="镜头仍可继续制作，但后台没有小样或时间线任务。完成镜头审核并明确启动装配后，小样才会出现在这里。" action={<div className="empty-state__actions"><Link className="button button--primary button--md" to={readiness?.nextActionHref ?? `/projects/${projectId}/episodes/${project.episodeId}`}>{readiness?.nextActionLabel ?? '继续镜头制作'}</Link><Link className="button button--secondary button--md" to={`/tasks?project=${projectId}`}>查看项目任务</Link></div>} /></div>
  }

  return <div className="page page--preview">
    <PageHeader
      eyebrow={`${project.name} · 时间线第 ${currentTimeline?.version ?? project.timelineVersion} 版`}
      title="完整小样"
      description="真实 H.264/AAC · 720p/24 帧 · 模拟分镜 + 临时声音与字幕"
      actions={<StatusBadge status={currentTimeline?.status ?? 'PRODUCING'} label={currentTimeline?.status === 'APPROVED' ? '已批准基线' : '待批准'} />}
    />
    {error ? <div className="brief-save-message brief-save-message--error" role="alert">{error}<Button onClick={refresh} size="sm" variant="ghost"><RotateCcw size={14} />重试读取</Button></div> : null}

    <div className="preview-layout">
      <section className="preview-main">
        <div className="preview-player preview-player--media">
          <div className="preview-player__badges"><span>模拟生成 · 临时资产</span><span>H.264 / AAC</span><span>{project.aspectRatio}</span></div>
          {currentTimeline ? <video controls key={currentTimeline.id} preload="metadata" ref={videoRef} src={currentTimeline.assets.mp4}><track default kind="subtitles" label="简体中文" src={currentTimeline.assets.vtt} srcLang="zh-CN" /></video> : <div className="preview-media-wait"><LoaderCircle className="spin" size={24} /><strong>小样正在组装</strong><p>后台任务进程完成 FFmpeg 与 ffprobe 校验后，小样会显示在这里。</p></div>}
        </div>

        <div className="preview-timeline">
          <div className="preview-timeline__ruler">{rulerMarks.map((sec) => <span key={sec}>{formatTimelineTime(sec)}</span>)}</div>
          <div className="preview-timeline__track">{project.shots.map((shot) => <button className={selectedShot.id === shot.id ? 'active' : ''} key={shot.id} onClick={() => selectShot(shot.id)} style={{ flex: shot.durationSec }}><strong>{shot.code}</strong><small>{shot.durationSec} 秒</small></button>)}</div>
          <div className="preview-timeline__legend"><span><i />模拟分镜</span><span><i className="selected" />已选修改范围</span></div>
        </div>

        <section className="revision-composer">
          <div className="section-heading"><div><p className="eyebrow">局部修改</p><h2>局部修改</h2></div><span>已选择 {selectedShot.code} · {selectedShot.durationSec} 秒</span></div>
          <div className="revision-scope"><span><Film size={15} />{selectedShot.code} · {selectedShot.title}</span><small>先分析影响，再执行变更集</small></div>
          <textarea aria-label="修改指令" disabled={Boolean(activeRevision)} onChange={(event) => setInstruction(event.target.value)} value={instruction} />
          <div className="revision-actions"><span><ShieldCheck size={15} />未受影响版本的编号与 SHA-256 哈希保持不变</span><Button disabled={instruction.trim().length < 6 || Boolean(activeRevision) || busy === 'impact'} onClick={inspectImpact}>{busy === 'impact' ? <LoaderCircle className="spin" size={16} /> : <ArrowLeftRight size={16} />}先看影响</Button></div>
          {activeRevision ? <div className="inline-job"><div><strong>{localizeDisplayText(activeRevision.label)}</strong><small>{localizeDisplayText(activeRevision.stage)}</small></div><ProgressBar value={activeRevision.progress} /></div> : null}
        </section>
      </section>

      <aside className="preview-side">
        <section className="approval-card">
          <div className="approval-card__icon"><ShieldCheck size={20} /></div>
          <p className="eyebrow">审批关卡</p>
          <h2>{currentTimeline?.status === 'APPROVED' ? '当前小样已批准' : '确认故事与节奏后再批准'}</h2>
          <p>{currentTimeline?.status === 'APPROVED' ? `时间线第 ${currentTimeline.version} 版与基线哈希已冻结。` : '批准会记录操作者、时间戳与基线哈希；历史版本不会删除。'}</p>
          <ul><li><Check size={14} />{Math.round((currentTimeline?.durationMs ?? 0) / 1000)} 秒时间线无缺口</li><li><Check size={14} />真实 MP4 / SRT / VTT 已登记</li><li><AlertTriangle size={14} />模拟生成与临时资产已明确标识</li></ul>
          <Button disabled={!currentTimeline || currentTimeline.status === 'APPROVED' || Boolean(activeRevision) || busy === 'approve'} onClick={() => setApproveOpen(true)}>{busy === 'approve' ? <LoaderCircle className="spin" size={16} /> : currentTimeline?.status === 'APPROVED' ? <CheckCircle2 size={16} /> : <Lock size={16} />}{currentTimeline?.status === 'APPROVED' ? '已批准当前基线' : `批准时间线第 ${currentTimeline?.version ?? 1} 版`}</Button>
          <Button disabled={!previousTimeline || busy === 'compare'} onClick={compare} variant="ghost"><ArrowLeftRight size={16} />{previousTimeline ? `比较第 ${previousTimeline.version} 版 / 第 ${currentTimeline?.version} 版` : '暂无历史版本'}</Button>
        </section>

        <section className={`export-card ${currentTimeline?.status === 'APPROVED' ? 'export-card--ready' : ''}`}>
          <div className="section-heading"><div><p className="eyebrow">导出</p><h2>导出</h2></div>{currentTimeline?.status === 'APPROVED' ? <StatusBadge status="APPROVED" label="可进入预检" /> : <Lock size={17} />}</div>
          <div className="export-spec"><span><small>画幅</small><strong>{project.aspectRatio}</strong></span><span><small>清晰度</small><strong>720p</strong></span><span><small>时长</small><strong>{totalDuration} 秒</strong></span></div>
          {currentTimeline?.status !== 'APPROVED' ? <p className="export-blocked"><Lock size={15} />先批准当前小样，才能导出。</p> : null}
          {exportEstimate && currentTimeline?.status === 'APPROVED' && latestExport?.status !== 'READY' ? <label className="rights-confirm"><input checked={rightsConfirmed} onChange={(event) => setRightsConfirmed(event.target.checked)} type="checkbox" /><span><Check size={12} /></span><small>我确认模拟生成或临时素材仅用于演示验证；风险预检不等于法律意见或平台审核保证。</small></label> : null}
          {exportEstimate && latestExport?.status !== 'READY' ? <Button disabled={!rightsConfirmed || exportEstimate.blocked || Boolean(activeExport) || busy === 'export'} onClick={startExport}>{activeExport ? <Clock3 size={16} /> : <ShieldCheck size={16} />}{activeExport ? '正在打包真实文件' : `导出四件套 · ${exportEstimate.estimatedPoints} 积分`}</Button> : null}
          {activeExport ? <ProgressBar label={localizeDisplayText(activeExport.stage)} value={activeExport.progress} /> : null}
          {latestExport?.status === 'READY' ? <div className="download-list">{latestExport.assets.mp4 ? <a href={latestExport.assets.mp4}><Film size={17} /><span><strong>Preview.mp4</strong><small>H.264/AAC · 支持分段下载</small></span><Download size={16} /></a> : null}{latestExport.assets.srt ? <a href={latestExport.assets.srt}><Subtitles size={17} /><span><strong>Subtitles.srt</strong><small>UTF-8 字幕</small></span><Download size={16} /></a> : null}{latestExport.assets.vtt ? <a href={latestExport.assets.vtt}><Subtitles size={17} /><span><strong>Subtitles.vtt</strong><small>WebVTT 字幕</small></span><Download size={16} /></a> : null}{latestExport.assets.manifest ? <a href={latestExport.assets.manifest}><FileJson size={17} /><span><strong>Manifest.json</strong><small>批准基线与 SHA-256 哈希回链</small></span><Download size={16} /></a> : null}</div> : null}
          <small className="export-disclaimer">权利状态：{localizeDisplayText(latestExport?.rightsStatus ?? exportEstimate?.rightsStatus ?? '等待批准小样')}。</small>
        </section>
      </aside>
    </div>

    <Modal open={impact !== null} onClose={() => setImpact(null)} title="局部修改影响范围" description="只有确认后才会创建持久化变更集。" footer={<><Button onClick={() => setImpact(null)} variant="secondary">返回修改</Button><Button disabled={busy === 'revision'} onClick={confirmRevision}>{busy === 'revision' ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}确认并执行</Button></>}><div className="impact-list"><div><span>解析意图</span><strong>{localizeDisplayText(impact?.intent.type ?? '')}</strong><small>{impact?.intent.instruction}</small></div><div><span>影响范围</span><strong>{impact?.affected.shots.length} 个镜头</strong><small>{impact?.affected.assetTypes.map(localizeDisplayText).join(' · ')}</small></div><div><span>预计消耗</span><strong>{impact?.estimatedPoints} 积分 · {impact?.estimatedSeconds} 秒</strong><small>保留 {impact?.affected.preservedHashes.length} 个未影响资产哈希</small></div></div></Modal>

    <Modal open={comparison !== null} onClose={() => setComparison(null)} title="时间线版本比较" description={localizeDisplayText(comparison?.summary ?? '')} footer={<><Button onClick={() => setComparison(null)} variant="secondary">保留当前版本</Button><Button disabled={busy === 'rollback'} onClick={rollback}>{busy === 'rollback' ? <LoaderCircle className="spin" size={16} /> : <RotateCcw size={16} />}回退到第 {previousTimeline?.version} 版</Button></>}><div className="timeline-compare"><div><span>第 {comparison?.left.version} 版</span><strong>历史基线</strong><p>状态：{getStatusLabel(comparison?.left.status ?? '')} · 基线 {comparison?.left.baselineHash.slice(0, 12)}</p></div><ArrowLeftRight size={20} /><div><span>第 {comparison?.right.version} 版</span><strong>当前小样</strong><p>变化媒体：{comparison?.changedAssets.map(localizeDisplayText).join('、') || '无'}；不变：{comparison?.unchangedAssets.map(localizeDisplayText).join('、') || '无'}</p></div></div></Modal>

    <ImpactConfirmModal
      confirmLabel={`批准时间线第 ${currentTimeline?.version ?? 1} 版`}
      description="批准会记录操作者、时间戳与基线哈希；历史版本不会删除。"
      items={[
        { icon: <Lock size={16} />, title: '冻结时间线基线', detail: `${Math.round((currentTimeline?.durationMs ?? 0) / 1000)} 秒 · 基线哈希将写入审计记录。` },
        { icon: <ShieldCheck size={16} />, title: '解锁导出四件套', detail: '批准后可导出 MP4、SRT、VTT 与 Manifest。' },
        { icon: <AlertTriangle size={16} />, title: '模拟资产已标识', detail: '临时分镜与模拟声音仍保留标识，不影响基线追溯。' },
      ]}
      loading={busy === 'approve'}
      onClose={() => { if (busy !== 'approve') setApproveOpen(false) }}
      onConfirm={() => void approve()}
      open={approveOpen}
      subtitle="确认故事节奏与镜头推进符合预期后再批准。"
      title="批准当前小样基线？"
    />
  </div>
}
