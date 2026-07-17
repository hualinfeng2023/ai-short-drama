import { useCallback, useEffect, useState } from 'react'
import {
  Check,
  Download,
  Film,
  Layers3,
  LoaderCircle,
  Music2,
  PackageCheck,
  RefreshCw,
  ShieldCheck,
  Volume2,
} from 'lucide-react'
import { Link, useParams } from 'react-router'
import {
  approvePreviewTimeline,
  createExportMatrix,
  createExportProfile,
  fetchAudioWorkspace,
  fetchExportProfiles,
  fetchProject,
  fetchProjectExports,
  fetchTimelineWorkspace,
  type AudioWorkspace,
  type ExportProfileRecord,
  type TimelineWorkspace,
} from '../api/client'
import { Button, EmptyState, PageHeader, StatusBadge } from '../components/ui'
import { useProjectReadiness } from '../store/ProjectReadinessContext'
import type { ExportPackage, ProjectRecord } from '../types'
import { localizeDisplayText } from '../utils/localizeDisplayText'

const DISPLAY_LABELS: Record<string, string> = {
  AMBIENCE: '环境音',
  AUDIO_VIDEO_SYNC: '音画同步',
  BGM: '背景音乐',
  BLACK_FRAME: '黑帧检查',
  BOTH: '内嵌字幕与外挂字幕',
  CLEARED: '已确认',
  CONTINUITY: '连续性',
  DIALOGUE: '对白',
  DURATION: '总时长',
  LIP_SYNC: '口型同步',
  LOUDNESS: '响度',
  SFX: '音效',
  SIDECAR: '外挂字幕',
  SUBTITLE: '字幕',
  SUBTITLE_BOUNDS: '字幕边界',
  TEMP_ASSET: '临时资产',
  VIDEO: '视频',
  douyin: '抖音',
  youtube_shorts: 'YouTube Shorts',
  'en-US': '英语（美国）',
  'zh-CN': '简体中文',
}

function displayLabel(value: string): string {
  return DISPLAY_LABELS[value] ?? value.replaceAll('_', ' ')
}

