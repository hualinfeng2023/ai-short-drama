import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, Check, LoaderCircle, LockKeyhole, Mic2, RefreshCw, Sparkles, UsersRound } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  approvePreproduction,
  fetchPreproduction,
  fetchProjectJobs,
  fetchProject,
  generateWorldAssetReference,
  lockCharacterCandidate,
  lockWorldAssetReference,
  type PreproductionWorkspace,
} from '../api/client'
import { Button, PageHeader, StatusBadge, Surface, getStatusLabel } from '../components/ui'
import { ImpactConfirmModal } from '../components/ConfirmModal'
import { PageLoadingSkeleton } from '../components/PageLoadingSkeleton'
import { ServiceRequiredState } from '../components/ServiceRequiredState'
import { useStudio } from '../store/StudioContext'
import { useToast } from '../store/ToastContext'
import { localizeCharacterRole, localizeDisplayText } from '../utils/localizeDisplayText'
import type { Job, ProjectRecord } from '../types'

const PREPRODUCTION_COMPLETE_STATUSES = new Set([
  'PREPRODUCTION_APPROVED',
  'STORYBOARD_READY',
  'STORYBOARD_APPROVED',
  'CHARACTER_LOCKED',
  'PRODUCING',
  'PREVIEW_READY',
  'APPROVED',
  'EXPORTING',
  'EXPORTED',
])

const ACTIVE_WORLD_GENERATION_STATUSES = new Set([
  'PENDING',
  'RETRY_WAIT',
  'RUNNING',
  'CANCEL_REQUESTED',
])

function formatLookLabel(label: string) {
  return localizeDisplayText(label).replace(/^造型\s*\d+\s*·\s*/, '')
}

function formatVoiceSetting(payload: Record<string, unknown>) {
  const text = (key: string) => typeof payload[key] === 'string' ? payload[key].trim() : ''
  const description = text('voice_description')
  const ageLabels: Record<string, string> = {
    adult: '成年声线',
    young_adult: '青年声线',
    child: '儿童声线',
    senior: '年长声线',
  }
  const expressionLabels: Record<string, string> = {
    neutral: '中性表达',
    feminine: '偏女性表达',
    masculine: '偏男性表达',
  }
  const toneLabels: Record<string, string> = {
    'natural-cinematic': '自然、克制的电影感',
  }
  const languageLabels: Record<string, string> = {
    'zh-CN': '普通话',
    'en-US': '美式英语',
  }
  const summary = description || [
    ageLabels[text('age_impression')] ?? text('age_impression'),
    expressionLabels[text('gender_expression')] ?? text('gender_expression'),
  ].filter(Boolean).join(' · ') || '尚未填写声线描述'
  const detail = description
    ? toneLabels[text('tone')] ?? text('tone')
    : [
        toneLabels[text('tone')] ?? text('tone'),
        languageLabels[text('language')] ?? text('language'),
      ].filter(Boolean).join(' · ')
  return { summary, detail }
}

