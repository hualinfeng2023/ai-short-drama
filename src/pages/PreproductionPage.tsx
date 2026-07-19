import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Check, LoaderCircle, LockKeyhole, Mic2, RefreshCw } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  approvePreproduction,
  fetchPreproduction,
  fetchProject,
  lockCharacterCandidate,
  type PreproductionWorkspace,
} from '../api/client'
import { Button, PageHeader, StatusBadge, getStatusLabel } from '../components/ui'
import { ServiceRequiredState } from '../components/ServiceRequiredState'
import { useStudio } from '../store/StudioContext'
import { localizeDisplayText } from '../utils/localizeDisplayText'
import type { ProjectRecord } from '../types'

export function PreproductionPage() {
  const { projectId } = useParams()
  const { project: activeProject } = useStudio()
  const navigate = useNavigate()
  const [project, setProject] = useState<ProjectRecord | null>(null)
  const [workspace, setWorkspace] = useState<PreproductionWorkspace | null>(null)
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
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
    return <div className="page brief-page-state"><LoaderCircle className="spin" size={22} /><strong>正在读取角色、造型、场景与声音资产…</strong></div>
  }

  const allLocked = workspace.characters.length > 0
    && workspace.characters.every((character) => Boolean(character.lockedCandidateId))
  const looksReady = workspace.characters.every(
    (character) => workspace.looks.filter((look) => look.characterId === character.id).length >= 1,
  )
  const canApprove = project.status === 'PREPRODUCTION_READY' && allLocked && looksReady

  return <div className="page page--preproduction">
    <PageHeader
      eyebrow="第 3 阶段 · 视觉与声音设定"
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

    <section className="story-section">
      <div className="section-heading"><div><p className="eyebrow">角色形象</p><h2>已锁定身份引用</h2><p>角色身份已在剧本生成前人工锁定；本阶段只读取身份与基础 Look Version，不会替换候选或覆盖既有镜头。</p></div></div>
      <div className="preproduction-character-list">{workspace.characters.map((character) => {
        const lockedReferenceUrl = character.lockedCandidateId && activeProject.id === projectId
          ? activeProject.shots.find((shot) => (
            shot.currentImageUrl
            && (!shot.characterIds?.length || shot.characterIds.includes(character.id))
          ))?.currentImageUrl ?? activeProject.shots.find((shot) => shot.currentImageUrl)?.currentImageUrl
          : undefined
        return <article key={character.id}>
        <header><div><p className="eyebrow">{localizeDisplayText(character.role)}</p><h3>{character.name}</h3><p>{character.visualBrief}</p></div><StatusBadge status={character.status} /></header>
        <div className="character-candidate-grid">{character.candidates.map((candidate) => {
          const active = selected[character.id] === candidate.id
          const usesProjectFrame = candidate.id === character.lockedCandidateId && Boolean(lockedReferenceUrl)
          return <button className={`character-candidate ${active ? 'character-candidate--selected' : ''}`} disabled={Boolean(character.lockedCandidateId)} key={candidate.id} onClick={() => setSelected((value) => ({ ...value, [character.id]: candidate.id }))}><img alt={`${character.name} 候选 ${candidate.ordinal}`} src={usesProjectFrame ? lockedReferenceUrl : candidate.assetUrl} /><span><strong>候选 {candidate.ordinal}</strong><small>{usesProjectFrame ? '已锁定形象 · 项目参考镜头' : `生成种子 · ${candidate.seed}`}</small></span>{active ? <em><Check size={15} />{character.lockedCandidateId ? '已锁定' : '已选择'}</em> : null}</button>
        })}</div>
        <footer>{character.lockedCandidateId ? <span><LockKeyhole size={16} />角色形象已冻结</span> : <Button disabled={!selected[character.id] || busy !== null} onClick={() => void lockCharacter(character.id)}>{busy === character.id ? <LoaderCircle className="spin" size={16} /> : <LockKeyhole size={16} />}锁定 {character.name}</Button>}</footer>
      </article>
      })}</div>
    </section>

    <section className="preproduction-assets">
      <article><p className="eyebrow">造型版本</p><h2>{workspace.looks.length} 个已生成版本</h2>{workspace.looks.map((look) => <div key={look.id}><strong>{localizeDisplayText(look.label)}</strong><span>第 {look.version} 版 · {localizeDisplayText(look.usageScope)}</span><StatusBadge status={look.status} /></div>)}</article>
      <article><p className="eyebrow">声音安全</p><h2>声音档案</h2>{workspace.voices.map((voice) => <div key={voice.id}><Mic2 size={15} /><strong>{voice.voiceKey}</strong><span>{getStatusLabel(voice.consentStatus)}</span><small>{voice.cloningEnabled ? '已开启克隆' : '真人声音克隆关闭'}</small></div>)}</article>
      <article><p className="eyebrow">世界资产</p><h2>场景与道具</h2>{workspace.locations.map((location) => <div key={location.id}><strong>{location.name}</strong><span>场景第 {location.version} 版</span></div>)}{workspace.props.map((prop) => <div key={prop.id}><strong>{prop.name}</strong><span>道具第 {prop.version} 版</span></div>)}</article>
    </section>

    <section className="character-lock-bar"><div><LockKeyhole size={18} /><span><strong>第 3 阶段 · 视觉设定</strong><small>批准后，所有分镜镜头都会绑定稳定的角色造型、场景、道具和声音编号。</small></span></div><Button disabled={!canApprove || busy !== null} onClick={() => void approve()}>{busy === 'approve' ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}批准第 3 阶段并生成分镜</Button></section>
  </div>
}
