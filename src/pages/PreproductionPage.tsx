import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, AlertCircle, Check, ChevronDown, Download, LoaderCircle, LockKeyhole, Maximize2, Mic2, RefreshCw, Sparkles, Trash2, UsersRound, ZoomIn, ZoomOut } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  approvePreproduction,
  deleteWorldAssetReference,
  fetchPreproduction,
  fetchProjectJobs,
  fetchProject,
  generateWorldAssetReference,
  lockCharacterCandidate,
  lockWorldAssetReference,
  previewWorldAssetReferencePrompt,
  type PreproductionWorkspace,
} from '../api/client'
import { Button, Modal, PageHeader, SelectControl, StatusBadge, Surface, getStatusLabel } from '../components/ui'
import { ConfirmModal, ImpactConfirmModal } from '../components/ConfirmModal'
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

const MAX_WORLD_CHARACTER_REFS = 3

type WorldAspectRatio = '1:1' | '4:3' | '3:4' | '16:9' | '9:16' | '3:2' | '2:3' | '21:9'

const WORLD_ASPECT_RATIOS: Array<{ id: WorldAspectRatio; label: string }> = [
  { id: '16:9', label: '横屏宽画幅' },
  { id: '9:16', label: '竖屏短视频' },
  { id: '1:1', label: '正方形' },
  { id: '4:3', label: '横向标准' },
  { id: '3:4', label: '竖向标准' },
  { id: '3:2', label: '横向摄影' },
  { id: '2:3', label: '竖向摄影' },
  { id: '21:9', label: '超宽银幕' },
]

const DEFAULT_WORLD_ASPECT_RATIO: WorldAspectRatio = '16:9'

interface WorldAssetImageViewer {
  assetType: 'location' | 'prop'
  versionId: string
  assetName: string
  typeLabel: string
  mode: 'compose' | 'review'
  assetId?: string
  styleLabel?: string
  assetUrl?: string
  locked?: boolean
  generationPrompt?: string
  characterIds?: string[]
  aspectRatio?: string
}

function worldDetailPromptKey(viewer: Pick<WorldAssetImageViewer, 'assetId' | 'versionId'>) {
  return viewer.assetId ? `asset:${viewer.assetId}` : `compose:${viewer.versionId}`
}

