import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Check, LoaderCircle, LockKeyhole, Mic2, RefreshCw, Sparkles, UsersRound } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  approvePreproduction,
  fetchPreproduction,
  fetchProject,
  lockCharacterCandidate,
  type PreproductionWorkspace,
} from '../api/client'
import { Button, PageHeader, StatusBadge, Surface, getStatusLabel } from '../components/ui'
import { ImpactConfirmModal } from '../components/ConfirmModal'
import { PageLoadingSkeleton } from '../components/PageLoadingSkeleton'
import { ServiceRequiredState } from '../components/ServiceRequiredState'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'
import { localizeCharacterRole, localizeDisplayText } from '../utils/localizeDisplayText'
import type { ProjectRecord } from '../types'

export function PreproductionPage() {
  const { projectId } = useParams()
  const { project: activeProject } = useStudio()
  const navigate = useNavigate()
  const { notify } = useToast()
  const [project, setProject] = useState<ProjectRecord | null>(null)
  const [workspace, setWorkspace] = useState<PreproductionWorkspace | null>(null)
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [approveOpen, setApproveOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!projectId) return
    const [nextProject, nextWorkspace] = await Promise.all([
      fetchProject(projectId),
      fetchPreproduction(projectId),
    ])
    setProject(nextProject)
    setWorkspace(nextWorkspace)
    setSelected((current) => Object.fromEntries(nextWorkspace.characters.map((character) => [
      character.id,
      character.lockedCandidateId
        ?? current[character.id]
        ?? character.candidates[0]?.id
        ?? '',
    ])))
  }, [projectId])

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        await refresh()
        if (active) setError(null)
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : '前期制作数据读取失败')
      } finally {
        if (active) setLoading(false)
      }
    }
    void load()
    const interval = window.setInterval(load, 3000)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [refresh])

  async function lockCharacter(characterId: string) {
    if (!projectId || !project || !selected[characterId]) return
    setBusy(characterId)
    setError(null)
    try {
      await lockCharacterCandidate(
        projectId,
        characterId,
        selected[characterId],
        project.lockVersion,
      )
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '角色锁定失败')
    } finally {
      setBusy(null)
    }
  }

  async function approve() {
    if (!projectId || !project) return
    setBusy('approve')
    setError(null)
    try {
      await approvePreproduction(projectId, project.lockVersion)
      setApproveOpen(false)
      notify('第 3 阶段已批准，分镜生成任务已入队。')
      navigate(`/tasks?project=${projectId}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '第 3 阶段批准失败')
    } finally {
      setBusy(null)
    }
  }

  if (!loading && (!project || !workspace || !projectId)) {
    return <ServiceRequiredState feature="前期资产" projectId={projectId} />
  }
  if (loading || !project || !workspace || !projectId) {
    return <PageLoadingSkeleton label="正在读取前期资产" stage="角色、造型、场景与声音" />
  }

  const allLocked = workspace.characters.length > 0
    && workspace.characters.every((character) => Boolean(character.lockedCandidateId))
  const looksReady = workspace.characters.every(
    (character) => workspace.looks.filter((look) => look.characterId === character.id).length >= 1,
  )
  const canApprove = project.status === 'PREPRODUCTION_READY' && allLocked && looksReady

  return <div className="page page--preproduction">
    <PageHeader
      title="前期资产锁定"
      description="为全部角色选择形象候选，并确认造型、声音、场景与道具的稳定版本引用。"
      actions={<><Link className="button button--secondary button--md" to={`/projects/${projectId}/story`}><ArrowLeft size={16} />返回剧本</Link><Button onClick={() => void refresh()} variant="secondary"><RefreshCw size={16} />刷新</Button></>}
    />
    {error ? <div className="brief-save-message brief-save-message--error" role="alert">{error}</div> : null}
    <section className="story-gate-summary">
      <div><span>项目阶段</span><StatusBadge status={project.status} /></div>
      <div><span>角色</span><strong>{workspace.characters.length}</strong></div>
      <div><span>造型</span><strong>{workspace.looks.length}</strong></div>
      <div><span>声音 / 场景 / 道具</span><strong>{workspace.voices.length} / {workspace.locations.length} / {workspace.props.length}</strong></div>
    </section>

    <Surface className="story-section">
      <div className="section-heading"><div><h2>已锁定身份引用</h2><p>角色身份已在剧本生成前人工锁定；本阶段只读取身份与基础 Look Version，不会替换候选或覆盖既有镜头。</p></div></div>
      <div className="preproduction-character-list">{workspace.characters.map((character) => {
        const lockedReferenceUrl = character.lockedCandidateId && activeProject.id === projectId
          ? activeProject.shots.find((shot) => (
            shot.currentImageUrl
            && (!shot.characterIds?.length || shot.characterIds.includes(character.id))
          ))?.currentImageUrl ?? activeProject.shots.find((shot) => shot.currentImageUrl)?.currentImageUrl
          : undefined
        const lockedCandidate = character.lockedCandidateId
          ? character.candidates.find((candidate) => candidate.id === character.lockedCandidateId)
          : undefined
        const lockedImageUrl = lockedReferenceUrl || lockedCandidate?.assetUrl
        return <article key={character.id}>
        <header><div><p className="eyebrow">{localizeCharacterRole(character.role)}</p><h3>{character.name}</h3><p>{character.visualBrief}</p></div><StatusBadge status={character.status} /></header>
        {lockedCandidate && lockedImageUrl ? (
          <div className="preproduction-locked-look">
            <img alt={`${character.name} 已锁定形象`} src={lockedImageUrl} />
            <div>
              <strong>已锁定形象</strong>
              <small>候选 {lockedCandidate.ordinal}{lockedReferenceUrl ? ' · 项目参考镜头' : ''}</small>
            </div>
          </div>
        ) : (
          <div className="character-candidate-grid">{character.candidates.map((candidate) => {
            const active = selected[character.id] === candidate.id
            return <button className={`character-candidate ${active ? 'character-candidate--selected' : ''}`} key={candidate.id} onClick={() => setSelected((value) => ({ ...value, [character.id]: candidate.id }))}><img alt={`${character.name} 候选 ${candidate.ordinal}`} src={candidate.assetUrl} /><span><strong>候选 {candidate.ordinal}</strong><small>生成种子 · {candidate.seed}</small></span>{active ? <em><Check size={15} />已选择</em> : null}</button>
          })}</div>
        )}
        {character.lockedCandidateId ? null : (
          <footer>
            <Button disabled={!selected[character.id] || busy !== null} onClick={() => void lockCharacter(character.id)}>
              {busy === character.id ? <LoaderCircle className="spin" size={16} /> : <LockKeyhole size={16} />}
              锁定 {character.name}
            </Button>
          </footer>
        )}
      </article>
      })}</div>
    </Surface>

    <section className="preproduction-assets">
      <article><p className="eyebrow">造型版本</p><h2>{workspace.looks.length} 个已生成版本</h2>{workspace.looks.map((look) => <div key={look.id}><strong>{localizeDisplayText(look.label)}</strong><StatusBadge status={look.status} /><span>第 {look.version} 版 · {localizeDisplayText(look.usageScope)}</span></div>)}</article>
      <article><p className="eyebrow">声音安全</p><h2>声音档案</h2>{workspace.voices.map((voice) => <div key={voice.id}><Mic2 size={15} /><strong>{voice.voiceKey}</strong><span>{getStatusLabel(voice.consentStatus)}</span><small>{voice.cloningEnabled ? '已开启克隆' : '真人声音克隆关闭'}</small></div>)}</article>
      <article><p className="eyebrow">世界资产</p><h2>场景与道具</h2>{workspace.locations.map((location) => <div key={location.id}><strong>{location.name}</strong><span>场景第 {location.version} 版</span></div>)}{workspace.props.map((prop) => <div key={prop.id}><strong>{prop.name}</strong><span>道具第 {prop.version} 版</span></div>)}</article>
    </section>

    <section className="character-lock-bar"><div><LockKeyhole size={18} /><span><strong>第 3 阶段 · 视觉设定</strong><small>批准后，所有分镜镜头都会绑定稳定的角色造型、场景、道具和声音编号。</small></span></div><Button disabled={!canApprove || busy !== null} onClick={() => setApproveOpen(true)}>{busy === 'approve' ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}批准第 3 阶段并生成分镜</Button></section>

    <ImpactConfirmModal
      confirmLabel="批准并生成分镜"
      description="批准后无法直接回退，只能通过创建修改版重新走流程。"
      items={[
        { icon: <LockKeyhole size={16} />, title: '锁定视觉资产引用', detail: `${workspace.characters.length} 个角色、${workspace.looks.length} 个造型与场景道具编号将绑定到分镜。` },
        { icon: <Sparkles size={16} />, title: '启动分镜生成', detail: '系统将依据已批准剧本自动拆镜并装配节奏样片。' },
        { icon: <UsersRound size={16} />, title: '进入第 4 阶段', detail: '批准后会跳转到任务页，等待分镜与节奏样片就绪。' },
      ]}
      loading={busy === 'approve'}
      onClose={() => { if (busy !== 'approve') setApproveOpen(false) }}
      onConfirm={() => void approve()}
      open={approveOpen}
      subtitle="确认前期资产已完整锁定后再继续。"
      title="批准第 3 阶段？"
    />
  </div>
}
