import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Dna,
  Eye,
  GitCompare,
  History,
  LoaderCircle,
  LockKeyhole,
  Maximize2,
  Pencil,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  UserRound,
  UserRoundCheck,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router'
import {
  applyCharacterVisualChange,
  fetchCharacterVisuals,
  generateCharacterIdentityView,
  generateCharacterVisualCandidates,
  lockCharacterVisualIdentity,
  retryPersistedJob,
  restoreCharacterVisualIdentity,
  selectCharacterVisualCandidate,
  updateCharacterVisualProfile,
  type CharacterVisualRecord,
  type CharacterVisualWorkspace,
} from '../api/client'
import { Button, getStatusLabel, Modal, PageHeader, StatusBadge } from '../components/ui'
import { ServiceRequiredState } from '../components/ServiceRequiredState'
import { buildCharacterVisualSummary } from '../utils/characterVisualSummary'
import { localizeDisplayText } from '../utils/localizeDisplayText'

const STATUS_LABELS: Record<string, string> = {
  NOT_GENERATED: '尚未生成',
  GENERATING: '正在生成',
  CANDIDATES_READY: '已生成候选',
  PENDING_SELECTION: '待选择',
  PENDING_REVIEW: '待审核',
  REVIEW_REQUIRED: '待审核',
  LOCKED: '已锁定',
  TEXT_CHANGED: '文字设定已变化',
  RE_REVIEW_REQUIRED: '需要重新审核',
  GENERATION_FAILED: '生成失败',
}

const VIEW_LABELS: Record<string, string> = {
  FRONT: '正面',
  THREE_QUARTER: '45° 侧面',
  PROFILE: '侧面',
  FULL_BODY: '全身',
  EXPRESSIONS: '基础表情组',
}

const DOSSIER_VIEW_TYPES = [
  'FRONT',
  'THREE_QUARTER',
  'PROFILE',
  'FULL_BODY',
  'EXPRESSIONS',
] as const

const ACTIVE_DOSSIER_JOB_STATUSES = new Set(['PENDING', 'RETRY_WAIT', 'RUNNING'])
const FAILED_DOSSIER_JOB_STATUSES = new Set(['FAILED', 'CANCELLED'])

const FAMILY_SIMILARITY_META: Record<string, { label: string; strength: number }> = {
  LOW: { label: '低度相似', strength: 1 },
  MEDIUM: { label: '中度相似', strength: 2 },
  HIGH: { label: '高度相似', strength: 3 },
  VERY_HIGH: { label: '双胞胎级', strength: 4 },
}

const CANDIDATE_PLACEHOLDERS = [
  { label: '候选方向 1', description: '生成时动态抽取' },
  { label: '候选方向 2', description: '生成时动态抽取' },
  { label: '候选方向 3', description: '生成时动态抽取' },
] as const

function familyRelationshipLabel(character?: CharacterVisualRecord): string {
  const role = character?.role ?? ''
  if (/母亲|妈妈/.test(role)) return '母亲'
  if (/父亲|爸爸/.test(role)) return '父亲'
  if (/女儿/.test(role)) return '女儿'
  if (/儿子/.test(role)) return '儿子'
  if (/姐姐|姊姊/.test(role)) return '姐姐'
  if (/妹妹/.test(role)) return '妹妹'
  if (/哥哥/.test(role)) return '哥哥'
  if (/弟弟/.test(role)) return '弟弟'
  if (/祖母|奶奶|外婆/.test(role)) return '祖母'
  if (/祖父|爷爷|外公/.test(role)) return '祖父'
  return '亲属'
}

interface ProfileDraft {
  age: string
  genderExpression: string
  region: string
  era: string
  occupation: string
  socialClass: string
  storyIdentity: string
  faceShape: string
  facialFeatures: string
  browEyeShape: string
  noseShape: string
  mouthCorner: string
  skinTone: string
  hairstyle: string
  hairTexture: string
  bodyType: string
  identifyingFeatures: string
  lifeMarks: string
  expression: string
  gaze: string
  posture: string
  movement: string
  wardrobe: string
  materials: string
  colors: string
  shoesBags: string
  accessories: string
  forbiddenElements: string
  negativeConstraints: string
}

interface IdentityViewAction {
  characterId: string
  characterName: string
  identityVersionId: string
  viewType: string
  viewLabel: string
  assetUrl: string
  mode: 'adjust' | 'regenerate'
}

interface IdentityImageViewer {
  characterId: string
  characterName: string
  identityVersionId: string
  viewType: string
  viewLabel: string
  assetUrl: string
}

function DossierGenerationMark() {
  return <div aria-hidden="true" className="character-dossier-generation-mark">
    <UserRound size={32} strokeWidth={1.35} />
    <Sparkles size={17} strokeWidth={1.7} />
  </div>
}

function profileDraft(character: CharacterVisualRecord): ProfileDraft | null {
  const profile = character.profile
  if (!profile) return null
  const identity = profile.identityFields
  const appearance = profile.appearanceFields
  const personality = profile.personalityVisualization
  const styling = profile.stylingFields
  const listText = (value: string | string[] | undefined) => (
    Array.isArray(value) ? value.join('、') : value ?? ''
  )
  return {
    age: identity.age ?? '',
    genderExpression: identity.gender_expression ?? '',
    region: identity.region ?? '',
    era: identity.era ?? '',
    occupation: identity.occupation ?? '',
    socialClass: identity.social_class ?? '',
    storyIdentity: identity.story_identity ?? '',
    faceShape: appearance.face_shape ?? '',
    facialFeatures: appearance.facial_features ?? '',
    browEyeShape: appearance.brow_eye_shape ?? '',
    noseShape: appearance.nose_shape ?? '',
    mouthCorner: appearance.mouth_corner ?? '',
    skinTone: appearance.skin_tone ?? '',
    hairstyle: appearance.hairstyle ?? '',
    hairTexture: appearance.hair_texture ?? '',
    bodyType: appearance.body_type ?? '',
    identifyingFeatures: appearance.identifying_features ?? '',
    lifeMarks: appearance.life_marks ?? '',
    expression: personality.expression ?? '',
    gaze: personality.gaze ?? '',
    posture: personality.posture ?? '',
    movement: personality.movement ?? '',
    wardrobe: listText(styling.wardrobe),
    materials: listText(styling.materials),
    colors: listText(styling.colors),
    shoesBags: listText(styling.shoes_bags),
    accessories: listText(styling.accessories),
    forbiddenElements: listText(styling.forbidden_elements),
    negativeConstraints: profile.negativeConstraints.join('、'),
  }
}

function splitItems(value: string): string[] {
  return value.split(/[、,，；;]/).map((item) => item.trim()).filter(Boolean)
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? localizeDisplayText(status)
}

function compactDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return remainder ? `${minutes} 分 ${remainder} 秒` : `${minutes} 分钟`
}

function characterReferenceImage(character: CharacterVisualRecord): string | undefined {
  const activeIdentity = character.identities.find(
    (identity) => identity.id === character.lockedIdentityVersionId,
  ) ?? character.identities.at(-1)
  return activeIdentity?.sourceCandidateAssetUrl
    ?? character.candidates.find((candidate) => candidate.selected)?.assetUrl
    ?? character.candidates.at(-1)?.assetUrl
}

function characterScrollOffset(): number {
  if (!window.matchMedia('(max-width: 1180px)').matches) return 92
  const locatorHeight = document.querySelector<HTMLElement>('.character-locator')
    ?.getBoundingClientRect().height ?? 0
  const topbarHeight = window.matchMedia('(max-width: 620px)').matches ? 68 : 76
  return topbarHeight + locatorHeight + 12
}

export function CharactersPage() {
  const { projectId } = useParams()
  const navigate = useNavigate()
  const [workspace, setWorkspace] = useState<CharacterVisualWorkspace | null>(null)
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [compare, setCompare] = useState<Record<string, boolean>>({})
  const [refinements, setRefinements] = useState<Record<string, string>>({})
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState<ProfileDraft | null>(null)
  const [identityViewAction, setIdentityViewAction] = useState<IdentityViewAction | null>(null)
  const [identityViewNote, setIdentityViewNote] = useState('')
  const [identityImageViewer, setIdentityImageViewer] = useState<IdentityImageViewer | null>(null)
  const [identityImageZoom, setIdentityImageZoom] = useState(100)
  const [identityReviewSelection, setIdentityReviewSelection] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeCharacterId, setActiveCharacterId] = useState('')
  const characterIdKey = workspace?.characters.map((character) => character.id).join('|') ?? ''

  const refresh = useCallback(async () => {
    if (!projectId) return
    const next = await fetchCharacterVisuals(projectId)
    setWorkspace(next)
    setActiveCharacterId((current) => (
      next.characters.some((character) => character.id === current)
        ? current
        : next.characters[0]?.id ?? ''
    ))
    setSelected((current) => Object.fromEntries(next.characters.map((character) => [
      character.id,
      current[character.id]
        ?? character.identities.at(-1)?.sourceCandidateId
        ?? character.candidates.find((candidate) => candidate.selected)?.id
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
        if (active) setError(reason instanceof Error ? reason.message : '角色形象数据读取失败')
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

  useEffect(() => {
    if (loading || !characterIdKey) return
    const characterIds = characterIdKey.split('|')
    const elements = characterIds
      .map((characterId) => document.getElementById(`character-${characterId}`))
      .filter((element): element is HTMLElement => Boolean(element))
    if (!elements.length) return

    const hashAnchor = decodeURIComponent(window.location.hash.slice(1))
    const hashCharacterId = hashAnchor.startsWith('character-')
      ? hashAnchor.slice('character-'.length)
      : ''
    const initialCharacterId = characterIds.includes(hashCharacterId)
      ? hashCharacterId
      : characterIds[0]
    setActiveCharacterId(initialCharacterId)

    let updateFrame = 0
    const updateActiveCharacter = () => {
      window.cancelAnimationFrame(updateFrame)
      updateFrame = window.requestAnimationFrame(() => {
        const guideLine = characterScrollOffset()
        const current = elements.find((element) => {
          const bounds = element.getBoundingClientRect()
          return bounds.top <= guideLine && bounds.bottom > guideLine
        }) ?? elements.find((element) => element.getBoundingClientRect().top > guideLine)
          ?? elements.at(-1)
        if (current?.dataset.characterId) setActiveCharacterId(current.dataset.characterId)
      })
    }

    const observer = new IntersectionObserver(updateActiveCharacter, {
      rootMargin: '-88px 0px -68% 0px',
      threshold: [0, 0.01],
    })
    const startTracking = () => {
      elements.forEach((element) => observer.observe(element))
      window.addEventListener('resize', updateActiveCharacter)
      window.addEventListener('scroll', updateActiveCharacter, { passive: true })
      updateActiveCharacter()
    }
    const positionHashTarget = () => {
      if (!hashCharacterId) return
      const target = document.getElementById(`character-${hashCharacterId}`)
      if (!target) return
      window.scrollTo({
        top: Math.max(
          0,
          target.getBoundingClientRect().top + window.scrollY - characterScrollOffset(),
        ),
      })
      setActiveCharacterId(hashCharacterId)
    }
    const initialTimer = window.setTimeout(() => {
      positionHashTarget()
      startTracking()
    }, hashCharacterId ? 120 : 0)
    const hashRetryTimer = hashCharacterId
      ? window.setTimeout(() => {
          positionHashTarget()
          updateActiveCharacter()
        }, 600)
      : 0

    return () => {
      observer.disconnect()
      window.removeEventListener('resize', updateActiveCharacter)
      window.removeEventListener('scroll', updateActiveCharacter)
      window.clearTimeout(initialTimer)
      window.clearTimeout(hashRetryTimer)
      window.cancelAnimationFrame(updateFrame)
    }
  }, [characterIdKey, loading])

  async function run(key: string, action: () => Promise<unknown>) {
    setBusy(key)
    setError(null)
    try {
      await action()
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败，请稍后重试')
    } finally {
      setBusy(null)
    }
  }

  function openIdentityViewAction(action: IdentityViewAction) {
    setIdentityViewAction(action)
    setIdentityViewNote('')
  }

  function openIdentityImageViewer(viewer: IdentityImageViewer) {
    setIdentityImageViewer(viewer)
    setIdentityImageZoom(100)
  }

  function closeIdentityImageViewer() {
    setIdentityImageViewer(null)
    setIdentityImageZoom(100)
  }

  function regenerateIdentityImageViewer() {
    if (!identityImageViewer) return
    const viewer = identityImageViewer
    closeIdentityImageViewer()
    window.setTimeout(() => {
      openIdentityViewAction({
        characterId: viewer.characterId,
        characterName: viewer.characterName,
        identityVersionId: viewer.identityVersionId,
        viewType: viewer.viewType,
        viewLabel: viewer.viewLabel,
        assetUrl: viewer.assetUrl,
        mode: 'regenerate',
      })
    }, 0)
  }

  function jumpToCharacter(characterId: string, actionSelector?: string) {
    const characterCard = document.getElementById(`character-${characterId}`)
    if (!characterCard) return
    const target = actionSelector
      ? characterCard.querySelector<HTMLElement>(actionSelector) ?? characterCard
      : characterCard
    setActiveCharacterId(characterId)
    characterCard.focus({ preventScroll: true })
    const nextUrl = new URL(window.location.href)
    nextUrl.hash = `character-${characterId}`
    window.history.replaceState(window.history.state, '', nextUrl)
    window.scrollTo({
      behavior: 'smooth',
      top: Math.max(
        0,
        target.getBoundingClientRect().top + window.scrollY - characterScrollOffset(),
      ),
    })
  }

  async function submitIdentityViewAction() {
    if (!projectId || !workspace || !identityViewAction) return
    const character = workspace.characters.find(
      (item) => item.id === identityViewAction.characterId,
    )
    if (!character) return
    const note = identityViewAction.mode === 'adjust' ? identityViewNote.trim() : undefined
    await run(`identity-view-${identityViewAction.viewType}`, async () => {
      await generateCharacterIdentityView(
        projectId,
        character.id,
        character.lockVersion,
        identityViewAction.identityVersionId,
        identityViewAction.viewType,
        note,
      )
      setIdentityViewAction(null)
      setIdentityViewNote('')
    })
  }

  function edit(character: CharacterVisualRecord) {
    setEditingId(character.id)
    setDraft(profileDraft(character))
  }

  async function saveProfile(character: CharacterVisualRecord) {
    if (!projectId || !draft) return
    await run(`save-${character.id}`, async () => {
      await updateCharacterVisualProfile(projectId, character.id, character.lockVersion, {
        identity_fields: {
          age: draft.age,
          gender_expression: draft.genderExpression,
          region: draft.region,
          era: draft.era,
          occupation: draft.occupation,
          social_class: draft.socialClass,
          story_identity: draft.storyIdentity,
        },
        appearance_fields: {
          face_shape: draft.faceShape,
          facial_features: draft.facialFeatures,
          brow_eye_shape: draft.browEyeShape,
          nose_shape: draft.noseShape,
          mouth_corner: draft.mouthCorner,
          skin_tone: draft.skinTone,
          hairstyle: draft.hairstyle,
          hair_texture: draft.hairTexture,
          body_type: draft.bodyType,
          identifying_features: draft.identifyingFeatures,
          life_marks: draft.lifeMarks,
        },
        personality_visualization: {
          expression: draft.expression,
          gaze: draft.gaze,
          posture: draft.posture,
          movement: draft.movement,
        },
        styling_fields: {
          wardrobe: draft.wardrobe,
          materials: draft.materials,
          colors: draft.colors,
          shoes_bags: draft.shoesBags,
          accessories: draft.accessories,
          forbidden_elements: splitItems(draft.forbiddenElements),
        },
        negative_constraints: splitItems(draft.negativeConstraints),
      })
      setEditingId(null)
      setDraft(null)
    })
  }

  const allLocked = Boolean(
    workspace?.characters.length
    && workspace.characters.every((character) => character.status === 'LOCKED'),
  )

  if (!loading && (!projectId || !workspace)) {
    return <ServiceRequiredState feature="角色形象生成与锁定" projectId={projectId} />
  }

  if (loading || !projectId || !workspace) {
    return <div className="page brief-page-state"><LoaderCircle className="spin" size={22} /><strong>正在准备角色结构化视觉档案…</strong></div>
  }

  const activeCharacterIndex = Math.max(
    0,
    workspace.characters.findIndex((character) => character.id === activeCharacterId),
  )

  return <div className="page page--characters page--character-visuals">
    <PageHeader
          eyebrow="角色身份 · 基准管理"
      title="生成并锁定角色身份"
      description="检查角色设定后生成形象，选择满意版本并锁定。系统不会自动生图或采用候选，锁定后才会用于后续剧本与分镜。"
      actions={<><Link className="button button--secondary button--md" to={`/projects/${projectId}/story`}><ArrowLeft size={16} />返回角色文字设定</Link><Button onClick={() => void refresh()} variant="secondary"><RefreshCw size={16} />刷新</Button></>}
    />

    {error ? <div className="brief-save-message brief-save-message--error" role="alert"><AlertTriangle size={16} />{error}</div> : null}

    <div className="character-page-body">
      <aside aria-label="快速定位角色" className="character-locator">
        <header><strong>角色定位</strong><small>{activeCharacterIndex + 1}/{workspace.characters.length}</small></header>
        <ol>{workspace.characters.map((character) => {
          const active = character.id === activeCharacterId
          const referenceImage = characterReferenceImage(character)
          return <li key={character.id}><button aria-current={active ? 'location' : undefined} aria-label={`定位到${character.name}，当前状态：${statusLabel(character.status)}`} className={active ? 'is-active' : ''} onClick={() => jumpToCharacter(character.id)} type="button"><span className="character-locator__marker">{referenceImage ? <img alt="" src={referenceImage} /> : <span aria-hidden="true">{character.name.slice(0, 1)}</span>}<i className={`is-${character.status.toLowerCase()}`} /></span><span className="character-locator__copy"><strong>{character.name}</strong><small>{statusLabel(character.status)}</small></span></button></li>
        })}</ol>
      </aside>

      <div className="character-visual-list">{workspace.characters.map((character) => {
      const profile = character.profile
      const familyConstraint = character.familyResemblanceConstraint
      const familySimilarity = familyConstraint
        ? FAMILY_SIMILARITY_META[familyConstraint.similarityLevel]
          ?? { label: familyConstraint.similarityLevel, strength: 1 }
        : null
      const familySources = familyConstraint
        ? [...new Set(
          familyConstraint.inheritedFeatures.map((feature) => feature.sourceCharacterName),
        )]
        : []
      const familySourceReferences = familyConstraint
        ? [...new Map(
          familyConstraint.inheritedFeatures.map((feature) => {
            const sourceCharacter = workspace.characters.find(
              (item) => item.id === feature.sourceCharacterId,
            )
            return [
              feature.sourceCharacterId,
              `${familyRelationshipLabel(sourceCharacter)}${feature.sourceCharacterName}`,
            ]
          }),
        ).values()]
        : []
      const familyFeatureLabels = familyConstraint
        ? [...new Set(familyConstraint.inheritedFeatures.map((feature) => feature.label))]
        : []
      const relativeToLock = familyConstraint?.status === 'WAITING_FOR_LOCKED_RELATIVE'
        ? familyConstraint.relationshipEvidence
          .map((evidence) => workspace.characters.find(
            (item) => item.characterKey === evidence.relativeCharacterKey,
          ))
          .find((item) => item && item.status !== 'LOCKED')
        : undefined
      const selectedCandidateId = selected[character.id]
      const selectedCandidate = character.candidates.find((item) => item.id === selectedCandidateId)
      const latestPendingIdentity = [...character.identities].reverse().find(
        (identity) => ['GENERATING_DOSSIER', 'READY_FOR_REVIEW'].includes(identity.status),
      )
      const selectedReviewIdentity = character.identities.find(
        (identity) => (
          identity.id === identityReviewSelection[character.id]
          && ['GENERATING_DOSSIER', 'READY_FOR_REVIEW'].includes(identity.status)
        ),
      )
      const pendingIdentity = selectedReviewIdentity ?? latestPendingIdentity
      const viewingReplacedIdentity = Boolean(
        pendingIdentity && latestPendingIdentity && pendingIdentity.id !== latestPendingIdentity.id,
      )
      const pendingIdentityAssets = new Map(
        pendingIdentity?.assets.map((asset) => [asset.viewType, asset]) ?? [],
      )
      const pendingIdentityJobs = new Map(
        pendingIdentity?.viewJobs.map((job) => [job.viewType, job]) ?? [],
      )
      const latestIdentityJobs = [...pendingIdentityJobs.values()]
      const activeIdentityJobs = latestIdentityJobs.filter((job) => (
        ACTIVE_DOSSIER_JOB_STATUSES.has(job.status)
      ))
      const failedIdentityJobs = latestIdentityJobs.filter((job) => (
        FAILED_DOSSIER_JOB_STATUSES.has(job.status)
      ))
      const generatingDossier = activeIdentityJobs.length > 0
        || (pendingIdentity?.status === 'GENERATING_DOSSIER' && !pendingIdentity?.viewJobs.length)
      const selectedHasIdentity = character.identities.some(
        (identity) => identity.sourceCandidateId === selectedCandidateId,
      )
      const isEditing = editingId === character.id && draft
      const blockers = profile?.conflictReport.filter((item) => item.severity === 'BLOCKER') ?? []
      const generatingCandidates = character.status === 'GENERATING'
        || character.batches.at(-1)?.status === 'GENERATING'
        || busy === `generate-${character.id}`
      const generating = generatingCandidates
        || generatingDossier
      const refinementNote = refinements[character.id] ?? ''
      const candidateBatchVersions = new Map(character.batches.map((batch) => [batch.id, batch.version]))
      const candidatesByRecency = [...character.candidates].sort((left, right) => {
        const batchDifference = (candidateBatchVersions.get(left.batchId ?? '') ?? 0)
          - (candidateBatchVersions.get(right.batchId ?? '') ?? 0)
        return batchDifference || left.ordinal - right.ordinal
      })
      const latestCandidateIds = new Set(candidatesByRecency.slice(-3).map((candidate) => candidate.id))
      const latestCandidates = character.candidates.filter((candidate) => latestCandidateIds.has(candidate.id))
      const historicalCandidates = character.candidates.filter((candidate) => !latestCandidateIds.has(candidate.id))
      const profileSummary = profile ? buildCharacterVisualSummary({
        age: profile.identityFields.age ?? '',
        occupation: profile.identityFields.occupation ?? '',
        identifyingFeatures: profile.appearanceFields.identifying_features ?? '',
        gaze: profile.personalityVisualization.gaze ?? '',
        wardrobe: String(profile.stylingFields.wardrobe ?? ''),
        fallbackSummary: profile.summary,
      }) : ''
      return <article className="character-visual-card" data-character-id={character.id} id={`character-${character.id}`} key={character.id} tabIndex={-1}>
        <header>
          <div><p className="eyebrow">{localizeDisplayText(character.role)}</p><h2>{character.name}</h2></div>
          <span className={`character-visual-status is-${character.status.toLowerCase()}`}>{statusLabel(character.status)}</span>
        </header>

        {profile ? <section className="character-profile-summary">
          <div className="character-profile-summary__heading"><div><strong>角色设定摘要</strong><span>第 {profile.version} 版</span></div><Button disabled={generating || busy !== null} onClick={() => edit(character)} size="sm" variant="secondary"><Pencil size={14} />调整设定</Button></div>
          <p className="character-profile-summary__line">{profileSummary}</p>
          <details className="character-profile-details" open={blockers.length > 0}>
            <summary>查看完整设定与一致性检查</summary>
            <dl><div><dt>身份</dt><dd>{profile.identityFields.region} · {profile.identityFields.era} · {profile.identityFields.social_class}</dd></div><div><dt>外貌</dt><dd>{profile.appearanceFields.face_shape}；{profile.appearanceFields.identifying_features}</dd></div><div><dt>可见性格</dt><dd>{profile.personalityVisualization.expression}；{profile.personalityVisualization.gaze}</dd></div><div><dt>造型</dt><dd>{String(profile.stylingFields.wardrobe)}；{String(profile.stylingFields.colors)}</dd></div></dl>
            <div className="character-profile-audit"><strong><ShieldCheck size={15} />一致性检查</strong><ul>{profile.conflictReport.map((issue) => <li data-severity={issue.severity.toLowerCase()} key={issue.code}><span>{issue.message}</span><small>{issue.suggestion}</small></li>)}</ul></div>
          </details>
        </section> : null}

        {familyConstraint && familySimilarity ? <section className="character-family-constraint" data-status={familyConstraint.status.toLowerCase()}>
          <header>
            <div className="character-family-constraint__title">
              <span><Dna size={18} /></span>
              <strong>家族相似性</strong>
            </div>
            {familyConstraint.status === 'WAITING_FOR_LOCKED_RELATIVE' ? <em><i />等待亲属基准</em> : null}
          </header>
          {familyConstraint.status === 'ACTIVE' ? <>
            <p className="character-family-conclusion">
              生成{character.name}时，将参考{familySourceReferences.join('、') || '已锁定亲属'}的{familyFeatureLabels.join('和')}，保持{familySimilarity.label}，但不会复制五官或覆盖已锁定身份。
            </p>
            <dl className="character-family-facts">
              <div><dt>来源</dt><dd>{familySources.join('、') || '待确认'}</dd></div>
              <div><dt>继承特征</dt><dd>{familyFeatureLabels.join('、') || '待确认'}</dd></div>
            </dl>
            <div className="character-family-actions">
              <Link className="button button--secondary button--sm" to={`/projects/${projectId}/story#relationship-review`}>
                <SlidersHorizontal size={14} />调整相似度
              </Link>
              <details className="character-family-rules">
                <summary className="button button--ghost button--sm"><ShieldCheck size={14} />查看规则</summary>
                <div>
                  <p><strong>系统详情</strong><span>约束版本 V{familyConstraint.version} · {familySimilarity.label}</span></p>
                  <ul className="character-family-rule-traits">
                    {familyConstraint.inheritedFeatures.map((feature) => <li key={`${feature.field}-${feature.sourceIdentityVersionId}`}><strong>{feature.label}</strong><span>{feature.value}</span><small>来源：{feature.sourceCharacterName}</small></li>)}
                  </ul>
                  <strong>生成边界</strong>
                  <ul>{familyConstraint.independenceConstraints.map((item) => <li key={item}>{item}</li>)}</ul>
                  {character.status === 'LOCKED' ? <small>当前角色已锁定，调整只影响未来生成。</small> : null}
                </div>
              </details>
            </div>
          </> : <div className="character-family-waiting">
            <LockKeyhole size={18} />
            <div className="character-family-waiting__copy">
              <strong>先锁定一位亲属，系统才会提取稳定家族特征，不会自动生图或覆盖已有内容。</strong>
            </div>
            {relativeToLock ? <Button
              aria-label={`去完成${relativeToLock.name}的角色基准锁定`}
              className="character-family-waiting__action"
              onClick={() => jumpToCharacter(
                relativeToLock.id,
                '[data-character-lock-action="true"], .character-identity-dossier, .character-candidate-section',
              )}
              size="sm"
              variant="secondary"
            >
              <UserRoundCheck size={14} />去锁定{relativeToLock.name}
            </Button> : null}
          </div>}
        </section> : null}

        {isEditing ? <section className="character-visual-editor">
          <header><div><span>生成前可调整</span><h3>外貌、造型与可见性格</h3></div><small>保存后会创建新版本并重新运行一致性审核</small></header>
          <div className="character-visual-editor__grid">
            <label>年龄<input onChange={(event) => setDraft({ ...draft, age: event.target.value })} value={draft.age} /></label>
            <label>性别表达<input onChange={(event) => setDraft({ ...draft, genderExpression: event.target.value })} value={draft.genderExpression} /></label>
            <label>地域<input onChange={(event) => setDraft({ ...draft, region: event.target.value })} value={draft.region} /></label>
            <label>时代<input onChange={(event) => setDraft({ ...draft, era: event.target.value })} value={draft.era} /></label>
            <label>职业<input onChange={(event) => setDraft({ ...draft, occupation: event.target.value })} value={draft.occupation} /></label>
            <label>阶层<input onChange={(event) => setDraft({ ...draft, socialClass: event.target.value })} value={draft.socialClass} /></label>
            <label className="is-wide">剧中身份<textarea onChange={(event) => setDraft({ ...draft, storyIdentity: event.target.value })} rows={2} value={draft.storyIdentity} /></label>
            <label>脸型<input onChange={(event) => setDraft({ ...draft, faceShape: event.target.value })} value={draft.faceShape} /></label>
            <label>五官<input onChange={(event) => setDraft({ ...draft, facialFeatures: event.target.value })} value={draft.facialFeatures} /></label>
            <label>眉眼<input onChange={(event) => setDraft({ ...draft, browEyeShape: event.target.value })} value={draft.browEyeShape} /></label>
            <label>鼻型<input onChange={(event) => setDraft({ ...draft, noseShape: event.target.value })} value={draft.noseShape} /></label>
            <label>嘴角<input onChange={(event) => setDraft({ ...draft, mouthCorner: event.target.value })} value={draft.mouthCorner} /></label>
            <label>肤色<input onChange={(event) => setDraft({ ...draft, skinTone: event.target.value })} value={draft.skinTone} /></label>
            <label>发型<input onChange={(event) => setDraft({ ...draft, hairstyle: event.target.value })} value={draft.hairstyle} /></label>
            <label>发质<input onChange={(event) => setDraft({ ...draft, hairTexture: event.target.value })} value={draft.hairTexture} /></label>
            <label>体型<input onChange={(event) => setDraft({ ...draft, bodyType: event.target.value })} value={draft.bodyType} /></label>
            <label>识别特征<input onChange={(event) => setDraft({ ...draft, identifyingFeatures: event.target.value })} value={draft.identifyingFeatures} /></label>
            <label className="is-wide">生活痕迹<input onChange={(event) => setDraft({ ...draft, lifeMarks: event.target.value })} value={draft.lifeMarks} /></label>
            <label>表情<input onChange={(event) => setDraft({ ...draft, expression: event.target.value })} value={draft.expression} /></label>
            <label>眼神<input onChange={(event) => setDraft({ ...draft, gaze: event.target.value })} value={draft.gaze} /></label>
            <label>站姿<input onChange={(event) => setDraft({ ...draft, posture: event.target.value })} value={draft.posture} /></label>
            <label>动作<input onChange={(event) => setDraft({ ...draft, movement: event.target.value })} value={draft.movement} /></label>
            <label className="is-wide">服装<input onChange={(event) => setDraft({ ...draft, wardrobe: event.target.value })} value={draft.wardrobe} /></label>
            <label>材质<input onChange={(event) => setDraft({ ...draft, materials: event.target.value })} value={draft.materials} /></label>
            <label>色彩<input onChange={(event) => setDraft({ ...draft, colors: event.target.value })} value={draft.colors} /></label>
            <label>鞋包<input onChange={(event) => setDraft({ ...draft, shoesBags: event.target.value })} value={draft.shoesBags} /></label>
            <label>配饰<input onChange={(event) => setDraft({ ...draft, accessories: event.target.value })} value={draft.accessories} /></label>
            <label className="is-wide">禁止元素<input onChange={(event) => setDraft({ ...draft, forbiddenElements: event.target.value })} value={draft.forbiddenElements} /></label>
            <label className="is-wide">负面约束<textarea onChange={(event) => setDraft({ ...draft, negativeConstraints: event.target.value })} rows={2} value={draft.negativeConstraints} /></label>
          </div>
          <footer><Button onClick={() => { setEditingId(null); setDraft(null) }} variant="secondary">取消</Button><Button disabled={busy !== null} onClick={() => void saveProfile(character)}>{busy === `save-${character.id}` ? <LoaderCircle className="spin" size={15} /> : <ShieldCheck size={15} />}保存并重新审核</Button></footer>
        </section> : null}

        {profile && character.status !== 'LOCKED' ? <section className="character-candidate-section">
          <header><div><span>第一步 · 生成与比较</span><h3>每批随机抽取三个形象方向</h3></div><div>{character.candidates.length ? <Button onClick={() => setCompare((current) => ({ ...current, [character.id]: !current[character.id] }))} size="sm" variant="secondary"><GitCompare size={14} />{compare[character.id] ? '退出比较' : '并排比较'}</Button> : null}<Button disabled={blockers.length > 0 || generating || busy !== null} onClick={() => void run(`generate-${character.id}`, () => generateCharacterVisualCandidates(projectId, character.id, character.lockVersion, profile.id))}>{busy === `generate-${character.id}` || generating ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}{character.candidates.length ? '换一批 3 个' : '生成形象'}</Button></div></header>
          {blockers.length ? <div className="character-candidate-blocked"><AlertTriangle size={15} />请先调整 {blockers.length} 个阻断问题，再生成形象。</div> : null}
          {character.candidates.length ? <div className="character-candidate-collection">
            <div className={`character-candidate-grid ${compare[character.id] ? 'is-comparing' : ''}`}>{latestCandidates.map((candidate) => {
              const batchVersion = candidateBatchVersions.get(candidate.batchId ?? '')
              return <button aria-pressed={selectedCandidateId === candidate.id} className={`character-candidate ${selectedCandidateId === candidate.id ? 'character-candidate--selected' : ''}`} disabled={generating || candidate.profileVersionId !== profile.id} key={candidate.id} onClick={() => setSelected((current) => ({ ...current, [character.id]: candidate.id }))}><img alt={`${character.name} 形象候选 ${candidate.ordinal}`} src={candidate.assetUrl} /><span><strong>{candidate.variantLabel ?? `候选 ${candidate.ordinal}`}</strong><small>第 {batchVersion ?? 1} 批 · 候选 {candidate.ordinal}</small></span>{selectedCandidateId === candidate.id ? <em><Check size={15} />已选择</em> : null}</button>
            })}</div>
            {historicalCandidates.length ? <section className="character-candidate-history"><header><strong>历史候选</strong><small>{historicalCandidates.length} 张</small></header><div className="character-candidate-history__grid">{historicalCandidates.map((candidate) => {
              const batchVersion = candidateBatchVersions.get(candidate.batchId ?? '')
              return <button aria-pressed={selectedCandidateId === candidate.id} className={`character-candidate character-candidate--compact ${selectedCandidateId === candidate.id ? 'character-candidate--selected' : ''}`} disabled={generating || candidate.profileVersionId !== profile.id} key={candidate.id} onClick={() => setSelected((current) => ({ ...current, [character.id]: candidate.id }))}><img alt={`${character.name} 历史形象候选 ${candidate.ordinal}`} src={candidate.assetUrl} /><span><strong>{candidate.variantLabel ?? `候选 ${candidate.ordinal}`}</strong><small>第 {batchVersion ?? 1} 批 · 候选 {candidate.ordinal}</small></span>{selectedCandidateId === candidate.id ? <em><Check size={13} />已选</em> : null}</button>
            })}</div></section> : null}
          </div> : <div aria-busy={generatingCandidates} aria-live="polite" className={`character-candidate-empty ${generatingCandidates ? 'is-generating' : ''}`}>
            <div aria-label="待生成的三个形象方向" className="character-candidate-placeholder-grid">
              {CANDIDATE_PLACEHOLDERS.map((direction) => <article className="character-candidate-placeholder" key={direction.label}>
                <div aria-hidden="true" className="character-candidate-placeholder__portrait">
                  <div className="character-candidate-placeholder__figure"><UserRound size={48} strokeWidth={1.25} />{generatingCandidates ? <Sparkles size={18} /> : null}</div>
                  <span>{generatingCandidates ? '正在生成' : '待生成'}</span>
                </div>
                <div><strong>{direction.label}</strong><small>{direction.description}</small></div>
              </article>)}
            </div>
            {generatingCandidates ? <p><span aria-hidden="true" className="character-generation-dots"><i /><i /><i /></span><span>正在塑造三个形象方向，完成后会自动显示。</span></p> : null}
          </div>}
          {selectedCandidate && !generating ? <div className="character-candidate-refinement"><textarea aria-label={`${character.name}候选微调说明`} onChange={(event) => setRefinements((current) => ({ ...current, [character.id]: event.target.value }))} placeholder="例如：保留五官，只让眼神更警觉，发型更利落" rows={2} value={refinementNote} /><Button disabled={!refinementNote.trim() || busy !== null} onClick={() => void run(`refine-${character.id}`, () => generateCharacterVisualCandidates(projectId, character.id, character.lockVersion, profile.id, { sourceCandidateId: selectedCandidate.id, note: refinementNote.trim() }))} variant="secondary">{busy === `refine-${character.id}` ? <LoaderCircle className="spin" size={15} /> : <SlidersHorizontal size={15} />}生成微调版本</Button></div> : null}
          {selectedCandidate && !selectedHasIdentity && !generating ? <footer><div><strong>已选“{selectedCandidate.variantLabel ?? `候选 ${selectedCandidate.ordinal}`}”</strong><small>下一步会生成多角度检查图，确认五官、年龄感、体型和识别特征稳定。</small></div><Button disabled={busy !== null} onClick={() => void run(`select-${character.id}`, () => selectCharacterVisualCandidate(projectId, character.id, character.lockVersion, selectedCandidate.id))}><UserRoundCheck size={15} />生成基准检查图</Button></footer> : null}
        </section> : null}

        {pendingIdentity ? <section className="character-identity-dossier" data-identity-version-id={pendingIdentity.id}>
          <header><div><span>{viewingReplacedIdentity ? '历史版本 · 仅供查看' : '第二步 · 基准检查'}</span><h3>角色身份 · 第 {pendingIdentity.version} 版</h3></div><StatusBadge description={viewingReplacedIdentity ? `当前以第 ${latestPendingIdentity?.version ?? pendingIdentity.version} 版为审核目标` : failedIdentityJobs.length ? `${failedIdentityJobs.length} 个角色身份视角生成失败，可以单独重新生成` : generatingDossier ? '正在生成多角度角色身份基准检查图' : '角色身份基准检查图已生成，等待确认'} label={viewingReplacedIdentity ? '已被新版本替代' : failedIdentityJobs.length ? `${failedIdentityJobs.length} 个视角生成失败` : undefined} status={viewingReplacedIdentity ? 'SUPERSEDED' : failedIdentityJobs.length ? 'GENERATION_FAILED' : pendingIdentity.status} /></header>
          <div aria-busy={generatingDossier} aria-live="polite">
            {DOSSIER_VIEW_TYPES.map((viewType) => {
              const asset = pendingIdentityAssets.get(viewType)
              const viewJob = pendingIdentityJobs.get(viewType)
              const label = VIEW_LABELS[viewType]
              const failed = Boolean(viewJob && FAILED_DOSSIER_JOB_STATUSES.has(viewJob.status))
              const active = Boolean(viewJob && ACTIVE_DOSSIER_JOB_STATUSES.has(viewJob.status))
              const retrying = viewJob ? busy === `retry-dossier-${viewJob.id}` : false
              return asset
                ? <figure aria-busy={active} className={`character-dossier-asset ${active ? 'is-updating' : ''} ${failed ? 'has-error' : ''}`} key={viewType}>
                    <div className="character-dossier-asset__media">
                      <button aria-label={`查看${character.name}${label}的完整原图`} className="character-dossier-asset__open" onClick={() => openIdentityImageViewer({ characterId: character.id, characterName: character.name, identityVersionId: pendingIdentity.id, viewType, viewLabel: label, assetUrl: asset.assetUrl })} type="button">
                        <img alt={`${character.name} ${label}`} src={asset.assetUrl} />
                        <span aria-hidden="true"><Maximize2 size={14} /></span>
                      </button>
                      {active ? <div className="character-dossier-regeneration" role="status">
                        <DossierGenerationMark />
                        <strong>正在生成新版本</strong>
                        <small>旧图会保留到新版本完成</small>
                      </div> : failed ? <span className="character-dossier-asset__status is-error"><AlertTriangle size={14} />上次生成失败</span> : null}
                      {!viewingReplacedIdentity ? <>
                        <Button aria-label={`调整${character.name}${label}的细节`} className="character-dossier-asset__action character-dossier-asset__action--adjust" disabled={busy !== null || active} onClick={() => openIdentityViewAction({ characterId: character.id, characterName: character.name, identityVersionId: pendingIdentity.id, viewType, viewLabel: label, assetUrl: asset.assetUrl, mode: 'adjust' })} size="sm" variant="secondary">调整细节</Button>
                        <Button aria-label={`重新生成${character.name}${label}`} className="character-dossier-asset__action character-dossier-asset__action--regenerate" disabled={busy !== null || active} onClick={() => openIdentityViewAction({ characterId: character.id, characterName: character.name, identityVersionId: pendingIdentity.id, viewType, viewLabel: label, assetUrl: asset.assetUrl, mode: 'regenerate' })} size="sm" variant="secondary">重新生成</Button>
                      </> : null}
                    </div>
                    <figcaption>{label}</figcaption>
                  </figure>
                : <figure aria-busy={active} aria-label={`${label}图片${failed ? '生成失败' : active ? '正在生成' : '等待生成'}`} className={`character-dossier-placeholder ${active ? 'is-generating' : ''} ${failed ? 'is-failed' : ''}`} key={viewType}>
                    <div className={`character-dossier-placeholder__visual ${active ? 'is-generating' : ''}`}>
                      {failed ? <AlertTriangle aria-hidden="true" size={32} strokeWidth={1.4} /> : active ? <DossierGenerationMark /> : <UserRound aria-hidden="true" size={36} strokeWidth={1.25} />}
                      <strong>{failed ? '生成失败' : viewJob?.status === 'RETRY_WAIT' ? '等待重试' : active ? '正在生成' : '等待生成'}</strong>
                      {active && viewJob ? <small>单次最长等待 {compactDuration(viewJob.maxWaitSeconds)}</small> : null}
                      {failed && viewJob?.retryable ? <Button disabled={busy !== null} onClick={() => void run(`retry-dossier-${viewJob.id}`, () => retryPersistedJob(viewJob.id))} size="sm" variant="secondary">{retrying ? <LoaderCircle className="spin" size={13} /> : <RefreshCw size={13} />}重新生成</Button> : null}
                    </div>
                    <figcaption>{label}</figcaption>
                  </figure>
            })}
            {failedIdentityJobs.length ? <div className="character-dossier-error-summary"><AlertTriangle size={18} />{failedIdentityJobs.length} 个视角未完成，可在对应卡片重新生成。</div> : null}
          </div>
        </section> : null}

        {character.identities.length ? <section className="character-version-history">
          <header><div><History size={17} /><span><strong>角色基准版本</strong><small>每次设为基准都会保留完整版本，可恢复但不会覆盖既有镜头。</small></span></div><em>{character.identities.length} 个版本</em></header>
          <div>{[...character.identities].reverse().map((identity) => {
            const isActive = identity.id === character.lockedIdentityVersionId
            const reviewable = identity.id === latestPendingIdentity?.id
            const replacedPending = !isActive
              && !reviewable
              && !identity.lockedAt
              && ['GENERATING_DOSSIER', 'READY_FOR_REVIEW'].includes(identity.status)
            const viewingThisVersion = identity.id === pendingIdentity?.id
            const readyToLock = reviewable
              && viewingThisVersion
              && identity.status === 'READY_FOR_REVIEW'
            const restorable = !isActive && Boolean(identity.lockedAt)
              && ['LOCKED', 'SUPERSEDED'].includes(identity.status)
            return <article className={`${isActive ? 'is-active' : ''} ${viewingThisVersion ? 'is-viewing' : ''}`.trim()} key={identity.id}>
              {identity.sourceCandidateAssetUrl ? <img alt={`${character.name}角色基准第${identity.version}版`} src={identity.sourceCandidateAssetUrl} /> : <div className="character-version-history__placeholder"><UserRoundCheck size={18} /></div>}
              <div>
                <span>第 {identity.version} 版</span>
                <strong>{isActive ? '当前角色基准' : replacedPending ? '已被新版本替代' : getStatusLabel(identity.status)}</strong>
                <small>{identity.lockedAt ? `曾由 ${identity.lockedBy ?? '创作者'} 设为基准` : replacedPending ? `当前以第 ${latestPendingIdentity?.version ?? identity.version} 版为审核目标` : '尚未完成基准确认'}</small>
              </div>
              {reviewable ? <Button disabled={busy !== null} onClick={() => {
                if (!viewingThisVersion) {
                  setIdentityReviewSelection((current) => ({ ...current, [character.id]: identity.id }))
                  window.setTimeout(() => jumpToCharacter(character.id, `.character-identity-dossier[data-identity-version-id="${identity.id}"]`), 0)
                } else if (readyToLock) {
                  void run(`lock-${character.id}`, async () => {
                    const result = await lockCharacterVisualIdentity(projectId, character.id, character.lockVersion, identity.id)
                    if (result.script_job) navigate(`/tasks?project=${projectId}&jobType=GENERATE_SCRIPT_PACKAGE`)
                  })
                } else {
                  jumpToCharacter(character.id, `.character-identity-dossier[data-identity-version-id="${identity.id}"]`)
                }
              }} size="md" variant={readyToLock ? 'primary' : 'secondary'}>{busy === `lock-${character.id}` ? <LoaderCircle className="spin" size={15} /> : <UserRoundCheck size={15} />}{readyToLock ? '审核并设为基准' : viewingThisVersion ? '查看生成进度' : '返回审核'}</Button> : replacedPending ? <Button onClick={() => {
                setIdentityReviewSelection((current) => ({ ...current, [character.id]: identity.id }))
                window.setTimeout(() => jumpToCharacter(character.id, `.character-identity-dossier[data-identity-version-id="${identity.id}"]`), 0)
              }} size="sm" variant="secondary"><Eye size={14} />{viewingThisVersion ? '正在查看' : '查看此版本'}</Button> : restorable ? <Button disabled={busy !== null} onClick={() => void run(`restore-${identity.id}`, () => restoreCharacterVisualIdentity(projectId, character.id, character.lockVersion, identity.id))} size="sm" variant="secondary">{busy === `restore-${identity.id}` ? <LoaderCircle className="spin" size={14} /> : <RotateCcw size={14} />}恢复此版本</Button> : null}
            </article>
          })}</div>
        </section> : null}

        {['TEXT_CHANGED', 'RE_REVIEW_REQUIRED'].includes(character.status) ? <div className="character-change-decision"><AlertTriangle size={18} /><div><strong>文字设定包含重大身份变化</strong><small>已锁定身份与既有镜头不会自动覆盖，请明确选择。</small></div><Button disabled={busy !== null} onClick={() => void run(`preserve-${character.id}`, () => applyCharacterVisualChange(projectId, character.id, character.lockVersion, 'PRESERVE_IDENTITY'))} variant="secondary">保留原身份</Button><Button disabled={busy !== null} onClick={() => void run(`regenerate-${character.id}`, () => applyCharacterVisualChange(projectId, character.id, character.lockVersion, 'REGENERATE'))}>重新生成</Button></div> : null}
      </article>
    })}</div>
    </div>

    {allLocked ? <section className="character-lock-bar">
      <div>
        <Check size={18} />
        <span>
          <strong>全部角色身份已锁定</strong>
          <small>后续剧本与分镜将引用这些固定版本。</small>
        </span>
      </div>
      <Link className="button button--primary button--md" to={`/projects/${projectId}/story`}>
        查看剧本进度
      </Link>
    </section> : null}

    <Modal
      className="modal--identity-view"
      description={identityViewAction?.mode === 'adjust' ? '以当前图片为参考生成调整版；新图成功前，当前图片会继续保留。' : '重新生成同一视角；新图成功前，当前图片会继续保留。'}
      footer={<>
        <Button disabled={busy !== null} onClick={() => { setIdentityViewAction(null); setIdentityViewNote('') }} variant="secondary">取消</Button>
        <Button disabled={busy !== null || (identityViewAction?.mode === 'adjust' && identityViewNote.trim().length < 4)} onClick={() => void submitIdentityViewAction()}>
          {busy?.startsWith('identity-view-') ? <LoaderCircle className="spin" size={15} /> : identityViewAction?.mode === 'adjust' ? <Sparkles size={15} /> : <RefreshCw size={15} />}
          {identityViewAction?.mode === 'adjust' ? '生成调整版' : '确认重新生成'}
        </Button>
      </>}
      onClose={() => { setIdentityViewAction(null); setIdentityViewNote('') }}
      open={identityViewAction !== null}
      title={identityViewAction ? `${identityViewAction.mode === 'adjust' ? '调整细节' : '重新生成'} · ${identityViewAction.characterName} ${identityViewAction.viewLabel}` : '调整角色身份图'}
    >
      {identityViewAction ? <div className="identity-view-dialog">
        <img alt={`${identityViewAction.characterName} ${identityViewAction.viewLabel} 当前版本`} src={identityViewAction.assetUrl} />
        <div>
          <div className="identity-view-dialog__summary"><span>{identityViewAction.viewLabel}</span><strong>{identityViewAction.mode === 'adjust' ? '只调整你指定的细节' : '生成一个身份一致的替代版本'}</strong><p>{identityViewAction.mode === 'adjust' ? '脸型、五官、年龄感、体型、发型和服装默认保持不变。' : '沿用已选角色身份与当前视角要求，画面细节会自然变化。'}</p></div>
          {identityViewAction.mode === 'adjust' ? <label className="identity-view-dialog__field" htmlFor="identity-view-refinement-note"><span>希望调整什么？</span><textarea autoFocus id="identity-view-refinement-note" maxLength={300} onChange={(event) => setIdentityViewNote(event.target.value)} placeholder="例如：保留五官和发型，让视线更坚定，减少笑意" rows={4} value={identityViewNote} /><small>{identityViewNote.trim().length}/300 · 至少输入 4 个字</small></label> : null}
          <div className="identity-view-dialog__notice" role="note"><ShieldCheck size={16} /><span><strong>当前图片不会立即被覆盖</strong><small>生成成功后才替换这个待确认视角；失败时仍保留现在的图片。</small></span></div>
        </div>
      </div> : null}
    </Modal>

    <Modal
      className="modal--identity-image-viewer"
      description={identityImageViewer ? `${identityImageViewer.characterName} · ${identityImageViewer.viewLabel} · 完整原图` : undefined}
      footer={<Button onClick={regenerateIdentityImageViewer}><RefreshCw size={15} />重新生成</Button>}
      onClose={closeIdentityImageViewer}
      open={identityImageViewer !== null}
      title={identityImageViewer ? `${identityImageViewer.viewLabel}原图` : '查看角色原图'}
    >
      {identityImageViewer ? <div className="identity-image-viewer">
        <div className="identity-image-viewer__toolbar">
          <span><Maximize2 size={15} /><strong>{identityImageZoom === 100 ? '适应画面' : `${identityImageZoom}%`}</strong></span>
          <div>
            <Button aria-label="缩小原图" disabled={identityImageZoom <= 100} onClick={() => setIdentityImageZoom((current) => Math.max(100, current - 25))} size="sm" variant="secondary"><ZoomOut size={15} /></Button>
            <Button disabled={identityImageZoom === 100} onClick={() => setIdentityImageZoom(100)} size="sm" variant="secondary">复位</Button>
            <Button aria-label="放大原图" disabled={identityImageZoom >= 200} onClick={() => setIdentityImageZoom((current) => Math.min(200, current + 25))} size="sm" variant="secondary"><ZoomIn size={15} /></Button>
          </div>
        </div>
        <div className="identity-image-viewer__viewport">
          <div className="identity-image-viewer__canvas" style={{ height: `${identityImageZoom}%`, width: `${identityImageZoom}%` }}>
            <img alt={`${identityImageViewer.characterName} ${identityImageViewer.viewLabel}完整原图`} draggable={false} src={identityImageViewer.assetUrl} />
          </div>
        </div>
        <small>使用放大按钮查看面部、服装和全身细节；放大后可滚动画布。</small>
      </div> : null}
    </Modal>
  </div>
}