export function PreproductionPage() {
  const { projectId } = useParams()
  const { project: activeProject } = useStudio()
  const navigate = useNavigate()
  const { notify } = useToast()
  const [project, setProject] = useState<ProjectRecord | null>(null)
  const [workspace, setWorkspace] = useState<PreproductionWorkspace | null>(null)
  const [worldJobs, setWorldJobs] = useState<Job[]>([])
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [selectedWorldCandidate, setSelectedWorldCandidate] = useState<Record<string, string>>({})
  const [worldGenerationCounts, setWorldGenerationCounts] = useState<Record<string, number>>({})
  const [worldAdjustments, setWorldAdjustments] = useState<Record<string, string>>({})
  const [editingWorldAssetId, setEditingWorldAssetId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [approveOpen, setApproveOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!projectId) return
    const [nextProject, nextWorkspace, nextJobs] = await Promise.all([
      fetchProject(projectId),
      fetchPreproduction(projectId),
      fetchProjectJobs(projectId),
    ])
    setProject(nextProject)
    setWorkspace(nextWorkspace)
    setWorldJobs(nextJobs.filter((job) => job.jobType === 'GENERATE_WORLD_ASSET_IMAGE'))
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

  async function generateWorldReference(
    assetType: 'location' | 'prop',
    versionId: string,
    name: string,
  ) {
    if (!projectId || !project) return
    setBusy(`generate-${versionId}`)
    setError(null)
    try {
      const count = worldGenerationCounts[versionId] ?? 1
      const jobs = await generateWorldAssetReference(
        projectId,
        assetType,
        versionId,
        project.lockVersion,
        count,
      )
      setWorldJobs((current) => [
        ...jobs,
        ...current.filter((item) => jobs.every((job) => item.id !== job.id)),
      ])
      notify(`${name}的 ${count} 张不同风格参考图已进入生成队列。`)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '参考图生成失败')
    } finally {
      setBusy(null)
    }
  }

  async function lockWorldReference(
    assetType: 'location' | 'prop',
    versionId: string,
    assetId: string,
    name: string,
  ) {
    if (!projectId || !project) return
    setBusy(`lock-${versionId}`)
    setError(null)
    try {
      await lockWorldAssetReference(
        projectId,
        assetType,
        versionId,
        assetId,
        project.lockVersion,
      )
      notify(`${name}参考图已锁定。`)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '参考图锁定失败')
    } finally {
      setBusy(null)
    }
  }

  async function refineWorldReference(
    assetType: 'location' | 'prop',
    versionId: string,
    sourceAssetId: string,
    name: string,
  ) {
    if (!projectId || !project) return
    const adjustmentPrompt = worldAdjustments[versionId]?.trim()
    if (!adjustmentPrompt) return
    setBusy(`refine-${versionId}`)
    setError(null)
    try {
      const jobs = await generateWorldAssetReference(
        projectId,
        assetType,
        versionId,
        project.lockVersion,
        1,
        sourceAssetId,
        adjustmentPrompt,
      )
      setWorldJobs((current) => [
        ...jobs,
        ...current.filter((item) => jobs.every((job) => item.id !== job.id)),
      ])
      setWorldAdjustments((current) => ({ ...current, [versionId]: '' }))
      setEditingWorldAssetId(null)
      notify(`${name}已基于所选参考图和修改要求进入生成队列。`)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '参考图修改生成失败')
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
  const worldAssets = [
    ...workspace.locations.map((item) => ({
      ...item,
      assetType: 'location' as const,
      typeLabel: '场景',
    })),
    ...workspace.props.map((item) => ({
      ...item,
      assetType: 'prop' as const,
      typeLabel: '关键道具',
    })),
  ]
  const worldAssetsReady = worldAssets.every((item) => item.referenceAssetIds.length > 0)
  const canApprove = (
    project.status === 'PREPRODUCTION_READY'
    && allLocked
    && looksReady
    && worldAssetsReady
  )
  const unlockedCharacterNames = workspace.characters
    .filter((character) => !character.lockedCandidateId)
    .map((character) => character.name)
  const missingLookCharacterNames = workspace.characters
    .filter((character) => (
      workspace.looks.every((look) => look.characterId !== character.id)
    ))
    .map((character) => character.name)
  const missingWorldAssetNames = worldAssets
    .filter((item) => item.referenceAssetIds.length === 0)
    .map((item) => item.name)
  const approvalDisabled = !canApprove || busy !== null
  const preproductionComplete = PREPRODUCTION_COMPLETE_STATUSES.has(project.status)
  const approvalDisabledReasons = preproductionComplete
    ? ['第 3 阶段已经批准，无需重复操作']
    : [
      busy !== null ? '正在处理其他前期资产操作，请稍候' : null,
      project.status !== 'PREPRODUCTION_READY'
        ? `项目当前为「${getStatusLabel(project.status)}」，请先完成上一阶段`
        : null,
      workspace.characters.length === 0
        ? '尚未生成角色候选'
        : unlockedCharacterNames.length > 0
          ? `请先锁定角色：${unlockedCharacterNames.join('、')}`
          : null,
      missingLookCharacterNames.length > 0
        ? `请先为角色准备至少 1 个造型：${missingLookCharacterNames.join('、')}`
        : null,
      missingWorldAssetNames.length > 0
        ? `请先生成并锁定参考图：${missingWorldAssetNames.join('、')}`
        : null,
    ].filter((reason): reason is string => Boolean(reason))
  const approvalDisabledReason = approvalDisabled
    ? `暂不可批准：${approvalDisabledReasons.join('；')}`
    : null
  const approvalTooltipId = 'preproduction-approval-disabled-reason'

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
      <div className="section-heading"><div><h2>角色准备情况</h2><p>逐个确认角色的身份参考图、当前造型和声音权限；本阶段不会自动替换已锁定的身份。</p></div></div>
      <div className="preproduction-character-list character-readiness-list">{workspace.characters.map((character) => {
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
        const looks = workspace.looks.filter((look) => look.characterId === character.id)
        const voices = workspace.voices.filter((voice) => voice.characterId === character.id)
        const look = looks.reduce((latest, item) => (
          !latest || item.version > latest.version ? item : latest
        ), looks[0])
        const voice = voices.reduce((latest, item) => (
          !latest || item.version > latest.version ? item : latest
        ), voices[0])
        const voiceSetting = voice ? formatVoiceSetting(voice.payload) : null
        return <article key={character.id}>
        <header><div><p className="eyebrow">{localizeCharacterRole(character.role)}</p><h3>{character.name}</h3><p>{character.visualBrief}</p></div></header>
        <div className="character-readiness__grid">
          <div className="character-readiness__identity">
            {lockedCandidate && lockedImageUrl ? <img alt={`${character.name} 已锁定形象`} src={lockedImageUrl} /> : <div className="character-readiness__placeholder"><UsersRound size={20} /></div>}
            <div>
              <span><LockKeyhole size={14} />身份参考</span>
              <strong>{character.lockedCandidateId ? '已锁定形象' : '等待锁定'}</strong>
              <small>{lockedCandidate ? `候选 ${lockedCandidate.ordinal}${lockedReferenceUrl ? ' · 项目参考镜头' : ''}` : '请从下方候选中选择'}</small>
            </div>
          </div>
          <div className="character-assets__summary">
            <span><Sparkles size={14} />当前造型</span>
            <strong>{look ? formatLookLabel(look.label) : '尚未准备'}</strong>
            <small>{look ? `第 ${look.version} 版 · ${localizeDisplayText(look.usageScope)} · ${getStatusLabel(look.status)}` : '需要生成并批准角色造型'}</small>
          </div>
          <div className="character-assets__summary character-assets__summary--voice">
            <span><Mic2 size={14} />声音设定</span>
            <strong>{voiceSetting?.summary ?? '尚未准备'}</strong>
            {voiceSetting?.detail ? <small>{voiceSetting.detail}</small> : null}
            <small>{voice ? `${getStatusLabel(voice.consentStatus)} · ${voice.cloningEnabled ? '真人声音克隆已开启' : '真人声音克隆关闭'}` : '需要配置声音档案'}</small>
          </div>
        </div>
        {character.lockedCandidateId ? null : (
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

    <section className="preproduction-assets preproduction-assets--world-only">
      <article className="world-assets">
        <p className="eyebrow">世界资产</p>
        <h2>场景与关键道具参考图</h2>
        <p className="world-assets__intro">每轮可生成 1–3 张同主题、不同风格的候选图；人工选择并锁定其中一张后，分镜和后续镜头才会引用它。</p>
        <div className="world-assets__grid">{worldAssets.map((item) => {
          const latestBatchId = item.imageCandidates[0]?.batchId
          const batchCandidates = latestBatchId
            ? item.imageCandidates.filter((candidate) => candidate.batchId === latestBatchId)
            : item.imageCandidates.slice(0, 1)
          const visibleCandidates = batchCandidates
          const candidate = visibleCandidates.find((candidate) => (
            candidate.id === selectedWorldCandidate[item.id]
          )) ?? visibleCandidates[0]
          const generationJobs = worldJobs.filter((job) => (
            job.entityId === item.id
            && ACTIVE_WORLD_GENERATION_STATUSES.has(job.status)
          ))
          const generationJob = generationJobs[0]
          const isGenerating = generationJobs.length > 0
          const hasLockedReference = item.referenceAssetIds.length > 0
          const candidateIsLocked = Boolean(
            candidate && item.referenceAssetIds.includes(candidate.id),
          )
          const stateLabel = isGenerating
            ? generationJob?.status === 'RETRY_WAIT' ? '等待重试' : '生成中'
            : hasLockedReference
            ? candidateIsLocked ? '已锁定' : '新候选待确认'
            : candidate ? '待确认' : '缺少参考图'
          return <section
            aria-busy={isGenerating}
            className="world-asset-card"
            key={`${item.assetType}-${item.id}`}
          >
            <header>
              <div><span>{item.typeLabel}</span><h3>{item.name}</h3></div>
              <strong data-ready={hasLockedReference}>{stateLabel}</strong>
            </header>
            <div className={`world-asset-card__candidates world-asset-card__candidates--${Math.min(Math.max(visibleCandidates.length, 1), 3)}`}>
              {visibleCandidates.length > 0 ? visibleCandidates.map((option) => {
                const optionIsLocked = item.referenceAssetIds.includes(option.id)
                const optionIsSelected = option.id === candidate?.id
                return <button
                  aria-label={`选择${item.name}${option.styleLabel ?? ''}参考图`}
                  aria-pressed={optionIsSelected}
                  className="world-asset-card__candidate"
                  data-selected={optionIsSelected}
                  key={option.id}
                  onClick={() => setSelectedWorldCandidate((current) => ({
                    ...current,
                    [item.id]: option.id,
                  }))}
                  type="button"
                >
                  <img alt={`${item.name}${option.styleLabel ?? ''}参考图候选`} src={option.assetUrl} />
                  <span>{option.styleLabel ?? '参考图候选'}{optionIsLocked ? ' · 已锁定' : ''}</span>
                </button>
              }) : (
                <div className="world-asset-card__media">
                  <div className="world-asset-card__placeholder"><Sparkles size={22} /><span>尚未生成参考图</span></div>
                </div>
              )}
            </div>
            <div className="world-asset-card__generation">
              <label>
                <span>本轮生成</span>
                <select
                  aria-label={`${item.name}本轮生成数量`}
                  disabled={busy !== null || isGenerating}
                  onChange={(event) => setWorldGenerationCounts((current) => ({
                    ...current,
                    [item.id]: Number(event.target.value),
                  }))}
                  value={worldGenerationCounts[item.id] ?? 1}
                >
                  <option value={1}>1 张</option>
                  <option value={2}>2 张</option>
                  <option value={3}>3 张</option>
                </select>
              </label>
              <small>主题与关键结构保持一致，系统自动分配不同风格。</small>
            </div>
            {generationJob ? (
              <div className="world-asset-card__batch-status" role="status">
                <LoaderCircle aria-hidden="true" className="spin" size={16} />
                <span>{generationJobs.length} 张生成中 · {localizeDisplayText(generationJob.stage) || '正在生成参考图'}</span>
              </div>
            ) : null}
            <small>{item.typeLabel}第 {item.version} 版</small>
            <footer>
              <Button
                disabled={busy !== null || isGenerating}
                onClick={() => void generateWorldReference(item.assetType, item.id, item.name)}
                size="sm"
                variant="secondary"
              >
                {busy === `generate-${item.id}` || isGenerating ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}
                {isGenerating ? '生成中' : candidate ? '探索新风格' : '生成参考图'}
              </Button>
              {candidate ? (
                <Button
                  disabled={busy !== null || isGenerating}
                  onClick={() => setEditingWorldAssetId((current) => (
                    current === item.id ? null : item.id
                  ))}
                  size="sm"
                  variant="secondary"
                >
                  基于所选图修改
                </Button>
              ) : null}
              {candidate && !candidateIsLocked ? (
                <Button
                  disabled={busy !== null || isGenerating}
                  onClick={() => void lockWorldReference(
                    item.assetType,
                    item.id,
                    candidate.id,
                    item.name,
                  )}
                  size="sm"
                >
                  {busy === `lock-${item.id}` ? <LoaderCircle className="spin" size={15} /> : <LockKeyhole size={15} />}
                  锁定所选参考图
                </Button>
              ) : null}
            </footer>
            {candidate && editingWorldAssetId === item.id ? (
              <div className="world-asset-card__refinement">
                <div>
                  <strong>基于所选图修改</strong>
                  <small>将生成新的待确认候选，原图和当前锁定版本不会被覆盖。</small>
                </div>
                <label>
                  <span>修改要求</span>
                  <textarea
                    aria-label={`${item.name}参考图修改要求`}
                    maxLength={500}
                    onChange={(event) => setWorldAdjustments((current) => ({
                      ...current,
                      [item.id]: event.target.value,
                    }))}
                    placeholder="例如：保留房间结构和培养舱位置，将灯光改为故障红色应急灯，并增加地面积水。"
                    rows={3}
                    value={worldAdjustments[item.id] ?? ''}
                  />
                </label>
                <div>
                  <small>{(worldAdjustments[item.id] ?? '').length} / 500</small>
                  <Button
                    disabled={!worldAdjustments[item.id]?.trim() || busy !== null || isGenerating}
                    onClick={() => void refineWorldReference(
                      item.assetType,
                      item.id,
                      candidate.id,
                      item.name,
                    )}
                    size="sm"
                  >
                    {busy === `refine-${item.id}` ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}
                    按修改要求生成
                  </Button>
                </div>
              </div>
            ) : null}
          </section>
        })}</div>
      </article>
    </section>

    <section className="character-lock-bar">
      <div><LockKeyhole size={18} /><span><strong>第 3 阶段 · 视觉设定</strong><small>批准后，所有分镜镜头都会绑定稳定的角色造型、场景、道具和声音编号。</small></span></div>
      <span
        aria-describedby={approvalDisabledReason ? approvalTooltipId : undefined}
        className="preproduction-approval-control"
        tabIndex={approvalDisabledReason ? 0 : undefined}
        title={approvalDisabledReason ?? undefined}
      >
        <Button
          aria-describedby={approvalDisabledReason ? approvalTooltipId : undefined}
          disabled={approvalDisabled}
          onClick={() => setApproveOpen(true)}
        >
          {busy === 'approve' ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}
          批准第 3 阶段并生成分镜
        </Button>
        {approvalDisabledReason ? (
          <span id={approvalTooltipId} role="tooltip">{approvalDisabledReason}</span>
        ) : null}
      </span>
    </section>

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