export function ProductionPage() {
  const { projectId } = useParams()
  const { readiness } = useProjectReadiness()
  const [project, setProject] = useState<ProjectRecord | null>(null)
  const [audio, setAudio] = useState<AudioWorkspace | null>(null)
  const [timeline, setTimeline] = useState<TimelineWorkspace | null>(null)
  const [profiles, setProfiles] = useState<ExportProfileRecord[]>([])
  const [exports, setExports] = useState<ExportPackage[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const productionPollingActive = readiness === null
    || (readiness.activeStageKey === 'PRODUCTION' && readiness.summaryStatus === 'IN_PROGRESS')

  const refresh = useCallback(async () => {
    if (!projectId) return
    const [nextProject, nextAudio, nextTimeline, nextProfiles, nextExports] = await Promise.all([
      fetchProject(projectId),
      fetchAudioWorkspace(projectId),
      fetchTimelineWorkspace(projectId),
      fetchExportProfiles(projectId),
      fetchProjectExports(projectId),
    ])
    setProject(nextProject)
    setAudio(nextAudio)
    setTimeline(nextTimeline)
    setProfiles(nextProfiles)
    setExports(nextExports)
  }, [projectId])

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        await refresh()
        if (active) setError(null)
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : '制作工作区读取失败')
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    const interval = productionPollingActive ? window.setInterval(load, 3000) : null
    return () => {
      active = false
      if (interval !== null) window.clearInterval(interval)
    }
  }, [productionPollingActive, refresh])

  async function approveG5() {
    if (!project || !timeline?.timeline) return
    setBusy('g5')
    setError(null)
    try {
      await approvePreviewTimeline(timeline.timeline.id, project.lockVersion)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '第 5 阶段批准失败')
    } finally {
      setBusy(null)
    }
  }

  async function createDefaults() {
    if (!projectId || !project) return
    setBusy('profiles')
    setError(null)
    try {
      let current = project
      if (!profiles.some((item) => item.platform === 'douyin')) {
        await createExportProfile(projectId, current.lockVersion, {
          name: '抖音竖屏',
          platform: 'douyin',
          aspectRatio: '9:16',
          width: 720,
          height: 1280,
          captionMode: 'BOTH',
          languages: ['zh-CN', 'en-US'],
          audioTracks: ['DIALOGUE', 'BGM', 'AMBIENCE', 'SFX'],
        })
        current = await fetchProject(projectId)
      }
      if (!profiles.some((item) => item.platform === 'youtube_shorts')) {
        await createExportProfile(projectId, current.lockVersion, {
          name: 'YouTube Shorts',
          platform: 'youtube_shorts',
          aspectRatio: '9:16',
          width: 1080,
          height: 1920,
          captionMode: 'SIDECAR',
          languages: ['zh-CN', 'en-US'],
          audioTracks: ['DIALOGUE', 'BGM', 'AMBIENCE', 'SFX'],
        })
      }
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '默认导出规格创建失败')
    } finally {
      setBusy(null)
    }
  }

  async function startMatrix() {
    if (!projectId || !project || profiles.length < 2) return
    setBusy('matrix')
    setError(null)
    try {
      await createExportMatrix(
        projectId,
        project.lockVersion,
        profiles.slice(0, 2).map((item) => item.id),
        ['zh-CN', 'en-US'],
      )
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '交付矩阵创建失败')
    } finally {
      setBusy(null)
    }
  }

  if (!loading && (!project || !audio || !timeline || !projectId)) {
    return <div className="page"><EmptyState title="无法打开正式制作" description="音频、时间线或交付数据暂时没有返回，请确认项目服务已连接后重试。" action={<Link className="button button--secondary button--md" to={projectId ? `/tasks?project=${projectId}` : '/projects'}>查看项目任务</Link>} /></div>
  }
  if (loading || !project || !audio || !timeline || !projectId) {
    return <div className="page brief-page-state"><LoaderCircle className="spin" size={22} /><strong>正在读取音频、多轨时间线与交付状态…</strong></div>
  }

  if (readiness?.workflowMode === 'CLASSIC') {
    return <div className="page"><EmptyState title="当前项目使用经典镜头工作流" description="这个项目尚未迁移到五阶段制作流。请继续在分集、场景与镜头工作台中制作；空的新版时间线不会再显示为正在生成。" action={<Link className="button button--primary button--md" to={readiness.nextActionHref}>{readiness.nextActionLabel}</Link>} /></div>
  }

  const matrixExports = exports.filter((item) => item.exportProfileId)
  const allQcPassed = timeline.qualityChecks.length >= 8
    && timeline.qualityChecks.every((item) => item.status !== 'FAILED')
  const productionInProgress = readiness?.activeStageKey === 'PRODUCTION'
    && readiness.summaryStatus === 'IN_PROGRESS'
  return <div className="page page--production">
    <PageHeader eyebrow="第 5 阶段 · 成片与交付" title="正式制作与交付" description="检查正式音频、口型降级、多轨装配、整片质量和导出规格 × 语言交付矩阵。" actions={<Button onClick={() => void refresh()} variant="secondary"><RefreshCw size={16} />刷新</Button>} />
    {error ? <div className="brief-save-message brief-save-message--error" role="alert">{error}</div> : null}
    <section className="story-gate-summary"><div><span>项目阶段</span><StatusBadge status={project.status} /></div><div><span>音频节点</span><strong>{audio.cues.length}</strong></div><div><span>时间线轨道</span><strong>{timeline.tracks.length}</strong></div><div><span>交付项</span><strong>{matrixExports.filter((item) => item.status === 'READY').length} / {matrixExports.length || 4}</strong></div></section>

    <div className="production-grid">
      <section className="story-section"><div className="section-heading"><div><p className="eyebrow">音频流程</p><h2><Music2 size={19} />对白、背景音乐、环境音与音效</h2><p>权利状态：{displayLabel(audio.soundBrief?.rightsStatus ?? '等待声音简报')}</p></div><StatusBadge status={audio.soundBrief?.status ?? 'PRODUCING'} /></div><div className="audio-cue-list">{audio.cues.map((cue) => <div key={cue.id}><span className={`audio-cue-type audio-cue-type--${cue.type.toLowerCase()}`}><Volume2 size={14} />{displayLabel(cue.type)}</span><strong>{(cue.startMs / 1000).toFixed(1)} 秒 → {((cue.startMs + cue.durationMs) / 1000).toFixed(1)} 秒</strong><small>{String(cue.payload.text ?? '')}</small><StatusBadge status={cue.take?.qualityStatus ?? cue.status} /></div>)}</div>{audio.lipSync.length ? <p className="production-note">口型同步：{audio.lipSync.length} 个镜头；{audio.lipSync.filter((item) => item.fallbackStrategy).length} 个显式降级，源视频版本全部保留。</p> : null}</section>

      <section className="story-section"><div className="section-heading"><div><p className="eyebrow">多轨时间线</p><h2><Layers3 size={19} />六类轨道</h2></div>{timeline.timeline ? <StatusBadge status={timeline.timeline.status} /> : null}</div>{timeline.timeline ? <><video controls preload="metadata" src={`/api/v1/assets/${timeline.timeline.assets.mp4}/content`} /><div className="timeline-track-list">{timeline.tracks.map((track) => <div key={track.id}><strong>{displayLabel(track.type)}</strong><span>{track.clips.length} 个片段</span><small>{track.gainDb} 分贝</small><div>{track.clips.map((clip) => <i className={clip.degraded ? 'degraded' : ''} key={clip.id} style={{ flex: Math.max(1, clip.endMs - clip.startMs) }} title={`${(clip.startMs / 1000).toFixed(1)}–${(clip.endMs / 1000).toFixed(1)} 秒`} />)}</div></div>)}</div></> : productionInProgress ? <div className="preview-media-wait"><LoaderCircle className="spin" size={20} />正式媒体正在生成</div> : <EmptyState title="尚未创建正式时间线" description="当前没有时间线装配任务。请先完成并批准动态分镜，再启动正式制作。" action={<Link className="button button--secondary button--md" to={readiness?.nextActionHref ?? `/projects/${projectId}/storyboard`}>{readiness?.nextActionLabel ?? '检查前置阶段'}</Link>} />}</section>
    </div>

    <section className="story-section"><div className="section-heading"><div><p className="eyebrow">整片质量检查</p><h2>第 5 阶段 · 画面锁定</h2><p>检查黑帧、空片段、音画同步、字幕边界、总时长、响度、连续性、临时资产和权利状态。</p></div><Button disabled={!timeline.timeline || !allQcPassed || timeline.timeline.status === 'APPROVED' || busy !== null} onClick={() => void approveG5()}>{busy === 'g5' ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}{timeline.timeline?.status === 'APPROVED' ? '第 5 阶段已批准' : '批准第 5 阶段'}</Button></div><div className="qc-grid">{timeline.qualityChecks.map((check) => <article key={check.type}><Check size={15} /><strong>{displayLabel(check.type)}</strong><span>{check.score?.toFixed(2) ?? '—'}</span><StatusBadge status={check.status} /></article>)}</div></section>

    <section className="story-section"><div className="section-heading"><div><p className="eyebrow">交付矩阵</p><h2><PackageCheck size={19} />多平台 × 多语言</h2><p>画面母版在不同语言版本间复用，字幕和来源清单独立登记；默认不自动发布。</p></div><div>{profiles.length < 2 ? <Button disabled={project.status !== 'APPROVED' || busy !== null} onClick={() => void createDefaults()} variant="secondary">{busy === 'profiles' ? <LoaderCircle className="spin" size={16} /> : <PackageCheck size={16} />}创建 2 个默认导出规格</Button> : <Button disabled={project.status !== 'APPROVED' || busy !== null || matrixExports.length > 0} onClick={() => void startMatrix()}>{busy === 'matrix' ? <LoaderCircle className="spin" size={16} /> : <PackageCheck size={16} />}生成 2 × 2 交付矩阵</Button>}</div></div><div className="delivery-profile-grid">{profiles.map((profile) => <article key={profile.id}><span>{displayLabel(profile.platform)}</span><h3>{localizeDisplayText(profile.name)}</h3><p>{profile.width} × {profile.height} · {profile.aspectRatio}</p><small>{profile.languages.map(displayLabel).join(' / ')} · {displayLabel(profile.captionMode)}</small></article>)}</div>{matrixExports.length ? <div className="delivery-list">{matrixExports.map((item) => <article key={item.id}><div><Film size={17} /><span><strong>{localizeDisplayText(item.profile)} · {displayLabel(item.language)}</strong><small>权利状态：{displayLabel(item.rightsStatus)}</small></span></div><StatusBadge status={item.status} />{item.status === 'READY' && item.assets.manifest ? <a href={item.assets.manifest}><Download size={15} />下载清单</a> : <LoaderCircle className="spin" size={15} />}</article>)}</div> : null}</section>

    {timeline.timeline?.status === 'APPROVED' ? <Link className="button button--secondary button--md" to={`/projects/${projectId}/episodes/current/preview`}><Film size={16} />打开修改与小样工作区</Link> : null}
  </div>
}