function buildWorldAssetDownloadName(assetName: string, styleLabel: string): string {
  const safeName = `${assetName}-${styleLabel}`.replace(/[\\/:*?"<>|]+/g, '_').trim() || 'world-asset'
  return `${safeName}.png`
}

async function downloadImageAsset(assetUrl: string, filename: string): Promise<void> {
  const response = await fetch(assetUrl)
  if (!response.ok) throw new Error('download failed')
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(objectUrl)
}

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
  const [worldAspectRatios, setWorldAspectRatios] = useState<Record<string, WorldAspectRatio>>({})
  const [worldCharacterRefs, setWorldCharacterRefs] = useState<Record<string, string[]>>({})
  const [worldPromptDraft, setWorldPromptDraft] = useState<Record<string, string>>({})
  const [worldPromptDefault, setWorldPromptDefault] = useState<Record<string, string>>({})
  const [worldPromptDirty, setWorldPromptDirty] = useState<Record<string, boolean>>({})
  const [worldPromptStale, setWorldPromptStale] = useState<Record<string, boolean>>({})
  const [worldPromptLoading, setWorldPromptLoading] = useState<Record<string, boolean>>({})
  const [worldPromptVariants, setWorldPromptVariants] = useState<Record<string, Array<{
    styleId: string
    styleLabel: string
    prompt: string
  }>>>({})
  const [worldPromptVariantIndex, setWorldPromptVariantIndex] = useState<Record<string, number>>({})
  const [worldImageViewer, setWorldImageViewer] = useState<WorldAssetImageViewer | null>(null)
  const [worldImageZoom, setWorldImageZoom] = useState(100)
  const [worldGenerateCountMenuOpen, setWorldGenerateCountMenuOpen] = useState(false)
  const [pendingDeleteWorldAsset, setPendingDeleteWorldAsset] = useState<WorldAssetImageViewer | null>(null)
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

  async function loadWorldPromptPreview(
    assetType: 'location' | 'prop',
    versionId: string,
    options?: {
      force?: boolean
      characterIds?: string[]
      sourceAssetId?: string
      adjustmentPrompt?: string
      promptKey?: string
    },
  ) {
    if (!projectId || !project) return
    const promptKey = options?.promptKey ?? `compose:${versionId}`
    const force = options?.force ?? false
    if (!force && worldPromptDirty[promptKey]) {
      setWorldPromptStale((current) => ({ ...current, [promptKey]: true }))
      return
    }
    setWorldPromptLoading((current) => ({ ...current, [promptKey]: true }))
    try {
      const preview = await previewWorldAssetReferencePrompt(
        projectId,
        assetType,
        versionId,
        project.lockVersion,
        {
          count: worldGenerationCounts[versionId] ?? 1,
          characterIds: options?.characterIds ?? worldCharacterRefs[versionId] ?? [],
          sourceAssetId: options?.sourceAssetId,
          adjustmentPrompt: options?.adjustmentPrompt,
        },
      )
      setWorldPromptDefault((current) => ({ ...current, [promptKey]: preview.basePrompt }))
      setWorldPromptDraft((current) => ({ ...current, [promptKey]: preview.basePrompt }))
      setWorldPromptDirty((current) => ({ ...current, [promptKey]: false }))
      setWorldPromptStale((current) => ({ ...current, [promptKey]: false }))
      setWorldPromptVariants((current) => ({ ...current, [promptKey]: preview.variants }))
      setWorldPromptVariantIndex((current) => ({ ...current, [promptKey]: 0 }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '提示词预览失败')
    } finally {
      setWorldPromptLoading((current) => ({ ...current, [promptKey]: false }))
    }
  }

  function seedWorldPromptFromGeneration(
    promptKey: string,
    generationPrompt: string,
  ) {
    setWorldPromptDefault((current) => ({ ...current, [promptKey]: generationPrompt }))
    setWorldPromptDraft((current) => ({ ...current, [promptKey]: generationPrompt }))
    setWorldPromptDirty((current) => ({ ...current, [promptKey]: false }))
    setWorldPromptStale((current) => ({ ...current, [promptKey]: false }))
  }

  async function cycleWorldPromptVariant() {
    if (!worldImageViewer) return
    const promptKey = worldDetailPromptKey(worldImageViewer)
    let variants = worldPromptVariants[promptKey] ?? []
    if (variants.length === 0) {
      if (!projectId || !project) return
      setWorldPromptLoading((current) => ({ ...current, [promptKey]: true }))
      try {
        const preview = await previewWorldAssetReferencePrompt(
          projectId,
          worldImageViewer.assetType,
          worldImageViewer.versionId,
          project.lockVersion,
          {
            count: worldGenerationCounts[worldImageViewer.versionId] ?? 1,
            characterIds: worldCharacterRefs[worldImageViewer.versionId] ?? [],
          },
        )
        variants = preview.variants
        setWorldPromptVariants((current) => ({ ...current, [promptKey]: preview.variants }))
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '提示词预览失败')
        return
      } finally {
        setWorldPromptLoading((current) => ({ ...current, [promptKey]: false }))
      }
    }
    if (variants.length === 0) return
    const nextIndex = ((worldPromptVariantIndex[promptKey] ?? -1) + 1) % variants.length
    const nextPrompt = variants[nextIndex]?.prompt?.trim()
    if (!nextPrompt) return
    setWorldPromptDraft((current) => ({ ...current, [promptKey]: nextPrompt }))
    setWorldPromptDirty((current) => ({
      ...current,
      [promptKey]: nextPrompt !== (worldPromptDefault[promptKey] ?? '').trim(),
    }))
    setWorldPromptVariantIndex((current) => ({ ...current, [promptKey]: nextIndex }))
  }

  function toggleWorldCharacterRef(
    assetType: 'location' | 'prop',
    versionId: string,
    characterId: string,
  ) {
    const selectedIds = worldCharacterRefs[versionId] ?? []
    let nextIds: string[]
    if (selectedIds.includes(characterId)) {
      nextIds = selectedIds.filter((id) => id !== characterId)
    } else if (selectedIds.length >= MAX_WORLD_CHARACTER_REFS) {
      notify(`最多关联 ${MAX_WORLD_CHARACTER_REFS} 个角色形象`)
      return
    } else {
      nextIds = [...selectedIds, characterId]
    }
    setWorldCharacterRefs((current) => ({ ...current, [versionId]: nextIds }))
    const viewingCompose = worldImageViewer?.versionId === versionId && !worldImageViewer.assetId
    const promptKey = `compose:${versionId}`
    if (viewingCompose && !worldPromptDirty[promptKey]) {
      void loadWorldPromptPreview(assetType, versionId, { characterIds: nextIds, promptKey })
    } else {
      setWorldPromptDefault((current) => {
        const next = { ...current }
        delete next[promptKey]
        return next
      })
      if (worldPromptDirty[promptKey]) {
        setWorldPromptStale((current) => ({ ...current, [promptKey]: true }))
      }
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
      setSelectedWorldCandidate((current) => ({ ...current, [versionId]: assetId }))
      setWorldImageViewer((current) => (
        current && current.assetId === assetId
          ? { ...current, locked: true }
          : current
      ))
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '参考图锁定失败')
    } finally {
      setBusy(null)
    }
  }

  async function confirmDeleteWorldReference() {
    if (!projectId || !project || !pendingDeleteWorldAsset) return
    const target = pendingDeleteWorldAsset
    const assetId = target.assetId
    if (!assetId) return
    setBusy(`delete-${assetId}`)
    setError(null)
    try {
      await deleteWorldAssetReference(
        projectId,
        target.assetType,
        target.versionId,
        assetId,
        project.lockVersion,
      )
      setPendingDeleteWorldAsset(null)
      setSelectedWorldCandidate((current) => {
        if (current[target.versionId] !== assetId) return current
        const next = { ...current }
        delete next[target.versionId]
        return next
      })
      closeWorldImageViewer()
      notify(`${target.assetName}参考图已删除。`)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '参考图删除失败')
    } finally {
      setBusy(null)
    }
  }

  function openWorldImageViewer(viewer: WorldAssetImageViewer) {
    setWorldImageViewer(viewer)
    setWorldImageZoom(100)
    if (viewer.characterIds?.length) {
      setWorldCharacterRefs((current) => ({ ...current, [viewer.versionId]: viewer.characterIds! }))
    }
    if (viewer.aspectRatio && WORLD_ASPECT_RATIOS.some((option) => option.id === viewer.aspectRatio)) {
      setWorldAspectRatios((current) => ({
        ...current,
        [viewer.versionId]: viewer.aspectRatio as WorldAspectRatio,
      }))
    }
    if (viewer.assetId) {
      setSelectedWorldCandidate((current) => ({
        ...current,
        [viewer.versionId]: viewer.assetId!,
      }))
    }
    const promptKey = worldDetailPromptKey(viewer)
    if (viewer.generationPrompt?.trim()) {
      seedWorldPromptFromGeneration(promptKey, viewer.generationPrompt.trim())
      return
    }
    if (!worldPromptDirty[promptKey] || !worldPromptDraft[promptKey]) {
      void loadWorldPromptPreview(viewer.assetType, viewer.versionId, { promptKey })
    }
  }

  function openWorldGenerateDetail(
    assetType: 'location' | 'prop',
    versionId: string,
    assetName: string,
    typeLabel: string,
    candidate?: {
      id: string
      assetUrl: string
      styleLabel?: string | null
      locked: boolean
      generationPrompt?: string
      characterIds?: string[]
      aspectRatio?: string
    },
  ) {
    openWorldImageViewer({
      assetType,
      versionId,
      assetName,
      typeLabel,
      mode: candidate ? 'review' : 'compose',
      assetId: candidate?.id,
      assetUrl: candidate?.assetUrl,
      styleLabel: candidate?.styleLabel ?? (candidate ? '参考图候选' : '待生成'),
      locked: candidate?.locked ?? false,
      generationPrompt: candidate?.generationPrompt,
      characterIds: candidate?.characterIds,
      aspectRatio: candidate?.aspectRatio,
    })
  }

  function closeWorldImageViewer() {
    setWorldImageViewer(null)
    setWorldImageZoom(100)
    setWorldGenerateCountMenuOpen(false)
  }

  async function downloadWorldImage() {
    if (!worldImageViewer?.assetUrl) return
    try {
      await downloadImageAsset(
        worldImageViewer.assetUrl,
        buildWorldAssetDownloadName(
          worldImageViewer.assetName,
          worldImageViewer.styleLabel ?? '参考图',
        ),
      )
    } catch {
      setError('图片下载失败，请稍后重试')
    }
  }

  async function confirmWorldGenerateFromDetail() {
    if (!projectId || !project || !worldImageViewer) return
    const versionId = worldImageViewer.versionId
    const name = worldImageViewer.assetName
    const assetType = worldImageViewer.assetType
    const promptKey = worldDetailPromptKey(worldImageViewer)
    setBusy(`generate-${versionId}`)
    setError(null)
    try {
      const count = worldGenerationCounts[versionId] ?? 1
      const draft = worldPromptDraft[promptKey]?.trim()
      const defaultPrompt = worldPromptDefault[promptKey]?.trim()
      const customBasePrompt = (
        worldPromptDirty[promptKey]
        && draft
        && draft !== defaultPrompt
        && draft.length >= 20
      ) ? draft : undefined
      const jobs = await generateWorldAssetReference(
        projectId,
        assetType,
        versionId,
        project.lockVersion,
        count,
        undefined,
        undefined,
        {
          characterIds: worldCharacterRefs[versionId] ?? [],
          customBasePrompt,
          aspectRatio: worldAspectRatios[versionId] ?? DEFAULT_WORLD_ASPECT_RATIO,
        },
      )
      setWorldJobs((current) => [
        ...jobs,
        ...current.filter((item) => jobs.every((job) => item.id !== job.id)),
      ])
      notify(`${name}的 ${count} 张不同风格参考图已进入生成队列。`)
      closeWorldImageViewer()
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '参考图生成失败')
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
  const detailPromptKey = worldImageViewer ? worldDetailPromptKey(worldImageViewer) : ''
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
          const visibleCandidates = item.imageCandidates
          const batchKeysOldestFirst: string[] = []
          for (const option of [...visibleCandidates].reverse()) {
            const batchKey = option.batchId ?? `solo-${option.id}`
            if (!batchKeysOldestFirst.includes(batchKey)) {
              batchKeysOldestFirst.push(batchKey)
            }
          }
          const batchNumberByKey = new Map(
            batchKeysOldestFirst.map((batchKey, index) => [batchKey, index + 1]),
          )
          const candidate = visibleCandidates.find((option) => (
            option.id === selectedWorldCandidate[item.id]
          ))
            ?? visibleCandidates.find((option) => item.referenceAssetIds.includes(option.id))
            ?? visibleCandidates[0]
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
            <div className={`world-asset-card__candidates world-asset-card__candidates--${Math.min(Math.max(visibleCandidates.length + generationJobs.length, 1), 3)}`}>
              {generationJobs.map((job, jobIndex) => (
                <div
                  className="world-asset-card__candidate-shell"
                  data-generating="true"
                  key={job.id}
                >
                  <div
                    className="world-asset-card__candidate"
                    data-generating="true"
                    role="status"
                  >
                    <div className="world-asset-card__placeholder world-asset-card__placeholder--generating">
                      <LoaderCircle aria-hidden="true" className="spin" size={22} strokeWidth={1.5} />
                    </div>
                    <span>
                      {generationJobs.length > 1
                        ? `生成中 · ${jobIndex + 1}/${generationJobs.length}`
                        : '生成中'}
                    </span>
                  </div>
                </div>
              ))}
              {visibleCandidates.length > 0 ? visibleCandidates.map((option) => {
                const optionIsLocked = item.referenceAssetIds.includes(option.id)
                const optionIsSelected = option.id === candidate?.id
                const styleLabel = option.styleLabel ?? '参考图候选'
                const batchKey = option.batchId ?? `solo-${option.id}`
                const batchNumber = batchNumberByKey.get(batchKey) ?? 1
                const batchOptions = visibleCandidates.filter((entry) => (
                  (entry.batchId ?? `solo-${entry.id}`) === batchKey
                ))
                const optionIndex = batchOptions.findIndex((entry) => entry.id === option.id)
                const versionLabel = batchOptions.length > 1
                  ? `第 ${batchNumber} 批 · ${optionIndex + 1}`
                  : `第 ${batchNumber} 批`
                return <div
                  className="world-asset-card__candidate-shell"
                  data-selected={optionIsSelected}
                  key={option.id}
                >
                  <button
                    aria-label={`选择${item.name}${versionLabel}参考图`}
                    aria-pressed={optionIsSelected}
                    className="world-asset-card__candidate"
                    data-locked={optionIsLocked}
                    data-selected={optionIsSelected}
                    onClick={() => setSelectedWorldCandidate((current) => ({
                      ...current,
                      [item.id]: option.id,
                    }))}
                    type="button"
                  >
                    <img alt={`${item.name}${versionLabel}参考图候选`} src={option.assetUrl} />
                    <span>{versionLabel}{optionIsLocked ? ' · 已锁定' : ''}</span>
                  </button>
                  <button
                    aria-label={`全览${item.name}${styleLabel}参考图`}
                    className="world-asset-card__open"
                    onClick={() => openWorldImageViewer({
                      assetType: item.assetType,
                      versionId: item.id,
                      assetName: item.name,
                      typeLabel: item.typeLabel,
                      mode: 'review',
                      assetId: option.id,
                      styleLabel,
                      assetUrl: option.assetUrl,
                      locked: optionIsLocked,
                      generationPrompt: option.generationPrompt,
                      characterIds: option.characterIds,
                      aspectRatio: option.aspectRatio,
                    })}
                    title="全览"
                    type="button"
                  >
                    <Maximize2 size={15} />
                  </button>
                </div>
              }) : generationJobs.length === 0 ? (
                <div className="world-asset-card__candidate-shell">
                  <div className="world-asset-card__candidate" data-empty="true" role="status">
                    <div className="world-asset-card__placeholder">
                      <Sparkles size={22} strokeWidth={1.5} />
                    </div>
                    <span>尚未生成参考图</span>
                  </div>
                </div>
              ) : null}
            </div>
            <footer>
              <Button
                disabled={busy !== null || isGenerating}
                onClick={() => openWorldGenerateDetail(
                  item.assetType,
                  item.id,
                  item.name,
                  item.typeLabel,
                  candidate ? {
                    id: candidate.id,
                    assetUrl: candidate.assetUrl,
                    styleLabel: candidate.styleLabel,
                    locked: candidateIsLocked,
                    generationPrompt: candidate.generationPrompt,
                    characterIds: candidate.characterIds,
                    aspectRatio: candidate.aspectRatio,
                  } : undefined,
                )}
                size="sm"
                variant="secondary"
              >
                {busy === `generate-${item.id}` || isGenerating ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}
                {isGenerating ? '生成中' : candidate ? '探索新风格' : '生成参考图'}
              </Button>
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

    <Modal
      className="modal--identity-image-viewer modal--world-asset-detail"
      description={worldImageViewer
        ? worldImageViewer.assetUrl
          ? `${worldImageViewer.assetName} · ${worldImageViewer.styleLabel ?? '参考图'} · 完整参考图`
          : `${worldImageViewer.typeLabel} · ${worldImageViewer.assetName} · 确认提示词后生成`
        : undefined}
      footer={<>
        {worldImageViewer?.assetUrl && worldImageViewer.assetId ? (
          <Button
            disabled={busy !== null || worldImageViewer.locked}
            onClick={() => setPendingDeleteWorldAsset(worldImageViewer)}
            title={worldImageViewer.locked ? '已锁定的参考图不能删除' : '删除此参考图'}
            variant="danger"
          >
            <Trash2 size={15} />
            删除
          </Button>
        ) : null}
        {worldImageViewer?.assetUrl ? (
          <Button onClick={() => void downloadWorldImage()} variant="secondary">
            <Download size={15} />下载
          </Button>
        ) : null}
        {worldImageViewer ? (
          <div className={`world-asset-split-action${worldGenerateCountMenuOpen ? ' is-open' : ''}`}>
            <button
              className="world-asset-split-action__main"
              disabled={
                busy !== null
                || worldPromptLoading[detailPromptKey]
                || (worldPromptDraft[detailPromptKey] ?? '').trim().length < 20
              }
              onClick={() => {
                setWorldGenerateCountMenuOpen(false)
                void confirmWorldGenerateFromDetail()
              }}
              type="button"
            >
              {busy === `generate-${worldImageViewer.versionId}`
                ? <LoaderCircle className="spin" size={15} />
                : <Sparkles size={15} />}
              <span>
                {worldImageViewer.assetUrl ? '重新生成' : '开始生成参考图'}
                {` · ${worldGenerationCounts[worldImageViewer.versionId] ?? 1} 张`}
              </span>
            </button>
            <span aria-hidden="true" className="world-asset-split-action__divider" />
            <button
              aria-expanded={worldGenerateCountMenuOpen}
              aria-haspopup="menu"
              aria-label="选择本轮生成张数"
              className="world-asset-split-action__menu"
              disabled={busy !== null}
              onClick={() => setWorldGenerateCountMenuOpen((current) => !current)}
              type="button"
            >
              <ChevronDown size={16} />
            </button>
            {worldGenerateCountMenuOpen ? (
              <div
                className="world-asset-split-action__popover"
                role="menu"
              >
                {[1, 2, 3].map((count) => {
                  const selected = (worldGenerationCounts[worldImageViewer.versionId] ?? 1) === count
                  return (
                    <button
                      aria-checked={selected}
                      className="world-asset-split-action__option"
                      data-selected={selected}
                      key={count}
                      onClick={() => {
                        setWorldGenerationCounts((current) => ({
                          ...current,
                          [worldImageViewer.versionId]: count,
                        }))
                        setWorldGenerateCountMenuOpen(false)
                      }}
                      role="menuitemradio"
                      type="button"
                    >
                      <span>{count} 张</span>
                      {selected ? <Check size={14} /> : null}
                    </button>
                  )
                })}
              </div>
            ) : null}
          </div>
        ) : null}
        {worldImageViewer?.assetId && !worldImageViewer.locked ? (
          <Button
            disabled={busy !== null}
            onClick={() => void lockWorldReference(
              worldImageViewer.assetType,
              worldImageViewer.versionId,
              worldImageViewer.assetId!,
              worldImageViewer.assetName,
            )}
            variant="secondary"
          >
            {busy === `lock-${worldImageViewer.versionId}` ? <LoaderCircle className="spin" size={15} /> : <LockKeyhole size={15} />}
            锁定此参考图
          </Button>
        ) : null}
      </>}
      onClose={closeWorldImageViewer}
      open={worldImageViewer !== null}
      title={worldImageViewer
        ? (worldImageViewer.assetUrl
          ? `${worldImageViewer.styleLabel ?? '参考图'}全览`
          : `生成${worldImageViewer.typeLabel}参考图`)
        : '参考图详情'}
    >
      {worldImageViewer ? (
        <div
          className="world-asset-detail"
          onWheel={(event) => event.stopPropagation()}
        >
          <div className="world-asset-detail__media">
            {worldImageViewer.assetUrl ? (
              <>
                <div className="identity-image-viewer__toolbar">
                  <span><Maximize2 size={15} /><strong>{worldImageZoom === 100 ? '适应画面' : `${worldImageZoom}%`}</strong></span>
                  <div>
                    <Button
                      aria-label="缩小参考图"
                      disabled={worldImageZoom <= 100}
                      onClick={() => setWorldImageZoom((current) => Math.max(100, current - 25))}
                      size="sm"
                      variant="secondary"
                    >
                      <ZoomOut size={15} />
                    </Button>
                    <Button
                      disabled={worldImageZoom === 100}
                      onClick={() => setWorldImageZoom(100)}
                      size="sm"
                      variant="secondary"
                    >
                      复位
                    </Button>
                    <Button
                      aria-label="放大参考图"
                      disabled={worldImageZoom >= 200}
                      onClick={() => setWorldImageZoom((current) => Math.min(200, current + 25))}
                      size="sm"
                      variant="secondary"
                    >
                      <ZoomIn size={15} />
                    </Button>
                  </div>
                </div>
                <div
                  className="identity-image-viewer__viewport world-asset-detail__viewport"
                  onWheel={(event) => event.stopPropagation()}
                >
                  <div
                    className="identity-image-viewer__canvas"
                    style={{ height: `${worldImageZoom}%`, width: `${worldImageZoom}%` }}
                  >
                    <img
                      alt={`${worldImageViewer.assetName} ${worldImageViewer.styleLabel ?? ''}完整参考图`}
                      draggable={false}
                      src={worldImageViewer.assetUrl}
                    />
                  </div>
                </div>
              </>
            ) : (
              <div
                className="world-asset-detail__placeholder"
                role="status"
                style={{
                  aspectRatio: (worldAspectRatios[worldImageViewer.versionId] ?? DEFAULT_WORLD_ASPECT_RATIO)
                    .replace(':', ' / '),
                }}
              >
                <span className="world-asset-detail__placeholder-glow" aria-hidden="true" />
                <span className="world-asset-detail__placeholder-frame" aria-hidden="true" />
                <div className="world-asset-detail__placeholder-content">
                  <span className="world-asset-detail__placeholder-icon">
                    <Sparkles size={22} strokeWidth={1.5} />
                  </span>
                  <strong>尚未生成参考图</strong>
                  <small>确认右侧角色关联与提示词后开始生成</small>
                </div>
              </div>
            )}
          </div>
          <aside className="world-asset-detail__sidebar">
            <div className="world-asset-card__character-refs world-asset-card__character-refs--detail">
              <div className="world-asset-card__character-refs-heading">
                <span>关联角色形象</span>
                <span className="world-asset-card__character-refs-hint">
                  <button
                    aria-describedby={`world-character-ref-hint-${worldImageViewer.versionId}`}
                    aria-label="查看关联角色形象说明"
                    type="button"
                  >
                    <AlertCircle aria-hidden="true" size={15} />
                  </button>
                  <span id={`world-character-ref-hint-${worldImageViewer.versionId}`} role="tooltip">
                    {(worldCharacterRefs[worldImageViewer.versionId] ?? []).length > 0
                      ? `已关联 ${(worldCharacterRefs[worldImageViewer.versionId] ?? []).length} 个角色形象，生成时作为外貌参考。`
                      : '可选。用于旧照片、合影、有人出镜的场景等。'}
                  </span>
                </span>
              </div>
              <div className="world-asset-card__character-chips">
                {workspace.characters.map((character) => {
                  const locked = Boolean(
                    character.lockedCandidateId || character.lockedIdentityVersionId,
                  )
                  const lockedCandidate = character.lockedCandidateId
                    ? character.candidates.find((candidate) => candidate.id === character.lockedCandidateId)
                    : character.candidates.find((candidate) => candidate.selected)
                      ?? character.candidates[0]
                  const imageUrl = lockedCandidate?.assetUrl
                  const selectedIds = worldCharacterRefs[worldImageViewer.versionId] ?? []
                  const selected = selectedIds.includes(character.id)
                  return <button
                    aria-label={locked ? `关联${character.name}形象` : `${character.name}尚未锁定形象`}
                    aria-pressed={selected}
                    className="world-asset-card__character-chip"
                    data-selected={selected}
                    disabled={!locked || busy !== null}
                    key={character.id}
                    onClick={() => toggleWorldCharacterRef(
                      worldImageViewer.assetType,
                      worldImageViewer.versionId,
                      character.id,
                    )}
                    title={locked ? undefined : '请先锁定角色形象'}
                    type="button"
                  >
                    {imageUrl ? (
                      <img alt="" src={imageUrl} />
                    ) : (
                      <span className="world-asset-card__character-chip-fallback"><UsersRound size={12} /></span>
                    )}
                    <em>{character.name}</em>
                    {selected ? <Check size={12} /> : null}
                  </button>
                })}
              </div>
            </div>
            <label className="world-asset-detail__aspect">
              <span>生图比例</span>
              <SelectControl
                aria-label={`${worldImageViewer.assetName}生图比例`}
                disabled={busy !== null}
                onChange={(event) => setWorldAspectRatios((current) => ({
                  ...current,
                  [worldImageViewer.versionId]: event.target.value as WorldAspectRatio,
                }))}
                value={worldAspectRatios[worldImageViewer.versionId] ?? DEFAULT_WORLD_ASPECT_RATIO}
              >
                {WORLD_ASPECT_RATIOS.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.id} · {option.label}
                  </option>
                ))}
              </SelectControl>
            </label>
            <section className="candidate-prompt-editor world-asset-detail__prompt">
              <div className="candidate-prompt-editor__header">
                <strong>生图提示词</strong>
                <div className="candidate-prompt-editor__actions">
                  <Button
                    disabled={busy !== null || worldPromptLoading[detailPromptKey]}
                    onClick={() => void cycleWorldPromptVariant()}
                    size="sm"
                    variant="secondary"
                  >
                    换一个
                  </Button>
                  <Button
                    disabled={busy !== null || worldPromptLoading[detailPromptKey]}
                    onClick={() => void loadWorldPromptPreview(
                      worldImageViewer.assetType,
                      worldImageViewer.versionId,
                      { force: true, promptKey: detailPromptKey },
                    )}
                    size="sm"
                    variant="secondary"
                  >
                    {worldPromptLoading[detailPromptKey] ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}
                    重置为默认
                  </Button>
                </div>
              </div>
              {worldPromptLoading[detailPromptKey] && !worldPromptDraft[detailPromptKey] ? (
                <small>正在加载默认提示词…</small>
              ) : (
                <>
                  <label>
                    <textarea
                      aria-label={`${worldImageViewer.assetName}参考图提示词`}
                      disabled={busy !== null || worldPromptLoading[detailPromptKey]}
                      maxLength={4000}
                      onChange={(event) => {
                        const value = event.target.value
                        setWorldPromptDraft((current) => ({ ...current, [detailPromptKey]: value }))
                        setWorldPromptDirty((current) => ({
                          ...current,
                          [detailPromptKey]: value.trim() !== (worldPromptDefault[detailPromptKey] ?? '').trim(),
                        }))
                      }}
                      rows={8}
                      value={worldPromptDraft[detailPromptKey] ?? ''}
                    />
                  </label>
                  <div className="world-asset-card__prompt-actions">
                    <small>
                      {(worldPromptDraft[detailPromptKey] ?? '').length} / 4000
                      {worldPromptStale[detailPromptKey] ? ' · 角色已变，可重置提示词' : ''}
                      {worldImageViewer.generationPrompt ? ' · 当前为该图生成时使用的提示词' : ' · 将按所选张数生成，并自动追加不同风格说明。'}
                    </small>
                  </div>
                </>
              )}
            </section>
          </aside>
        </div>
      ) : null}
    </Modal>

    <ConfirmModal
      confirmLabel="删除"
      confirmVariant="danger"
      description="该参考图将被永久删除，不可恢复。"
      loading={pendingDeleteWorldAsset?.assetId
        ? busy === `delete-${pendingDeleteWorldAsset.assetId}`
        : false}
      onClose={() => {
        if (busy?.startsWith('delete-')) return
        setPendingDeleteWorldAsset(null)
      }}
      onConfirm={() => void confirmDeleteWorldReference()}
      open={pendingDeleteWorldAsset !== null}
      title={`删除「${pendingDeleteWorldAsset?.assetName ?? ''}」参考图？`}
    >
      {pendingDeleteWorldAsset?.assetUrl ? (
        <div className="confirm-delete-candidate">
          <img
            alt={`${pendingDeleteWorldAsset.assetName}参考图`}
            src={pendingDeleteWorldAsset.assetUrl}
          />
          <p>
            {pendingDeleteWorldAsset.typeLabel}
            {' · '}
            {pendingDeleteWorldAsset.styleLabel ?? '参考图'}
          </p>
        </div>
      ) : null}
    </ConfirmModal>
  </div>
}
