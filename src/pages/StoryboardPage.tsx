import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Check, GitBranch, LoaderCircle, RefreshCw } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  approveStoryboardVersion,
  fetchProject,
  fetchStoryboardWorkspace,
  type StoryboardWorkspace,
} from '../api/client'
import { Button, EmptyState, PageHeader, StatusBadge } from '../components/ui'
import { ServiceRequiredState } from '../components/ServiceRequiredState'
import type { ProjectRecord } from '../types'
import { localizeDisplayText } from '../utils/localizeDisplayText'

function workflowNodeLabel(value: string): string {
  if (value === 'storyboard.plan') return '分镜规划'
  if (value === 'animatic.render') return '节奏样片渲染'
  const take = value.match(/^storyboard\.take\.(\d+)$/)
  if (take) return `分镜版本 ${take[1]}`
  const keyframe = value.match(/^keyframe\.(\d+)\.(\d+)$/)
  if (keyframe) return `镜头 ${keyframe[1]} · 关键帧候选 ${keyframe[2]}`
  const video = value.match(/^video\.(\d+)$/)
  if (video) return `镜头 ${video[1]} · 视频`
  if (value === 'audio.pipeline') return '音频流程'
  if (value === 'timeline.multitrack') return '多轨时间线'
  return localizeDisplayText(value)
}

export function StoryboardPage() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState<ProjectRecord | null>(null)
  const [workspace, setWorkspace] = useState<StoryboardWorkspace | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
        if (active) setError(null)
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
      navigate(`/tasks?project=${project.id}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '第 4 阶段批准失败')
    } finally {
      setBusy(false)
    }
  }

  if (!loading && (!project || !workspace || !projectId)) {
    return <ServiceRequiredState feature="动态分镜" projectId={projectId} />
  }
  if (loading || !project || !workspace || !projectId) {
    return <div className="page brief-page-state"><LoaderCircle className="spin" size={22} /><strong>正在读取动态分镜与节奏样片…</strong></div>
  }
  if (!workspace.storyboard) {
    return <div className="page page--storyboard"><PageHeader eyebrow="第 4 阶段 · 分镜与节奏样片" title="动态分镜审核" description="批准前期资产后，系统会在这里装配镜头序列、节奏样片与任务依赖。" actions={<Link className="button button--secondary button--md" to={`/projects/${projectId}/preproduction`}><ArrowLeft size={16} />返回第 3 阶段</Link>} /><EmptyState title="分镜尚未生成" description="先完成第 3 阶段的前期资产锁定；任务启动后，本页会自动显示动态拆镜进度。" action={<div className="empty-state__actions"><Link className="button button--primary button--md" to={`/projects/${projectId}/preproduction`}>检查前期资产</Link><Link className="button button--secondary button--md" to={`/tasks?project=${projectId}`}>查看生成任务</Link></div>} /></div>
  }

  return <div className="page page--storyboard">
    <PageHeader eyebrow="第 4 阶段 · 分镜与节奏样片" title="动态分镜审核" description="镜头数由批准后的剧本动态决定；分镜版本、节奏样片与任务依赖均可追溯。" actions={<><Link className="button button--secondary button--md" to={`/projects/${projectId}/preproduction`}><ArrowLeft size={16} />返回第 3 阶段</Link><Button onClick={() => void refresh()} variant="secondary"><RefreshCw size={16} />刷新</Button></>} />
    {error ? <div className="brief-save-message brief-save-message--error" role="alert">{error}</div> : null}
    <section className="story-gate-summary"><div><span>分镜</span><strong>第 {workspace.storyboard.version} 版</strong></div><div><span>镜头</span><strong>{workspace.shots.length}</strong></div><div><span>总时长</span><strong>{Math.round(workspace.shots.reduce((sum, shot) => sum + shot.durationMs, 0) / 1000)} 秒</strong></div><div><span>审批阶段</span><StatusBadge status={workspace.gate?.status ?? workspace.storyboard.status} /></div></section>
    <div className="storyboard-layout">
      <section className="story-section storyboard-board"><div className="section-heading"><div><p className="eyebrow">镜头规格</p><h2>剧本驱动的镜头序列</h2></div></div><div className="storyboard-shot-grid">{workspace.shots.map((shot) => <article key={shot.shotSpecId}>{shot.imageUrl ? <img alt={`${shot.code} 分镜`} src={shot.imageUrl} /> : <div className="storyboard-placeholder"><LoaderCircle className="spin" size={18} /></div>}<header><strong>{shot.code} · {shot.title}</strong><small>{(shot.durationMs / 1000).toFixed(1)} 秒</small></header><p>{shot.description}</p>{shot.dialogue ? <blockquote>{shot.dialogue}</blockquote> : null}<footer><span>{localizeDisplayText(shot.shotSize)}</span><span>{localizeDisplayText(shot.cameraMovement)}</span><small>{shot.contentHash.slice(0, 10)}</small></footer></article>)}</div></section>
      <aside>
        <section className="approval-card"><p className="eyebrow">节奏样片</p><h2>低成本节奏样片</h2>{workspace.storyboard.animaticUrl ? <video controls preload="metadata" src={workspace.storyboard.animaticUrl} /> : <div className="preview-media-wait"><LoaderCircle className="spin" size={20} />正在装配</div>}<p>包含分镜、临时音轨、字幕与逐镜头时长。</p></section>
        <section className="approval-card"><p className="eyebrow">任务依赖图</p><h2><GitBranch size={18} />持久化依赖</h2><div className="workflow-node-list">{workspace.workflow?.nodes.map((node) => <div key={node.id}><span>{workflowNodeLabel(node.nodeKey)}</span><StatusBadge status={node.status} /><small>{node.dependencies.map(workflowNodeLabel).join(' → ') || '根节点'}</small></div>)}</div></section>
        <section className="character-lock-bar"><div><Check size={18} /><span><strong>第 4 阶段 · 分镜锁定</strong><small>批准后启动关键帧、视频、对白、背景音乐、环境音和音效的正式任务。</small></span></div><Button disabled={busy || project.status !== 'STORYBOARD_READY'} onClick={() => void approve()}>{busy ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}批准第 4 阶段</Button></section>
      </aside>
    </div>
  </div>
}
